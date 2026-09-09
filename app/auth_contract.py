from dataclasses import dataclass
from typing import Literal

from fastapi import Header, HTTPException

from app.config import get_settings


@dataclass(frozen=True)
class CurrentUser:
    role: Literal["student", "teacher", "registrar"]
    subject_id: int


def current_user(
    x_role: str | None = Header(default=None),
    x_subject_id: int | None = Header(default=None),
) -> CurrentUser:
    """成员 3 接入正式会话认证前的开发适配器；生产环境拒绝使用请求头身份。"""
    if get_settings().app_env != "development":
        raise HTTPException(status_code=503, detail="正式认证模块尚未接入")
    if x_role not in {"student", "teacher", "registrar"} or x_subject_id is None:
        raise HTTPException(status_code=401, detail="开发环境需提供 X-Role 与 X-Subject-ID")
    return CurrentUser(role=x_role, subject_id=x_subject_id)


def require_student(user: CurrentUser) -> int:
    if user.role != "student":
        raise HTTPException(status_code=403, detail="仅学生可执行此操作")
    return user.subject_id


def require_registrar(user: CurrentUser) -> int:
    if user.role != "registrar":
        raise HTTPException(status_code=403, detail="仅教务人员可执行此操作")
    return user.subject_id

