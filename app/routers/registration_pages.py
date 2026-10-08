"""页面适配仅整理展示字段，所有修改仍由统一业务服务执行。"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.auth_contract import require_registrar, require_student
from app.database import get_db
from app.errors import BusinessError
from app.models import (
    Course,
    CoursePrerequisite,
    Enrollment,
    Offering,
    Semester,
    SemesterStatus,
)
from app.routers.deps import require_csrf_token
from app.schemas import SaveDraftRequest, VersionRequest
from app.services import billing, catalog, registration

router = APIRouter(prefix="/api/v1", tags=["选课页面与关闭"])


def target_semester(db: Session, semester_id: int | None) -> Semester:
    semester = (
        db.get(Semester, semester_id)
        if semester_id
        else db.scalar(
            select(Semester)
            .order_by(Semester.starts_on.desc(), Semester.id.desc())
            .limit(1)
        )
    )
    if semester is None:
        raise BusinessError("暂无学期，请先初始化课程目录", 404)
    return semester


def section_view(db: Session, offering: Offering) -> dict:
    prerequisites = db.scalars(
        select(Course.code)
        .join(
            CoursePrerequisite, CoursePrerequisite.prerequisite_course_id == Course.id
        )
        .where(CoursePrerequisite.course_id == offering.course_id)
    ).all()
    return {
        "id": offering.id,
        "section_id": offering.id,
        "course_code": offering.course.code,
        "course_name": offering.course.name,
        "description": offering.course.description,
        "fee": str(offering.course.fee),
        "prerequisites": list(prerequisites),
        "teacher_name": offering.teacher.name if offering.teacher else "待认领",
        "section_number": offering.section_number,
        "capacity": offering.capacity,
        "enrolled_count": db.scalar(
            select(func.count(Enrollment.id)).where(
                Enrollment.offering_id == offering.id
            )
        )
        or 0,
        "schedule_time": " / ".join(
            f"周{slot.weekday} 第{slot.start_period}–{slot.end_period}节"
            for slot in offering.slots
        ),
        "status": offering.status.value,
    }


@router.get("/student/available-sections")
def available_sections(
    semester_id: int | None = Query(None, ge=1),
    _student=Depends(require_student),
    db: Session = Depends(get_db),
):
    semester = target_semester(db, semester_id)
    external = catalog.fetch_catalog(semester.code)
    if external.semester_code != semester.code:
        raise BusinessError("外部目录返回了其他学期，拒绝使用", 503)
    metadata = {item.code: item for item in external.courses}
    offerings = db.scalars(
        select(Offering)
        .where(Offering.semester_id == semester.id)
        .options(
            selectinload(Offering.course),
            selectinload(Offering.slots),
            selectinload(Offering.teacher),
        )
        .order_by(Offering.id)
    ).all()
    sections = []
    for offering in offerings:
        item = metadata.get(offering.course.code)
        if item is None:
            continue
        row = section_view(db, offering)
        row.update(
            description=item.description,
            department=item.department,
            prerequisites=item.prerequisites,
        )
        sections.append(row)
    return {
        "semester_id": semester.id,
        "semester_code": semester.code,
        "status": semester.status.value,
        "sections": sections,
    }


@router.get("/student/draft")
def draft(
    semester_id: int | None = Query(None, ge=1),
    student_id=Depends(require_student),
    db: Session = Depends(get_db),
):
    semester = target_semester(db, semester_id)
    result = registration.get_schedule_view(
        db, student_id=student_id, semester_id=semester.id
    )

    def enrich(rows):
        return [
            dict(section_view(db, db.get(Offering, row["offering_id"])), **row)
            for row in rows
        ]

    return dict(
        result,
        semester_code=semester.code,
        status=semester.status.value,
        items=enrich(result["draft"]),
        results=enrich(result["enrolled"]),
        alternates=enrich(result["formal_alternates"]),
    )


class CloseRequest(BaseModel):
    semester_id: int = Field(ge=1)
    confirmation: str


@router.post("/admin/registration/close", dependencies=[Depends(require_csrf_token)])
def close(
    body: CloseRequest,
    _registrar=Depends(require_registrar),
    db: Session = Depends(get_db),
):
    if body.confirmation != "CLOSE-CONFIRM":
        raise BusinessError("确认口令不正确", 422)
    cancelled, jobs = registration.close_registration(db, semester_id=body.semester_id)
    return {
        "message": "选课已关闭，账单任务已保存",
        "cancelled_offering_ids": cancelled,
        "billing_jobs_created": jobs,
    }


@router.get("/admin/registration/closing-status")
def closing_status(
    semester_id: int | None = Query(None, ge=1),
    _registrar=Depends(require_registrar),
    db: Session = Depends(get_db),
):
    return billing.billing_summary(db, semester_id=target_semester(db, semester_id).id)
