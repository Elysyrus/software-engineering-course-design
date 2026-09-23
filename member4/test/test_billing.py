"""任务 9：账单发送与失败重试测试。"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest

from app.models import BillingJob, BillingStatus
from app.services.billing import (
    BillingSendError,
    build_payload,
    dispatch_pending_jobs,
    http_sender,
    list_billing_jobs,
)


SNAPSHOT = '[{"course_id": 1, "offering_id": 4, "section_number": "01"}]'


def _new_job(db, semester_id: int, student_id: int, amount: str = "120.00") -> BillingJob:
    job = BillingJob(
        semester_id=semester_id,
        student_id=student_id,
        amount=Decimal(amount),
        schedule_snapshot=SNAPSHOT,
    )
    db.add(job)
    db.commit()
    return job


@pytest.fixture
def billing_job(db, seed_basic) -> BillingJob:
    """单条待发送账单任务。"""
    return _new_job(db, seed_basic.closed_semester.id, seed_basic.students[0].id)


@pytest.fixture
def billed_students(db, seed_basic) -> list[BillingJob]:
    """三名学生各一条待发送账单任务，用于批量与筛选测试。"""
    return [
        _new_job(db, seed_basic.closed_semester.id, student.id)
        for student in seed_basic.students
    ]


class _RecordingSender:
    def __init__(self, *, error: str | None = None):
        self.payloads: list[dict] = []
        self.error = error

    def __call__(self, job: BillingJob) -> None:
        if self.error is not None:
            raise BillingSendError(self.error)
        self.payloads.append(build_payload(job))


def test_successful_dispatch_marks_job_sent(db, billing_job):
    sender = _RecordingSender()

    summary = dispatch_pending_jobs(db, sender=sender)

    assert summary["dispatched"] == 1
    assert summary["sent"] == 1
    assert summary["failed"] == 0
    assert billing_job.status == BillingStatus.SENT
    assert billing_job.attempts == 1
    assert billing_job.last_error is None
    assert sender.payloads == [
        {
            "job_id": billing_job.id,
            "semester_id": billing_job.semester_id,
            "student_id": billing_job.student_id,
            "amount": "120.00",
            "schedule_snapshot": SNAPSHOT,
        }
    ]


def test_failure_keeps_job_and_schedules_retry(db, billing_job):
    sender = _RecordingSender(error="计费服务不可用：连接超时")
    moment = datetime.now(UTC)

    summary = dispatch_pending_jobs(db, sender=sender, now=moment)

    assert summary["failed"] == 1
    assert billing_job.status == BillingStatus.FAILED
    assert billing_job.attempts == 1
    assert "计费服务不可用" in billing_job.last_error
    expected = (moment + timedelta(seconds=30)).replace(tzinfo=None)
    assert billing_job.next_attempt_at.replace(tzinfo=None) == expected
    # 金额与快照保持关闭时的固定值，失败不改动业务结果
    assert billing_job.amount == Decimal("120.00")
    assert billing_job.schedule_snapshot == SNAPSHOT


def test_job_is_not_retried_before_the_interval(db, billing_job):
    moment = datetime.now(UTC)
    dispatch_pending_jobs(
        db, sender=_RecordingSender(error="计费服务暂时不可用"), now=moment
    )

    early = dispatch_pending_jobs(
        db, sender=_RecordingSender(), now=moment + timedelta(seconds=10)
    )

    assert early["dispatched"] == 0
    assert billing_job.attempts == 1


def test_job_retries_after_the_interval_and_succeeds(db, billing_job):
    moment = datetime.now(UTC)
    dispatch_pending_jobs(
        db, sender=_RecordingSender(error="计费服务暂时不可用"), now=moment
    )
    sender = _RecordingSender()

    summary = dispatch_pending_jobs(db, sender=sender, now=moment + timedelta(seconds=31))

    assert summary["dispatched"] == 1
    assert summary["sent"] == 1
    assert billing_job.status == BillingStatus.SENT
    assert billing_job.attempts == 2
    assert billing_job.last_error is None
    assert len(sender.payloads) == 1


def test_sent_job_is_never_sent_again(db, billing_job):
    dispatch_pending_jobs(db, sender=_RecordingSender())

    again = dispatch_pending_jobs(
        db, sender=_RecordingSender(), now=datetime.now(UTC) + timedelta(hours=1)
    )

    assert billing_job.status == BillingStatus.SENT
    assert again["dispatched"] == 0


def test_payload_is_not_recomputed_from_course_fee(db, seed_basic, billing_job):
    """学费后来被改动也不能影响已关闭账单：发送内容全部来自任务快照。"""
    sender = _RecordingSender()
    seed_basic.courses["algorithm"].fee = Decimal("999.00")
    db.commit()

    dispatch_pending_jobs(db, sender=sender)

    assert sender.payloads[0]["amount"] == "120.00"
    assert sender.payloads[0]["schedule_snapshot"] == SNAPSHOT


def test_abandoned_sending_job_is_picked_up_after_restart(db, billing_job):
    """模拟进程在发送途中退出：sending 状态的任务会在间隔后被重新拾取。"""
    moment = datetime.now(UTC) - timedelta(seconds=60)
    billing_job.status = BillingStatus.SENDING
    billing_job.next_attempt_at = moment
    db.commit()
    sender = _RecordingSender()

    summary = dispatch_pending_jobs(db, sender=sender, now=moment + timedelta(seconds=31))

    assert summary["dispatched"] == 1
    assert summary["sent"] == 1
    assert billing_job.status == BillingStatus.SENT


def test_limit_caps_batch_size(db, billed_students):
    summary = dispatch_pending_jobs(db, sender=_RecordingSender(), limit=2)

    assert summary["dispatched"] == 2
    statuses = [job.status for job in billed_students]
    assert statuses.count(BillingStatus.SENT) == 2
    assert statuses.count(BillingStatus.PENDING) == 1


def test_list_billing_jobs_filters_by_semester(db, seed_basic, billed_students):
    _new_job(db, seed_basic.semester.id, seed_basic.students[0].id, amount="80.00")

    closed_jobs = list_billing_jobs(db, semester_id=seed_basic.closed_semester.id)
    all_jobs = list_billing_jobs(db)

    assert len(closed_jobs) == 3
    assert len(all_jobs) == 4


def test_http_sender_posts_snapshot_payload(db, billing_job, monkeypatch):
    captured = {}

    def fake_post(url, json, timeout):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return httpx.Response(status_code=201, request=httpx.Request("POST", url))

    monkeypatch.setattr(httpx, "post", fake_post)

    http_sender(billing_job)

    assert captured["url"].endswith("/billing/charges")
    assert captured["json"]["amount"] == "120.00"
    assert captured["json"]["schedule_snapshot"] == SNAPSHOT
    assert captured["timeout"] > 0


def test_http_sender_reports_service_and_connection_errors(db, billing_job, monkeypatch):
    """计费服务返回错误码或连不上时，都要收敛成 BillingSendError 交给重试逻辑。"""

    def service_error(url, json, timeout):
        return httpx.Response(
            status_code=503, text="服务不可用", request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(httpx, "post", service_error)
    with pytest.raises(BillingSendError) as error:
        http_sender(billing_job)
    assert "503" in str(error.value)

    def connection_error(url, json, timeout):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(httpx, "post", connection_error)
    with pytest.raises(BillingSendError) as connect_error:
        http_sender(billing_job)
    assert "不可用" in str(connect_error.value)
