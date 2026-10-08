"""账单发送与失败重试。

消费成员 1 在关闭选课时写入的 ``BillingJob``：金额与课表快照在关闭事务里已经固定，
本模块只负责发送、更新状态和按间隔重试，不重新计算学费，也不修改关闭结果。

失败分两类处理：

- **可重试**：连接失败、超时、5xx、429 等外部系统暂时不可用，按
  ``billing_retry_seconds`` 间隔持续重试；``run_retry_loop`` 让它在进程中自动发生，
  重启后仍会继续处理。
- **不可重试**：计费端以 4xx 明确拒绝账单本身（例如同一学期同一学生但内容不一致的
  409 冲突），重发同一份内容永远不会成功，因此把 ``next_attempt_at`` 推到
  ``ABANDONED_AT`` 停止重试并保留 ``last_error`` 供排查；修正后可用 ``requeue_job``
  人工重新排队。
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Event

import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.errors import BusinessError
from app.models import (
    BillingJob,
    BillingStatus,
    Enrollment,
    Offering,
    OfferingStatus,
    Semester,
    Student,
)

RETRYABLE_STATUSES = (
    BillingStatus.PENDING,
    BillingStatus.SENDING,
    BillingStatus.FAILED,
)

# 不可重试的任务把下次尝试时间推到远未来，调度器不再拾取
ABANDONED_AT = datetime(9999, 1, 1, tzinfo=UTC)
ABANDONED_MARK = "不可重试"

# 这些状态码代表外部系统暂时不可用，稍后重试有意义
HTTP_RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})


class BillingSendError(RuntimeError):
    """发送失败；``retryable`` 决定任务进入重试队列还是停止重试。"""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _as_aware(value: datetime | None) -> datetime | None:
    """SQLite 读回的 datetime 不带时区，统一按 UTC 处理后才能比较。"""
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def is_abandoned(job: BillingJob) -> bool:
    """任务是否已被判定为不可重试（下次尝试时间被推到远未来）。"""
    moment = _as_aware(job.next_attempt_at)
    return moment is not None and moment >= ABANDONED_AT


def build_payload(job: BillingJob) -> dict:
    """发送内容只取自关闭时固定的任务快照，不重新查询课程或学费。"""
    return {
        "job_id": job.id,
        "semester_id": job.semester_id,
        "student_id": job.student_id,
        "amount": str(job.amount),
        "schedule_snapshot": job.schedule_snapshot,
    }


def http_sender(job: BillingJob) -> None:
    """默认发送实现：调用计费服务，把结果收敛为可重试 / 不可重试两类错误。"""
    settings = get_settings()
    try:
        response = httpx.post(
            f"{settings.billing_service_url}/billing/charges",
            json=build_payload(job),
            timeout=settings.billing_request_timeout_seconds,
        )
    except httpx.HTTPError as exc:
        raise BillingSendError(f"计费服务不可用：{exc}") from exc
    if response.status_code >= 400:
        preview = response.text[:200]
        if response.status_code in HTTP_RETRYABLE_STATUSES:
            raise BillingSendError(f"计费服务返回 {response.status_code}：{preview}")
        # 4xx 是计费端对账单内容的拒绝，重发同一份内容不会成功
        raise BillingSendError(
            f"计费服务拒绝账单 {response.status_code}：{preview}", retryable=False
        )


def _course_count(job: BillingJob) -> int:
    """账单快照中的班次数量；只解析快照，不重新查询数据库。"""
    try:
        snapshot = json.loads(job.schedule_snapshot or "[]")
    except (TypeError, ValueError):
        return 0
    return len(snapshot) if isinstance(snapshot, list) else 0


def billing_job_view(job: BillingJob) -> dict:
    return {
        "job_id": job.id,
        "semester_id": job.semester_id,
        "student_id": job.student_id,
        "amount": str(job.amount),
        "course_count": _course_count(job),
        "status": job.status.value,
        "retryable": not is_abandoned(job),
        "attempts": job.attempts,
        "next_attempt_at": job.next_attempt_at,
        "last_error": job.last_error,
        "created_at": job.created_at,
    }


def list_billing_jobs(
    db: Session, *, semester_id: int | None = None
) -> list[BillingJob]:
    """账单任务列表，供教务查看发送状态。"""
    stmt = select(BillingJob).order_by(BillingJob.semester_id, BillingJob.student_id)
    if semester_id is not None:
        stmt = stmt.where(BillingJob.semester_id == semester_id)
    return list(db.scalars(stmt).all())


PAGE_BILL_STATUSES = {
    BillingStatus.SENT: "SENT",
    BillingStatus.FAILED: "FAILED",
}


def billing_summary(db: Session, *, semester_id: int | None = None) -> dict:
    """关闭结果与账单发送状态汇总。

    字段与"关闭结果与账单状态"页面保持一致，页面把数据源换成这个接口即可：
    ``bills`` 每行都有 ``bill_id`` / ``student_number`` / ``student_name`` /
    ``enrolled_count`` / ``amount``（数值）/ ``status``（``SENT``/``FAILED``/``PENDING``）。
    学费与班次数量分别来自关闭时的任务快照，不重新计算。
    """
    jobs = list_billing_jobs(db, semester_id=semester_id)
    resolved = semester_id
    if resolved is None and jobs:
        resolved = jobs[0].semester_id

    student_ids = {job.student_id for job in jobs}
    students = (
        {
            student.id: student
            for student in db.scalars(
                select(Student).where(Student.id.in_(student_ids))
            ).all()
        }
        if student_ids
        else {}
    )

    bills = []
    for job in jobs:
        student = students.get(job.student_id)
        bills.append(
            {
                "bill_id": job.id,
                "student_id": job.student_id,
                "student_number": student.student_number if student else None,
                "student_name": student.name if student else None,
                "enrolled_count": _course_count(job),
                "amount": float(job.amount),
                "status": PAGE_BILL_STATUSES.get(job.status, "PENDING"),
                "sent_at": job.sent_at.isoformat() if job.sent_at else None,
                "attempts": job.attempts,
                "last_error": job.last_error,
            }
        )

    sent = sum(1 for job in jobs if job.status is BillingStatus.SENT)
    failed = sum(1 for job in jobs if job.status is BillingStatus.FAILED)
    if jobs and sent == len(jobs):
        billing_status = "ALL_SENT"
    elif failed:
        billing_status = "PARTIAL_FAIL"
    else:
        billing_status = "PENDING"

    semester = db.get(Semester, resolved) if resolved is not None else None
    opened = cancelled = enrolled = 0
    if resolved is not None:
        status_counts = {
            status: count
            for status, count in db.execute(
                select(Offering.status, func.count(Offering.id))
                .where(Offering.semester_id == resolved)
                .group_by(Offering.status)
            ).all()
        }
        opened = int(status_counts.get(OfferingStatus.OPEN, 0)) + int(
            status_counts.get(OfferingStatus.CLOSED, 0)
        )
        cancelled = int(status_counts.get(OfferingStatus.CANCELLED, 0))
        enrolled = int(
            db.scalar(
                select(func.count(Enrollment.id))
                .join(Offering, Offering.id == Enrollment.offering_id)
                .where(Offering.semester_id == resolved)
            )
            or 0
        )

    return {
        "semester_id": resolved,
        "semester_code": semester.code if semester is not None else None,
        "billing_status": billing_status,
        "opened_sections_count": opened,
        "cancelled_sections_count": cancelled,
        "total_enrolled_count": enrolled,
        "total_amount": float(sum((job.amount for job in jobs), Decimal("0.00"))),
        "jobs_total": len(jobs),
        "jobs_sent": sent,
        "jobs_failed": failed,
        "jobs_pending": len(jobs) - sent - failed,
        "bills": bills,
    }


def requeue_job(db: Session, *, job_id: int, now: datetime | None = None) -> dict:
    """把停止重试或待发送的任务重新排队，供人工恢复。"""
    job = db.get(BillingJob, job_id)
    if job is None:
        raise BusinessError("账单任务不存在", 404)
    if job.status is BillingStatus.SENT:
        raise BusinessError("账单已发送成功，不能重新排队")
    job.status = BillingStatus.PENDING
    job.next_attempt_at = now or _utcnow()
    db.commit()
    return {"message": "账单已重新排队", "job": billing_job_view(job)}


def dispatch_pending_jobs(
    db: Session,
    *,
    sender: Callable[[BillingJob], None] | None = None,
    now: datetime | None = None,
    limit: int | None = None,
) -> dict:
    """派发到期的待发送账单，并按固定间隔安排失败任务重试。

    分两阶段执行：

    1. **认领**：在一个事务里取出到期任务，递增尝试次数、置为 ``sending`` 并把
       ``next_attempt_at`` 顺延一个间隔后提交。并发的第二个调度器不会再挑到同一批任务；
       进程若在发送途中退出，任务会在间隔之后被重新拾取，因此重启不会丢账单。
    2. **发送**：逐个重新读取任务并调用发送器。成功置 ``sent``；可重试失败置
       ``failed`` 等待下一轮；计费端明确拒绝的任务置 ``failed`` 并停止重试。

    单个任务失败不会中断整批发送。
    """
    send = sender or http_sender
    moment = now or _utcnow()
    retry_delay = timedelta(seconds=get_settings().billing_retry_seconds)

    stmt = (
        select(BillingJob)
        .where(
            BillingJob.status.in_(RETRYABLE_STATUSES),
            BillingJob.next_attempt_at <= moment,
        )
        .order_by(BillingJob.next_attempt_at, BillingJob.id)
        .with_for_update(skip_locked=True)
    )
    if limit is not None:
        stmt = stmt.limit(limit)
    claimed = list(db.scalars(stmt).all())
    if not claimed:
        return {
            "dispatched": 0,
            "sent": 0,
            "failed": 0,
            "abandoned": 0,
            "retrying": 0,
            "results": [],
        }

    claimed_ids: list[int] = []
    for job in claimed:
        job.attempts += 1
        job.status = BillingStatus.SENDING
        job.next_attempt_at = moment + retry_delay
        claimed_ids.append(job.id)
    db.commit()

    results: list[dict] = []
    sent = failed = abandoned = 0
    for job_id in claimed_ids:
        job = db.get(BillingJob, job_id)
        if job is None:
            continue
        retryable = True
        try:
            send(job)
        except Exception as exc:  # 单个任务失败不能中断整批
            retryable = bool(getattr(exc, "retryable", True))
            job.status = BillingStatus.FAILED
            job.last_error = str(exc)[:500]
            failed += 1
            if retryable:
                job.next_attempt_at = moment + retry_delay
            else:
                job.next_attempt_at = ABANDONED_AT
                abandoned += 1
                if not job.last_error.startswith(f"[{ABANDONED_MARK}]"):
                    job.last_error = f"[{ABANDONED_MARK}] {job.last_error}"
        else:
            job.status = BillingStatus.SENT
            job.sent_at = _utcnow()
            job.last_error = None
            sent += 1
        db.commit()
        results.append(
            {
                "job_id": job.id,
                "student_id": job.student_id,
                "status": job.status.value,
                "attempts": job.attempts,
                "retryable": retryable,
                "error": job.last_error,
            }
        )

    return {
        "dispatched": len(claimed_ids),
        "sent": sent,
        "failed": failed,
        "abandoned": abandoned,
        "retrying": failed - abandoned,
        "results": results,
    }


def _session_factory() -> Callable[[], Session]:
    from app.database import SessionLocal

    return SessionLocal


def run_retry_loop(
    *,
    interval_seconds: float = 5.0,
    iterations: int | None = None,
    stop_event: Event | None = None,
    db_factory: Callable[[], Session] | None = None,
    sender: Callable[[BillingJob], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    on_cycle: Callable[[dict], None] | None = None,
) -> dict:
    """后台重试循环：按固定间隔派发到期任务。

    设置 ``stop_event`` 后循环在事件置位时退出；``iterations`` 用于单次执行或测试。
    """
    factory = db_factory or _session_factory()
    cycles = sent = failed = abandoned = 0
    while True:
        if stop_event is not None and stop_event.is_set():
            break
        with factory() as db:
            summary = dispatch_pending_jobs(db, sender=sender)
        cycles += 1
        sent += summary["sent"]
        failed += summary["failed"]
        abandoned += summary["abandoned"]
        if on_cycle is not None:
            on_cycle(summary)
        if iterations is not None and cycles >= iterations:
            break
        if stop_event is not None:
            if stop_event.wait(interval_seconds):
                break
        else:
            sleep(interval_seconds)
    return {"cycles": cycles, "sent": sent, "failed": failed, "abandoned": abandoned}
