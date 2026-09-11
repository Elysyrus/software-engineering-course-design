from pathlib import Path
from typing import List, Optional
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

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

@web_router.get("/change-password")
async def page_change_password(request: Request):
    user = {"username": "2024001", "full_name": "当前用户", "role": "student"}
    return templates.TemplateResponse("auth/change_password.html", {"request": request, "user": user})

@web_router.get("/student/courses")
async def page_student_courses(request: Request):
    user = {"username": "2024001", "full_name": "张三", "role": "student"}
    return templates.TemplateResponse("student/courses.html", {"request": request, "user": user, "active_nav": "courses"})

@web_router.get("/student/draft")
async def page_student_draft(request: Request):
    user = {"username": "2024001", "full_name": "张三", "role": "student"}
    return templates.TemplateResponse("student/draft.html", {"request": request, "user": user, "active_nav": "draft"})

@web_router.get("/student/results")
async def page_student_results(request: Request):
    user = {"username": "2024001", "full_name": "张三", "role": "student"}
    return templates.TemplateResponse("student/results.html", {"request": request, "user": user, "active_nav": "results"})

@web_router.get("/student/grades")
async def page_student_grades(request: Request):
    user = {"username": "2024001", "full_name": "张三", "role": "student"}
    return templates.TemplateResponse("student/grades.html", {"request": request, "user": user, "active_nav": "grades"})

@web_router.get("/teacher/claim")
async def page_teacher_claim(request: Request):
    user = {"username": "T1001", "full_name": "李老师", "role": "teacher"}
    return templates.TemplateResponse("teacher/claim.html", {"request": request, "user": user, "active_nav": "claim"})

@web_router.get("/teacher/roster")
async def page_teacher_roster(request: Request):
    user = {"username": "T1001", "full_name": "李老师", "role": "teacher"}
    return templates.TemplateResponse("teacher/roster.html", {"request": request, "user": user, "active_nav": "roster"})

@web_router.get("/teacher/grades")
async def page_teacher_grades(request: Request):
    user = {"username": "T1001", "full_name": "李老师", "role": "teacher"}
    return templates.TemplateResponse("teacher/grades.html", {"request": request, "user": user, "active_nav": "grades"})

@web_router.get("/admin/users")
async def page_admin_users(request: Request):
    user = {"username": "admin", "full_name": "教务管理员", "role": "admin"}
    return templates.TemplateResponse("admin/users.html", {"request": request, "user": user, "active_nav": "users"})

@web_router.get("/admin/close-registration")
async def page_admin_close(request: Request):
    user = {"username": "admin", "full_name": "教务管理员", "role": "admin"}
    return templates.TemplateResponse("admin/close_registration.html", {"request": request, "user": user, "active_nav": "close"})

@web_router.get("/admin/closing-status")
async def page_admin_status(request: Request):
    user = {"username": "admin", "full_name": "教务管理员", "role": "admin"}
    return templates.TemplateResponse("admin/closing_status.html", {"request": request, "user": user, "active_nav": "status"})


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
    username: str
    full_name: str
    role: str
    department: Optional[str] = None

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

@web_router.post("/api/v1/auth/login")
async def api_login(req: LoginReq):
    is_first = (req.password == "123456")
    role = "student"
    if "teacher" in req.username.lower() or req.username.startswith("T"):
        role = "teacher"
    elif "admin" in req.username.lower():
        role = "admin"
    return {
        "access_token": "mock-jwt-token-secd",
        "token_type": "bearer",
        "role": role,
        "is_first_login": is_first
    }

@web_router.post("/api/v1/auth/change-password")
async def api_change_password(req: ChangePwdReq):
    if len(req.new_password) < 8:
        raise HTTPException(status_code=400, detail="新密码安全强度不足，长度需不少于 8 位")
    return {"message": "密码修改成功"}

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
async def api_get_users():
    return [
        {"id": 1, "username": "2024001", "full_name": "张三", "role": "student", "department": "软件学院", "is_active": True},
        {"id": 2, "username": "T1001", "full_name": "李老师", "role": "teacher", "department": "计算机系", "is_active": True},
        {"id": 3, "username": "admin", "full_name": "教务管理员", "role": "admin", "department": "教务处", "is_active": True}
    ]

@web_router.post("/api/v1/admin/users")
async def api_create_user(req: CreateUserReq):
    return {"message": "用户创建成功", "default_password": "InitialPassword123"}

@web_router.post("/api/v1/admin/users/{user_id}/reset-password")
async def api_reset_password(user_id: int):
    return {"message": "密码重置完成，初始密码为 123456"}

@web_router.patch("/api/v1/admin/users/{user_id}/status")
async def api_toggle_status(user_id: int):
    return {"message": "状态切换成功"}

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