from pathlib import Path
from typing import List, Literal, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from datetime import date
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.auth_contract import (
    CurrentUser,
    current_user,
    current_user_for_password_change,
    require_registrar,
    require_student,
    require_teacher,
)
from app.database import get_db
from app.models import Account, AccountRole, ServerSession, Student, Teacher
from app.services.personnel import (
    create_student,
    create_teacher,
    delete_user_for_registrar,
    get_user_for_registrar,
    list_users_for_registrar,
    reset_account_password,
    set_account_status,
    update_user_for_registrar,
)
from app.services.auth import (
    authenticate_user,
    change_password,
    create_session,
    get_session_account,
    logout,
    validate_csrf,
)

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=BASE_DIR / "web" / "templates")

web_router = APIRouter(include_in_schema=False)

# ========================================================
# 1. 页面直达路由
# ========================================================


@web_router.get("/")
async def root():
    return RedirectResponse(url="/login")


@web_router.get("/login")
async def page_login(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="auth/login.html",
        context={"request": request, "user": None},
    )


def _template_user(db: Session, user: CurrentUser) -> dict[str, str | int]:
    """把可信身份转换为模板显示所需的最小公开资料。"""
    if user.role == "student":
        student = db.get(Student, user.subject_id)
        return {
            "username": student.student_number,
            "full_name": student.name,
            "role": "student",
        }
    if user.role == "teacher":
        teacher = db.get(Teacher, user.subject_id)
        return {
            "username": teacher.teacher_number,
            "full_name": teacher.name,
            "role": "teacher",
        }
    return {"username": "registrar", "full_name": "教务管理员", "role": "registrar"}


@web_router.get("/change-password")
async def page_change_password(
    request: Request,
    user: CurrentUser = Depends(current_user_for_password_change),
    db: Session = Depends(get_db),
):
    return templates.TemplateResponse(
        request=request,
        name="auth/change_password.html",
        context={"request": request, "user": _template_user(db, user)},
    )


