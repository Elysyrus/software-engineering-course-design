"""成员 4 账单状态与发送接口（教务专用）。"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth_contract import require_registrar
from app.database import get_db
from app.routers.deps import require_csrf_token
from app.services import billing


router = APIRouter(prefix="/api/v1/registrar/billing", tags=["成员4-计费"])


@router.get("/jobs")
def list_billing_jobs(
    semester_id: int | None = Query(default=None, ge=1),
    _registrar_id: int = Depends(require_registrar),
    db: Session = Depends(get_db),
):
    """账单任务与发送状态列表。"""
    jobs = billing.list_billing_jobs(db, semester_id=semester_id)
    return {"jobs": [billing.billing_job_view(job) for job in jobs]}


@router.post("/dispatch", dependencies=[Depends(require_csrf_token)])
def dispatch_billing_jobs(
    limit: int | None = Query(default=None, ge=1, le=500),
    _registrar_id: int = Depends(require_registrar),
    db: Session = Depends(get_db),
):
    """立即派发到期的待发送账单；失败任务会按配置间隔自动重试。"""
    return billing.dispatch_pending_jobs(db, limit=limit)
