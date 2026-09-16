"""成员 4 教师授课与学生成绩真实接口。

路径与成员 2 页面里的模拟接口刻意区分：
模拟实现仍留在 web/web_routes.py，真实接口挂在 /api/v1/teacher/offerings/... 下。
"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth_contract import current_user, require_student, require_teacher
from app.database import get_db
from app.models import Semester
from app.routers.deps import require_csrf_token
from app.services import teaching


router = APIRouter(prefix="/api/v1", tags=["成员4-教学"])


class GradeEntryInput(BaseModel):
    student_id: int
    # 空字符串与 null 都表示留空成绩
    value: str | None = None


class SaveGradesRequest(BaseModel):
    grades: list[GradeEntryInput] = Field(default_factory=list, max_length=200)


class SemesterItem(BaseModel):
    semester_id: int
    code: str
    status: str
    starts_on: str
    ends_on: str


@router.get("/semesters", response_model=list[SemesterItem])
def list_semesters(
    _user=Depends(current_user), db: Session = Depends(get_db)
):
    """所有已登录角色都可读的学期列表，供页面选择学期。"""
    semesters = db.scalars(
        select(Semester).order_by(Semester.starts_on.desc(), Semester.id.desc())
    ).all()
    return [
        SemesterItem(
            semester_id=semester.id,
            code=semester.code,
            status=semester.status.value,
            starts_on=semester.starts_on.isoformat(),
            ends_on=semester.ends_on.isoformat(),
        )
        for semester in semesters
    ]


@router.get("/teacher/me/qualified-courses")
def get_qualified_courses(
    teacher_id: int = Depends(require_teacher), db: Session = Depends(get_db)
):
    """当前教师具备授课资格的课程。"""
    courses = teaching.list_qualified_courses(db, teacher_id=teacher_id)
    return {
        "courses": [
            {
                "course_id": course.id,
                "course_code": course.code,
                "course_name": course.name,
                "fee": str(course.fee),
            }
            for course in courses
        ]
    }


@router.get("/teacher/offerings/claimable")
def list_claimable_offerings(
    semester_id: int = Query(..., ge=1),
    teacher_id: int = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """当前教师在本学期可认领的班次。"""
    return {
        "offerings": teaching.list_claimable_offerings(
            db, teacher_id=teacher_id, semester_id=semester_id
        )
    }


@router.get("/teacher/offerings/mine")
def list_my_offerings(
    semester_id: int | None = Query(default=None, ge=1),
    teacher_id: int = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """当前教师自己的授课列表。"""
    return {
        "offerings": teaching.list_my_offerings(
            db, teacher_id=teacher_id, semester_id=semester_id
        )
    }


@router.post(
    "/teacher/offerings/{offering_id}/claim",
    dependencies=[Depends(require_csrf_token)],
)
def claim_offering(
    offering_id: int,
    teacher_id: int = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    offering = teaching.claim_offering(
        db, teacher_id=teacher_id, offering_id=offering_id
    )
    return {
        "message": "认领成功",
        "offering_id": offering.id,
        "teacher_id": offering.teacher_id,
    }


@router.post(
    "/teacher/offerings/{offering_id}/release",
    dependencies=[Depends(require_csrf_token)],
)
def release_offering(
    offering_id: int,
    teacher_id: int = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    offering = teaching.release_offering(
        db, teacher_id=teacher_id, offering_id=offering_id
    )
    return {
        "message": "已取消授课",
        "offering_id": offering.id,
        "teacher_id": offering.teacher_id,
    }


@router.get("/teacher/offerings/{offering_id}/roster")
def get_offering_roster(
    offering_id: int,
    teacher_id: int = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """所教班次的正式学生名单。"""
    return teaching.get_offering_roster(
        db, teacher_id=teacher_id, offering_id=offering_id
    )


@router.get("/teacher/offerings/{offering_id}/grades")
def get_offering_grades(
    offering_id: int,
    teacher_id: int = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """班次成绩单：正式名单与当前成绩。"""
    return teaching.list_offering_grades(
        db, teacher_id=teacher_id, offering_id=offering_id
    )


@router.put(
    "/teacher/offerings/{offering_id}/grades",
    dependencies=[Depends(require_csrf_token)],
)
def save_offering_grades(
    offering_id: int,
    body: SaveGradesRequest,
    teacher_id: int = Depends(require_teacher),
    db: Session = Depends(get_db),
):
    """录入、修改或留空成绩。"""
    return teaching.save_offering_grades(
        db,
        teacher_id=teacher_id,
        offering_id=offering_id,
        entries=[
            {"student_id": item.student_id, "value": item.value} for item in body.grades
        ],
    )


@router.get("/student/me/grades")
def get_my_grades(
    semester_id: int | None = Query(default=None, ge=1),
    student_id: int = Depends(require_student),
    db: Session = Depends(get_db),
):
    """学生查看自己的成绩。"""
    return teaching.list_student_grades(
        db, student_id=student_id, semester_id=semester_id
    )
