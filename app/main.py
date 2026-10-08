from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.auth_contract import (
    CurrentUser,
    current_user,
    require_registrar,
    require_student,
)
from app.database import get_db
from app.errors import BusinessError
from app.schemas import (
    CloseResult,
    OperationResult,
    SaveDraftRequest,
    ScheduleView,
    VersionRequest,
)
from app.services.registration import (
    close_registration,
    delete_schedule,
    drop_offering,
    get_schedule_view,
    save_draft,
    submit_schedule,
)
from app.routers.deps import require_csrf_token
from app.routers import billing as billing_routes
from app.routers import teaching as teaching_routes
from app.routers import registration_pages

app = FastAPI(title="课程注册系统", version="0.1.0")

# 所有页面与 API 复用真实业务服务。
app.include_router(teaching_routes.router)
app.include_router(billing_routes.router)
app.include_router(registration_pages.router)


@app.exception_handler(BusinessError)
async def business_error_handler(_request, exc: BusinessError):
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/semesters/{semester_id}/schedule", response_model=ScheduleView)
def get_schedule(
    semester_id: int,
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    student_id = require_student(user)
    return get_schedule_view(db, student_id=student_id, semester_id=semester_id)


# 保存草稿接口
@app.put(
    "/semesters/{semester_id}/schedule/draft",
    response_model=OperationResult,
    dependencies=[Depends(require_csrf_token)],
)
def put_draft(
    semester_id: int,
    body: SaveDraftRequest,  # 把JSON转换成SaveDraftRequest
    # Depends(...)表示执行该路由函数前，先调用括号内指定的依赖函数，取得需要的数据
    user: CurrentUser = Depends(current_user),  # 通过登录Cookie查询当前用户
    db: Session = Depends(get_db),
):
    student_id = require_student(user)
    schedule = save_draft(
        db,
        student_id=student_id,
        semester_id=semester_id,
        expected_version=body.expected_version,
        choices=body.choices,
    )
    return OperationResult(
        schedule_id=schedule.id, version=schedule.version, message="草稿已保存"
    )


@app.post(
    "/semesters/{semester_id}/schedule/submit",
    response_model=OperationResult,
    dependencies=[Depends(require_csrf_token)],
)
def submit(
    semester_id: int,
    body: VersionRequest,
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    student_id = require_student(user)
    schedule = submit_schedule(
        db,
        student_id=student_id,
        semester_id=semester_id,
        expected_version=body.expected_version,
    )
    return OperationResult(
        schedule_id=schedule.id, version=schedule.version, message="选课方案已提交"
    )


@app.delete(
    "/semesters/{semester_id}/schedule/offerings/{offering_id}",
    response_model=OperationResult,
    dependencies=[Depends(require_csrf_token)],
)
def drop(
    semester_id: int,
    offering_id: int,
    body: VersionRequest,
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    student_id = require_student(user)
    schedule = drop_offering(
        db,
        student_id=student_id,
        semester_id=semester_id,
        offering_id=offering_id,
        expected_version=body.expected_version,
    )
    return OperationResult(
        schedule_id=schedule.id, version=schedule.version, message="退课成功"
    )


@app.delete(
    "/semesters/{semester_id}/schedule",
    response_model=OperationResult,
    dependencies=[Depends(require_csrf_token)],
)
def remove_schedule(
    semester_id: int,
    body: VersionRequest,
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    student_id = require_student(user)
    schedule = delete_schedule(
        db,
        student_id=student_id,
        semester_id=semester_id,
        expected_version=body.expected_version,
    )
    return OperationResult(
        schedule_id=schedule.id, version=schedule.version, message="选课方案已删除"
    )


@app.post(
    "/registrar/semesters/{semester_id}/close",
    response_model=CloseResult,
    dependencies=[Depends(require_csrf_token)],
)
def close(
    semester_id: int,
    user: CurrentUser = Depends(current_user),
    db: Session = Depends(get_db),
):
    require_registrar(user)
    cancelled, jobs = close_registration(db, semester_id=semester_id)
    return CloseResult(
        semester_id=semester_id,
        cancelled_offering_ids=cancelled,
        billing_jobs_created=jobs,
        message="选课已关闭",
    )


# 成员 2 前端模块挂载
from pathlib import Path
from fastapi.staticfiles import StaticFiles
from web.web_routes import web_router

# 1. 挂载静态文件目录
app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).resolve().parent.parent / "web" / "static"),
    name="static",
)

# 2. 引入前端页面路由
app.include_router(web_router)
