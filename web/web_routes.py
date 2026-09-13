from pathlib import Path
from typing import List, Literal, Optional
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
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
    return templates.TemplateResponse("auth/login.html", {"request": request, "user": None})


def _template_user(db: Session, user: CurrentUser) -> dict[str, str | int]:
    """把可信身份转换为模板显示所需的最小公开资料。"""
    if user.role == "student":
        student = db.get(Student, user.subject_id)
        return {"username": student.student_number, "full_name": student.name, "role": "student"}
    if user.role == "teacher":
        teacher = db.get(Teacher, user.subject_id)
        return {"username": teacher.teacher_number, "full_name": teacher.name, "role": "teacher"}
    return {"username": "registrar", "full_name": "教务管理员", "role": "registrar"}


@web_router.get("/change-password")
async def page_change_password(
    request: Request,
    user: CurrentUser = Depends(current_user_for_password_change),
    db: Session = Depends(get_db),
):
    return templates.TemplateResponse("auth/change_password.html", {"request": request, "user": _template_user(db, user)})

@web_router.get("/student/courses")
async def page_student_courses(request: Request, user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    require_student(user)
    return templates.TemplateResponse("student/courses.html", {"request": request, "user": _template_user(db, user), "active_nav": "courses"})

@web_router.get("/student/draft")
async def page_student_draft(request: Request, user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    require_student(user)
    return templates.TemplateResponse("student/draft.html", {"request": request, "user": _template_user(db, user), "active_nav": "draft"})

@web_router.get("/student/results")
async def page_student_results(request: Request, user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    require_student(user)
    return templates.TemplateResponse("student/results.html", {"request": request, "user": _template_user(db, user), "active_nav": "results"})

@web_router.get("/student/grades")
async def page_student_grades(request: Request, user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    require_student(user)
    return templates.TemplateResponse("student/grades.html", {"request": request, "user": _template_user(db, user), "active_nav": "grades"})

@web_router.get("/teacher/claim")
async def page_teacher_claim(request: Request, user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    require_teacher(user)
    return templates.TemplateResponse("teacher/claim.html", {"request": request, "user": _template_user(db, user), "active_nav": "claim"})

@web_router.get("/teacher/roster")
async def page_teacher_roster(request: Request, user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    require_teacher(user)
    return templates.TemplateResponse("teacher/roster.html", {"request": request, "user": _template_user(db, user), "active_nav": "roster"})

@web_router.get("/teacher/grades")
async def page_teacher_grades(request: Request, user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    require_teacher(user)
    return templates.TemplateResponse("teacher/grades.html", {"request": request, "user": _template_user(db, user), "active_nav": "grades"})

@web_router.get("/admin/users")
async def page_admin_users(request: Request, user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    require_registrar(user)
    return templates.TemplateResponse("admin/users.html", {"request": request, "user": _template_user(db, user), "active_nav": "users"})

@web_router.get("/admin/close-registration")
async def page_admin_close(request: Request, user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    require_registrar(user)
    return templates.TemplateResponse("admin/close_registration.html", {"request": request, "user": _template_user(db, user), "active_nav": "close"})

@web_router.get("/admin/closing-status")
async def page_admin_status(request: Request, user: CurrentUser = Depends(current_user), db: Session = Depends(get_db)):
    require_registrar(user)
    return templates.TemplateResponse("admin/closing_status.html", {"request": request, "user": _template_user(db, user), "active_nav": "status"})


# ========================================================
# 2. 配套测试接口 (响应前端页面内发起的 fetch 请求)
# ========================================================

class LoginReq(BaseModel):
    username: str
    password: str

class ChangePwdReq(BaseModel):
    old_password: str
    new_password: str

class DraftItemReq(BaseModel):
    section_id: int

class SubmitRegistrationReq(BaseModel):
    version: int
    ordered_section_ids: List[int]

class GradeItem(BaseModel):
    student_id: int
    score: float

class SubmitGradesReq(BaseModel):
    grades: List[GradeItem]

class CreateUserReq(BaseModel):
    full_name: str = Field(min_length=1, max_length=100)
    role: Literal["student", "teacher"]
    department: str | None = Field(default=None, max_length=100)


class UpdateUserReq(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=100)
    department: str | None = Field(default=None, max_length=100)


class AccountStatusReq(BaseModel):
    is_active: bool

class CloseReq(BaseModel):

    confirmation: str

MOCK_STATE = {
    "draft_version": 1,
    "draft_items": [
        {"section_id": 101, "course_code": "CS101", "course_name": "面向对象软件工程", "teacher_name": "张教授", "schedule_time": "周一 08:00-09:40"},
        {"section_id": 201, "course_code": "CS202", "course_name": "操作系统原理", "teacher_name": "李副教授", "schedule_time": "周二 10:00-11:40"},
        {"section_id": 301, "course_code": "CS303", "course_name": "分布式数据库系统", "teacher_name": "王教授", "schedule_time": "周三 13:30-15:10"},
        {"section_id": 401, "course_code": "CS404", "course_name": "编译原理与技术", "teacher_name": "赵教授", "schedule_time": "周四 15:30-17:10"},
    ],
    "results": []
}

def _set_auth_cookies(response: Response, session: ServerSession) -> None:
    """会话 ID 仅由服务器读取；CSRF 值供同源页面写请求提交。"""
    secure = get_settings().app_env != "development"
    response.set_cookie("session_id", session.id, httponly=True, samesite="lax", secure=secure)
    response.set_cookie("csrf_token", session.csrf_token, httponly=False, samesite="lax", secure=secure)


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
async def api_change_password(req: ChangePwdReq, request: Request, db: Session = Depends(get_db)):
    session_id = request.cookies.get("session_id")
    if session_id is None:
        raise HTTPException(status_code=401, detail="未登录")
    account = get_session_account(db, session_id, allow_password_change=True)
    session = db.get(ServerSession, session_id)
    validate_csrf(session, request.headers.get("X-CSRF-Token"))
    change_password(db, account, req.old_password, req.new_password)
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

@web_router.get("/api/v1/student/available-sections")
async def api_available_sections():
    return [
        {"id": 101, "course_code": "CS101", "course_name": "面向对象软件工程", "credits": 4.0, "teacher_name": "张教授", "schedule_time": "周一 08:00-09:40", "classroom": "正新楼 301", "capacity": 30, "enrolled_count": 28},
        {"id": 201, "course_code": "CS202", "course_name": "操作系统原理", "credits": 3.5, "teacher_name": "李副教授", "schedule_time": "周二 10:00-11:40", "classroom": "王湘浩楼 B108", "capacity": 35, "enrolled_count": 35},
        {"id": 301, "course_code": "CS303", "course_name": "分布式数据库系统", "credits": 3.0, "teacher_name": "王教授", "schedule_time": "周三 13:30-15:10", "classroom": "正新楼 202", "capacity": 40, "enrolled_count": 16},
        {"id": 401, "course_code": "CS404", "course_name": "编译原理与技术", "credits": 3.5, "teacher_name": "赵教授", "schedule_time": "周四 15:30-17:10", "classroom": "正新楼 102", "capacity": 30, "enrolled_count": 12},
        {"id": 501, "course_code": "CS505", "course_name": "计算机系统结构", "credits": 3.0, "teacher_name": "孙老师", "schedule_time": "周五 08:00-09:40", "classroom": "正新楼 405", "capacity": 30, "enrolled_count": 5}
    ]

@web_router.get("/api/v1/student/draft")
async def api_get_draft():
    return {"version": MOCK_STATE["draft_version"], "items": MOCK_STATE["draft_items"]}

@web_router.post("/api/v1/student/draft/items")
async def api_add_draft_item(req: DraftItemReq):
    if any(item["section_id"] == req.section_id for item in MOCK_STATE["draft_items"]):
        raise HTTPException(status_code=400, detail="该班次已在草稿箱中")
    MOCK_STATE["draft_items"].append({
        "section_id": req.section_id,
        "course_code": f"CS{req.section_id}",
        "course_name": "新增选修课程",
        "teacher_name": "主讲教师",
        "schedule_time": "待安排"
    })
    MOCK_STATE["draft_version"] += 1
    return {"message": "已添加"}

@web_router.delete("/api/v1/student/draft/items/{section_id}")
async def api_remove_draft_item(section_id: int):
    MOCK_STATE["draft_items"] = [x for x in MOCK_STATE["draft_items"] if x["section_id"] != section_id]
    MOCK_STATE["draft_version"] += 1
    return {"message": "已移除"}

@web_router.post("/api/v1/student/registration/submit")
async def api_submit_registration(req: SubmitRegistrationReq):
    if req.version != MOCK_STATE["draft_version"]:
        raise HTTPException(status_code=409, detail="草稿版本冲突，请刷新获取最新列表")
    MOCK_STATE["results"] = [
        {"section_id": sid, "course_code": f"CS{sid}", "course_name": f"核心课程-{sid}", "credits": 3.5, "teacher_name": "张教授", "schedule_time": "周一 08:00-09:40", "classroom": "正新楼", "status": "ENROLLED"}
        for sid in req.ordered_section_ids[:4]
    ]
    return {"message": "提交成功"}

@web_router.get("/api/v1/student/registration/results")
async def api_get_results():
    return MOCK_STATE["results"]

@web_router.get("/api/v1/student/grades")
async def api_get_student_grades():
    return {
        "total_credits": 14.0,
        "gpa": 3.82,
        "items": [
            {"semester": "2025-2026-1", "course_code": "CS101", "course_name": "面向对象软件工程", "credits": 4.0, "score": 92.0, "gpa_point": 4.0, "is_passed": True},
            {"semester": "2025-2026-1", "course_code": "CS202", "course_name": "操作系统原理", "credits": 3.5, "score": 88.5, "gpa_point": 3.7, "is_passed": True},
            {"semester": "2025-2026-2", "course_code": "CS303", "course_name": "分布式数据库系统", "credits": 3.0, "score": None, "gpa_point": None, "is_passed": False}
        ]
    }

@web_router.get("/api/v1/teacher/claimable-sections")
async def api_claimable_sections():
    return [
        {"id": 101, "course_code": "CS101", "course_name": "面向对象软件工程", "credits": 4.0, "capacity": 30, "schedule_time": "周一 08:00-09:40", "classroom": "正新楼 301", "teacher_id": 1001, "is_mine": True},
        {"id": 601, "course_code": "CS606", "course_name": "算法设计与分析", "credits": 3.0, "capacity": 45, "schedule_time": "周五 13:30-15:10", "classroom": "计算机楼 A101", "teacher_id": None, "is_mine": False}
    ]

@web_router.post("/api/v1/teacher/sections/{section_id}/claim")
async def api_claim_section(section_id: int):
    return {"message": "认领成功"}

@web_router.post("/api/v1/teacher/sections/{section_id}/unclaim")
async def api_unclaim_section(section_id: int):
    return {"message": "已释放认领"}

@web_router.get("/api/v1/teacher/my-sections")
async def api_my_sections():
    return [{"id": 101, "course_code": "CS101", "course_name": "面向对象软件工程 (班次 01)"}]

@web_router.get("/api/v1/teacher/sections/{section_id}/roster")
async def api_get_roster(section_id: int):
    return [
        {"student_number": "2024001", "full_name": "张三", "department": "软件学院", "enrolled_at": "2026-09-01 10:15", "email": "zhangsan@univ.edu.cn"},
        {"student_number": "2024002", "full_name": "李四", "department": "计算机系", "enrolled_at": "2026-09-01 10:20", "email": "lisi@univ.edu.cn"}
    ]

@web_router.get("/api/v1/teacher/sections/{section_id}/grades")
async def api_get_grading_roster(section_id: int):
    return [
        {"student_id": 1, "student_number": "2024001", "full_name": "张三", "score": 91.5, "updated_at": "2026-09-10 14:00"},
        {"student_id": 2, "student_number": "2024002", "full_name": "李四", "score": None, "updated_at": None}
    ]

@web_router.post("/api/v1/teacher/sections/{section_id}/grades")
async def api_submit_grades(section_id: int, req: SubmitGradesReq):
    return {"message": f"成功保存 {len(req.grades)} 条学生成绩"}

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

    if req.role == "student":
        student = create_student(db, req.full_name, "Initial123")
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
    teacher = create_teacher(db, req.full_name, req.department.strip(), "Initial123")
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
    return {"action": action, "message": "人员已删除" if action == "deleted" else "人员已停用"}


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

@web_router.post("/api/v1/admin/registration/close")
async def api_close_registration(req: CloseReq):
    if req.confirmation != "CLOSE-CONFIRM":
        raise HTTPException(status_code=400, detail="确认口令不正确")
    return {"message": "选课已关闭"}

@web_router.get("/api/v1/admin/registration/closing-status")
async def api_closing_status():
    return {
        "opened_sections_count": 8,
        "cancelled_sections_count": 1,
        "total_enrolled_count": 182,
        "billing_status": "ALL_SENT",
        "bills": [
            {"bill_id": 9001, "student_number": "2024001", "student_name": "张三", "enrolled_count": 4, "amount": 4800.0, "status": "SENT", "sent_at": "2026-09-10 16:30"},
            {"bill_id": 9002, "student_number": "2024002", "student_name": "李四", "enrolled_count": 4, "amount": 4800.0, "status": "SENT", "sent_at": "2026-09-10 16:30"}
        ]
    }
