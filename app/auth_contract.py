from dataclasses import dataclass
from typing import Literal

from fastapi import Cookie, Depends, HTTPException
from sqlalchemy.orm import Session


from app.config import get_settings



from .database import get_db
from app.models import Account
from app.services.auth import get_session_account


@dataclass(frozen=True)
class CurrentUser:
    role: Literal["student", "teacher", "registrar"]
    subject_id: int


# def current_user(
#     x_role: str | None = Header(default=None),
#     x_subject_id: int | None = Header(default=None),
# ) -> CurrentUser:
#     """成员 3 接入正式会话认证前的开发适配器；生产环境拒绝使用请求头身份。"""
#     if get_settings().app_env != "development":
#         raise HTTPException(status_code=503, detail="正式认证模块尚未接入")
#     if x_role not in {"student", "teacher", "registrar"} or x_subject_id is None:
#         raise HTTPException(status_code=401, detail="开发环境需提供 X-Role 与 X-Subject-ID")
#     return CurrentUser(role=x_role, subject_id=x_subject_id)


# def require_student(user: CurrentUser) -> int:
#     if user.role != "student":
#         raise HTTPException(status_code=403, detail="仅学生可执行此操作")
#     return user.subject_id


# def require_registrar(user: CurrentUser) -> int:
#     if user.role != "registrar":
#         raise HTTPException(status_code=403, detail="仅教务人员可执行此操作")
#     return user.subject_id

### updated on 9.13 Accepting the actual request by sjy
def current_account(
    session_id: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> Account:
    """供正常业务入口使用：首次登录未改密的账号会被拒绝。"""

    if session_id is None:
        raise HTTPException(
            status_code=401,
            detail="未登录",
        )

    return get_session_account(
    db,
    session_id,
    allow_password_change=False,
)


def current_user(account: Account = Depends(current_account)) -> CurrentUser:
    return CurrentUser(
        role=account.role.value,
        subject_id=account.subject_id,
    )

def current_user_for_password_change(
    session_id: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> CurrentUser:
    if session_id is None:
        raise HTTPException(status_code=401, detail="未登录")

    account = get_session_account(
        db,
        session_id,
        allow_password_change=True,
    )

    return CurrentUser(
        role=account.role.value,
        subject_id=account.subject_id,
    )


def require_student(
    user: CurrentUser = Depends(current_user),
) -> int:

    if user.role != "student":
        raise HTTPException(
            status_code=403,
            detail="仅学生可访问",
        )

    return user.subject_id


def require_teacher(
    user: CurrentUser = Depends(current_user),
) -> int:

    if user.role != "teacher":
        raise HTTPException(
            status_code=403,
            detail="仅教师可访问",
        )

    return user.subject_id


def require_registrar(
    user: CurrentUser = Depends(current_user),
) -> int:

    if user.role != "registrar":
        raise HTTPException(
            status_code=403,
            detail="仅教务员可访问",
        )

    return user.subject_id
