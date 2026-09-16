"""账单发送与失败重试。

消费成员 1 在关闭选课时写入的 ``BillingJob``：金额与课表快照在关闭事务里已经固定，
本模块只负责发送、更新状态和按间隔重试，不重新计算学费，也不修改关闭结果。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import BillingJob, BillingStatus


RETRYABLE_STATUSES = (
    BillingStatus.PENDING,
    BillingStatus.SENDING,
    BillingStatus.FAILED,
)


class BillingSendError(RuntimeError):
    """发送失败；原始原因会写入任务的 last_error 供排查。"""


def _utcnow() -> datetime:
    return datetime.now(UTC)


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
    """默认发送实现：调用计费模拟服务，任何异常都收敛为 BillingSendError。"""
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
        raise BillingSendError(
            f"计费服务返回 {response.status_code}：{response.text[:200]}"
        )


def billing_job_view(job: BillingJob) -> dict:
    return {
        "job_id": job.id,
        "semester_id": job.semester_id,
        "student_id": job.student_id,
        "amount": str(job.amount),
        "status": job.status.value,
        "attempts": job.attempts,
        "next_attempt_at": job.next_attempt_at,
        "last_error": job.last_error,
        "created_at": job.created_at,
    }


def list_billing_jobs(db: Session, *, semester_id: int | None = None) -> list[BillingJob]:
    """账单任务列表，供教务查看发送状态。"""
    stmt = select(BillingJob).order_by(BillingJob.semester_id, BillingJob.student_id)
    if semester_id is not None:
        stmt = stmt.where(BillingJob.semester_id == semester_id)
    return list(db.scalars(stmt).all())


def dispatch_pending_jobs(
    db: Session,
    *,
    sender: Callable[[BillingJob], None] | None = None,
    now: datetime | None = None,
    limit: int | None = None,
) -> dict:
    """发送到期的待发送账单，并按固定间隔安排失败任务重试。

    任务在真正发送前先置为 sending 并顺延 next_attempt_at：进程在发送途中退出时，
    该任务会在间隔之后被重新拾取，因此重启不会丢掉账单。
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
    jobs = list(db.scalars(stmt).all())

    results: list[dict] = []
    sent = 0
    failed = 0
    for job in jobs:
        job.attempts += 1
        job.status = BillingStatus.SENDING
        job.next_attempt_at = moment + retry_delay
        db.commit()
        try:
            send(job)
        except (BillingSendError, httpx.HTTPError) as exc:
            job.status = BillingStatus.FAILED
            job.last_error = str(exc)[:500]
            failed += 1
        else:
            job.status = BillingStatus.SENT
            job.last_error = None
            sent += 1
        db.commit()
        results.append(
            {
                "job_id": job.id,
                "student_id": job.student_id,
                "status": job.status.value,
                "attempts": job.attempts,
                "error": job.last_error,
            }
        )

    return {
        "dispatched": len(jobs),
        "sent": sent,
        "failed": failed,
        "results": results,
    }
