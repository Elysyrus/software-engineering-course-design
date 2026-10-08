"""任务 9：账单发送与失败重试测试。"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Event

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from app.errors import BusinessError
from app.models import BillingJob, BillingStatus
from app.services import billing
from app.services.billing import (
    BillingSendError,
    build_payload,
    billing_summary,
    dispatch_pending_jobs,
    http_sender,
    is_abandoned,
    list_billing_jobs,
    requeue_job,
    run_retry_loop,
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
    def __init__(self, *, error: str | None = None, retryable: bool = True):
        self.payloads: list[dict] = []
        self.error = error
        self.retryable = retryable

    def __call__(self, job: BillingJob) -> None:
        if self.error is not None:
            raise BillingSendError(self.error, retryable=self.retryable)
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


@pytest.fixture
def db_factory(db):
    """与 app.database.SessionLocal 同构的会话工厂，供后台重试循环测试使用。"""
    return sessionmaker(bind=db.get_bind(), expire_on_commit=False)


def test_http_sender_marks_client_rejection_as_not_retryable(
    db, billing_job, monkeypatch
):
    """计费端 4xx 拒绝账单本身，重发同一内容不会成功，必须停止重试。"""

    def conflict(url, json, timeout):
        return httpx.Response(
            status_code=409, text="内容不一致", request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(httpx, "post", conflict)
    with pytest.raises(BillingSendError) as error:
        http_sender(billing_job)
    assert error.value.retryable is False
    assert "409" in str(error.value)

    def busy(url, json, timeout):
        return httpx.Response(
            status_code=503, text="服务不可用", request=httpx.Request("POST", url)
        )

    monkeypatch.setattr(httpx, "post", busy)
    with pytest.raises(BillingSendError) as retry_error:
        http_sender(billing_job)
    assert retry_error.value.retryable is True


def test_permanent_rejection_stops_retrying(db, billing_job):
    sender = _RecordingSender(error="计费服务拒绝账单 409：内容不一致", retryable=False)

    summary = dispatch_pending_jobs(db, sender=sender)

    assert summary["dispatched"] == 1
    assert summary["failed"] == 1
    assert summary["abandoned"] == 1
    assert summary["retrying"] == 0
    assert billing_job.status == BillingStatus.FAILED
    assert is_abandoned(billing_job) is True
    assert billing_job.last_error.startswith("[不可重试]")

    # 第二天也不会再重发，避免对同一份被拒绝的账单无限重试
    later = dispatch_pending_jobs(
        db, sender=_RecordingSender(), now=datetime.now(UTC) + timedelta(days=1)
    )
    assert later["dispatched"] == 0
    assert billing_job.attempts == 1


def test_job_is_claimed_before_sending(db, billing_job):
    """发送时任务已置为 sending 且重试时间已顺延，并发调度器不会再挑到同一条。"""
    seen: dict = {}

    def sender(job: BillingJob) -> None:
        fresh = db.get(BillingJob, job.id)
        seen["status"] = fresh.status
        seen["next_attempt_at"] = fresh.next_attempt_at
        seen["attempts"] = fresh.attempts

    moment = datetime.now(UTC)
    dispatch_pending_jobs(db, sender=sender, now=moment)

    assert seen["status"] is BillingStatus.SENDING
    assert seen["attempts"] == 1
    assert seen["next_attempt_at"].replace(tzinfo=None) == (
        moment + timedelta(seconds=30)
    ).replace(tzinfo=None)
    assert billing_job.status == BillingStatus.SENT


def test_unexpected_sender_error_does_not_abort_batch(db, billed_students):
    """发送器抛出未预期的异常时，后面几条账单仍然继续发送。"""
    calls: list[int] = []

    def sender(job: BillingJob) -> None:
        calls.append(job.id)
        if len(calls) == 1:
            raise RuntimeError("模拟发送器内部异常")

    summary = dispatch_pending_jobs(db, sender=sender)

    assert summary["dispatched"] == 3
    assert len(calls) == 3
    assert summary["sent"] == 2
    assert summary["failed"] == 1
    assert summary["retrying"] == 1


def test_dispatch_with_nothing_due_is_a_noop(db, billing_job):
    dispatch_pending_jobs(db, sender=_RecordingSender())

    again = dispatch_pending_jobs(db, sender=_RecordingSender())

    assert again == {
        "dispatched": 0,
        "sent": 0,
        "failed": 0,
        "abandoned": 0,
        "retrying": 0,
        "results": [],
    }


def test_retry_loop_dispatches_then_becomes_idle(db, db_factory, billing_job):
    cycles: list[dict] = []

    result = run_retry_loop(
        db_factory=db_factory,
        sender=_RecordingSender(),
        iterations=2,
        interval_seconds=0,
        on_cycle=cycles.append,
    )

    assert result == {"cycles": 2, "sent": 1, "failed": 0, "abandoned": 0}
    assert [cycle["dispatched"] for cycle in cycles] == [1, 0]
    # 循环用的是另一个会话，重新读一次才能看到它写回的状态
    db.refresh(billing_job)
    assert billing_job.status == BillingStatus.SENT


def test_retry_loop_stops_when_event_is_set(db, db_factory, billing_job):
    stop_event = Event()
    stop_event.set()

    result = run_retry_loop(
        db_factory=db_factory,
        sender=_RecordingSender(),
        stop_event=stop_event,
        interval_seconds=0,
    )

    assert result["cycles"] == 0
    assert billing_job.status == BillingStatus.PENDING


def test_requeue_rearms_abandoned_job_for_another_attempt(db, billing_job):
    dispatch_pending_jobs(db, sender=_RecordingSender(error="拒绝", retryable=False))
    assert is_abandoned(billing_job) is True

    requeue_job(db, job_id=billing_job.id)

    assert billing_job.status == BillingStatus.PENDING
    assert is_abandoned(billing_job) is False
    assert dispatch_pending_jobs(db, sender=_RecordingSender())["sent"] == 1


def test_requeue_rejects_missing_or_sent_job(db, billing_job):
    with pytest.raises(BusinessError):
        requeue_job(db, job_id=999)

    dispatch_pending_jobs(db, sender=_RecordingSender())
    with pytest.raises(BusinessError):
        requeue_job(db, job_id=billing_job.id)


def test_billing_summary_carries_fields_the_status_page_needs(
    db, seed_basic, billed_students
):
    dispatch_pending_jobs(db, sender=_RecordingSender(), limit=2)

    summary = billing_summary(db, semester_id=seed_basic.closed_semester.id)

    assert summary["semester_code"] == "2025-FALL"
    assert summary["billing_status"] == "PENDING"
    assert summary["jobs_total"] == 3
    assert summary["jobs_sent"] == 2
    assert summary["jobs_pending"] == 1
    assert summary["total_amount"] == pytest.approx(360.0)
    bill = summary["bills"][0]
    assert bill["bill_id"] == billed_students[0].id
    assert bill["student_id"] == seed_basic.students[0].id
    assert bill["student_number"] == "20260001"
    assert bill["student_name"] == "学生1"
    assert bill["enrolled_count"] == 1
    assert isinstance(bill["amount"], float)
    assert bill["status"] == "SENT"


def test_billing_summary_reports_all_sent(db, seed_basic, billed_students):
    dispatch_pending_jobs(db, sender=_RecordingSender())

    summary = billing_summary(db, semester_id=seed_basic.closed_semester.id)

    assert summary["billing_status"] == "ALL_SENT"
    assert summary["jobs_failed"] == 0
    assert {bill["status"] for bill in summary["bills"]} == {"SENT"}


def test_billing_summary_reports_partial_failure(db, seed_basic, billed_students):
    calls: list[int] = []

    def sender(job: BillingJob) -> None:
        calls.append(job.id)
        if len(calls) == 1:
            raise BillingSendError("计费服务拒绝账单", retryable=False)

    dispatch_pending_jobs(db, sender=sender)
    summary = billing_summary(db, semester_id=seed_basic.closed_semester.id)
    assert summary["billing_status"] == "PARTIAL_FAIL"
    assert summary["jobs_failed"] == 1
    assert {bill["status"] for bill in summary["bills"]} == {"SENT", "FAILED"}