@web_router.get("/student/courses")
async def page_student_courses(
    request: Request,
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    require_student(user)
    return templates.TemplateResponse(
        request=request,
        name="student/courses.html",
        context={
            "request": request,
            "user": _template_user(db, user),
            "active_nav": "courses",
        },
    )


@web_router.get("/student/draft")
async def page_student_draft(
    request: Request,
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    require_student(user)
    return templates.TemplateResponse(
        request=request,
        name="student/draft.html",
        context={
            "request": request,
            "user": _template_user(db, user),
            "active_nav": "draft",
        },
    )


@web_router.get("/student/results")
async def page_student_results(
    request: Request,
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    require_student(user)
    return templates.TemplateResponse(
        request=request,
        name="student/results.html",
        context={
            "request": request,
            "user": _template_user(db, user),
            "active_nav": "results",
        },
    )


@web_router.get("/student/grades")
async def page_student_grades(
    request: Request,
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    require_student(user)
    return templates.TemplateResponse(
        request=request,
        name="student/grades.html",
        context={
            "request": request,
            "user": _template_user(db, user),
            "active_nav": "grades",
        },
    )


@web_router.get("/teacher/claim")
async def page_teacher_claim(
    request: Request,
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    require_teacher(user)
    return templates.TemplateResponse(
        request=request,
        name="teacher/claim.html",
        context={
            "request": request,
            "user": _template_user(db, user),
            "active_nav": "claim",
        },
    )


@web_router.get("/teacher/roster")
async def page_teacher_roster(
    request: Request,
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    require_teacher(user)
    return templates.TemplateResponse(
        request=request,
        name="teacher/roster.html",
        context={
            "request": request,
            "user": _template_user(db, user),
            "active_nav": "roster",
        },
    )


@web_router.get("/teacher/grades")
async def page_teacher_grades(
    request: Request,
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    require_teacher(user)
    return templates.TemplateResponse(
        request=request,
        name="teacher/grades.html",
        context={
            "request": request,
            "user": _template_user(db, user),
            "active_nav": "grades",
        },
    )


@web_router.get("/admin/users")
async def page_admin_users(
    request: Request,
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    require_registrar(user)
    return templates.TemplateResponse(
        request=request,
        name="admin/users.html",
        context={
            "request": request,
            "user": _template_user(db, user),
            "active_nav": "users",
        },
    )


@web_router.get("/admin/close-registration")
async def page_admin_close(
    request: Request,
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    require_registrar(user)
    return templates.TemplateResponse(
        request=request,
        name="admin/close_registration.html",
        context={
            "request": request,
            "user": _template_user(db, user),
            "active_nav": "close",
        },
    )


@web_router.get("/admin/closing-status")
async def page_admin_status(
    request: Request,
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    require_registrar(user)
    return templates.TemplateResponse(
        request=request,
        name="admin/closing_status.html",
        context={
            "request": request,
            "user": _template_user(db, user),
            "active_nav": "status",
        },
    )


# ========================================================
# 2. 正式认证与人员管理接口
# ========================================================


class LoginReq(BaseModel):
    username: str
    password: str


class ChangePwdReq(BaseModel):
    old_password: str
    new_password: str


class PersonnelFields(BaseModel):
    birth_date: date | None = None
    social_security_number: str | None = Field(default=None, max_length=32)
    status: Literal["active", "on_leave", "graduated", "retired"] | None = None
    graduation_date: date | None = None

    @field_validator("birth_date")
    @classmethod
    def valid_birth(cls, value):
        if value is not None and value > date.today():
            raise ValueError("出生日期不能在未来")
        return value


class CreateUserReq(PersonnelFields):
    full_name: str = Field(min_length=1, max_length=100)
    role: Literal["student", "teacher"]
    department: str | None = Field(default=None, max_length=100)
    status: Literal["active", "on_leave", "graduated", "retired"] = "active"


class UpdateUserReq(PersonnelFields):
    full_name: str | None = Field(default=None, min_length=1, max_length=100)
    department: str | None = Field(default=None, max_length=100)


class AccountStatusReq(BaseModel):
    is_active: bool


def _set_auth_cookies(response: Response, session: ServerSession) -> None:
    """会话 ID 仅由服务器读取；CSRF 值供同源页面写请求提交。"""
    secure = get_settings().app_env != "development"
    response.set_cookie(
        "session_id", session.id, httponly=True, samesite="lax", secure=secure
    )
    response.set_cookie(
        "csrf_token", session.csrf_token, httponly=False, samesite="lax", secure=secure
    )


def _validate_request_csrf(db: Session, request: Request) -> None:
    session_id = request.cookies.get("session_id")
    session = db.get(ServerSession, session_id) if session_id is not None else None
    if session is None:
        raise HTTPException(status_code=401, detail="登录状态无效")
    validate_csrf(session, request.headers.get("X-CSRF-Token"))


def _personnel_http_error(error: ValueError) -> None:
    message = str(error)
    status_code = 404 if "不存在" in message else 422
    raise HTTPException(status_code=status_code, detail=message)


@web_router.post("/api/v1/auth/login")
async def api_login(req: LoginReq, db: Session = Depends(get_db)):
    account = authenticate_user(db, req.username, req.password)
    session = create_session(db, account)
    response = JSONResponse(
        {
            "role": account.role.value,
            "is_first_login": account.must_change_password,
        }
    )
    _set_auth_cookies(response, session)
    return response


@web_router.post("/api/v1/auth/change-password")
async def api_change_password(
    req: ChangePwdReq, request: Request, db: Session = Depends(get_db)
):
    session_id = request.cookies.get("session_id")
    if session_id is None:
        raise HTTPException(status_code=401, detail="未登录")
    account = get_session_account(db, session_id, allow_password_change=True)
    session = db.get(ServerSession, session_id)
    validate_csrf(session, request.headers.get("X-CSRF-Token"))
    try:
        change_password(db, account, req.old_password, req.new_password)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    response = JSONResponse({"message": "密码修改成功，请重新登录"})
    response.delete_cookie("session_id")
    response.delete_cookie("csrf_token")
    return response


@web_router.post("/api/v1/auth/logout")
async def api_logout(request: Request, db: Session = Depends(get_db)):
    session_id = request.cookies.get("session_id")
    if session_id is not None:
        # Logout remains available to users who must change their initial password.
        get_session_account(db, session_id, allow_password_change=True)
        session = db.get(ServerSession, session_id)
        validate_csrf(session, request.headers.get("X-CSRF-Token"))
        logout(db, session_id)
    response = JSONResponse({"message": "已退出登录"})
    response.delete_cookie("session_id")
    response.delete_cookie("csrf_token")
    return response


@web_router.get("/api/v1/admin/users")
async def api_get_users(
    _registrar_id: int = Depends(require_registrar),
    db: Session = Depends(get_db),
):
    return list_users_for_registrar(db)


### 教务创建学生或教师账号
@web_router.post("/api/v1/admin/users", status_code=201)
async def api_create_user(
    req: CreateUserReq,
    request: Request,
    _registrar_id: int = Depends(require_registrar),
    db: Session = Depends(get_db),
):
    """教务创建师生资料与账号；登录编号始终由后端分配。"""
    _validate_request_csrf(db, request)

    profile = req.model_dump(exclude={"full_name", "role", "department"})
    if req.graduation_date and req.birth_date and req.graduation_date < req.birth_date:
        raise HTTPException(status_code=422, detail="毕业日期不能早于出生日期")
    if req.role == "teacher" and req.graduation_date is not None:
        raise HTTPException(status_code=422, detail="教师不能设置毕业日期")
    if not req.full_name.strip():
        raise HTTPException(status_code=422, detail="姓名不能为空")
    if req.role == "student":
        student = create_student(db, req.full_name.strip(), "Initial123", **profile)
        account_id = db.scalar(
            select(Account.id).where(
                Account.role == AccountRole.STUDENT,
                Account.subject_id == student.id,
            )
        )
        return {
            "id": account_id,
            "login_number": student.student_number,
            "role": "student",
            "message": "学生账号创建成功",
        }

    if not req.department or not req.department.strip():
        raise HTTPException(status_code=422, detail="教师必须填写院系")
    teacher = create_teacher(
        db, req.full_name.strip(), req.department.strip(), "Initial123", **profile
    )
    account_id = db.scalar(
        select(Account.id).where(
            Account.role == AccountRole.TEACHER,
            Account.subject_id == teacher.id,
        )
    )
    return {
        "id": account_id,
        "login_number": teacher.teacher_number,
        "role": "teacher",
        "message": "教师账号创建成功",
    }


@web_router.get("/api/v1/admin/users/{account_id}")
async def api_get_user(
    account_id: int,
    _registrar_id: int = Depends(require_registrar),
    db: Session = Depends(get_db),
):
    try:
        return get_user_for_registrar(db, account_id)
    except ValueError as error:
        _personnel_http_error(error)


@web_router.patch("/api/v1/admin/users/{account_id}")
async def api_update_user(
    account_id: int,
    req: UpdateUserReq,
    request: Request,
    _registrar_id: int = Depends(require_registrar),
    db: Session = Depends(get_db),
):
    _validate_request_csrf(db, request)
    try:
        return update_user_for_registrar(
            db,
            account_id,
            full_name=req.full_name,
            department=req.department,
            **req.model_dump(exclude={"full_name", "department"}, exclude_unset=True),
        )
    except ValueError as error:
        _personnel_http_error(error)


@web_router.delete("/api/v1/admin/users/{account_id}")
async def api_delete_user(
    account_id: int,
    request: Request,
    _registrar_id: int = Depends(require_registrar),
    db: Session = Depends(get_db),
):
    _validate_request_csrf(db, request)
    try:
        action = delete_user_for_registrar(db, account_id)
    except ValueError as error:
        _personnel_http_error(error)
    return {
        "action": action,
        "message": "人员已删除" if action == "deleted" else "人员已停用",
    }


@web_router.post("/api/v1/admin/users/{account_id}/reset-password")
async def api_reset_password(
    account_id: int,
    request: Request,
    _registrar_id: int = Depends(require_registrar),
    db: Session = Depends(get_db),
):
    _validate_request_csrf(db, request)
    try:
        reset_account_password(db, account_id, "Initial123")
    except ValueError as error:
        _personnel_http_error(error)
    return {"message": "密码已重置，用户下次登录必须修改密码"}


@web_router.patch("/api/v1/admin/users/{account_id}/status")
async def api_set_user_status(
    account_id: int,
    req: AccountStatusReq,
    request: Request,
    _registrar_id: int = Depends(require_registrar),
    db: Session = Depends(get_db),
):
    _validate_request_csrf(db, request)
    try:
        return set_account_status(db, account_id, req.is_active)
    except ValueError as error:
        _personnel_http_error(error)
