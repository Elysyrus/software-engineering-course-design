"""教师授课模块：资格查询、可认领班次、我的授课列表。

对外约定见 docs/接口约定v0.2.md。本模块只读取教师自身的授课数据，
不修改学生方案；认领、取消和成绩写入口在同一文件中后补。
"""

from __future__ import annotations

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.errors import BusinessError
from app.models import (
    Course,
    Enrollment,
    Offering,
    OfferingStatus,
    Semester,
    Teacher,
    TeacherQualification,
)


def _get_teacher(db: Session, teacher_id: int) -> Teacher:
    teacher = db.get(Teacher, teacher_id)
    if teacher is None:
        raise BusinessError("教师不存在", 404)
    return teacher


def _get_semester(db: Session, semester_id: int) -> Semester:
    semester = db.get(Semester, semester_id)
    if semester is None:
        raise BusinessError("学期不存在", 404)
    return semester


def qualified_course_ids(db: Session, *, teacher_id: int) -> set[int]:
    """先修检查与认领校验共用的教师资格查询入口。"""
    return set(
        db.scalars(
            select(TeacherQualification.course_id).where(
                TeacherQualification.teacher_id == teacher_id
            )
        ).all()
    )


def list_qualified_courses(db: Session, *, teacher_id: int) -> list[Course]:
    """教师具备授课资格的课程，按课程编号排序。"""
    teacher = _get_teacher(db, teacher_id)
    return list(
        db.scalars(
            select(Course)
            .join(TeacherQualification, TeacherQualification.course_id == Course.id)
            .where(TeacherQualification.teacher_id == teacher.id)
            .order_by(Course.code)
        ).all()
    )


def _enrolled_counts(db: Session, offering_ids: list[int]) -> dict[int, int]:
    if not offering_ids:
        return {}
    rows = db.execute(
        select(Enrollment.offering_id, func.count(Enrollment.id))
        .where(Enrollment.offering_id.in_(offering_ids))
        .group_by(Enrollment.offering_id)
    ).all()
    return {offering_id: count for offering_id, count in rows}


def _offering_view(offering: Offering, *, teacher_id: int, enrolled_count: int) -> dict:
    return {
        "offering_id": offering.id,
        "semester_id": offering.semester_id,
        "course_id": offering.course_id,
        "course_code": offering.course.code,
        "course_name": offering.course.name,
        "fee": offering.course.fee,
        "section_number": offering.section_number,
        "status": offering.status.value,
        "capacity": offering.capacity,
        "enrolled_count": enrolled_count,
        "teacher_id": offering.teacher_id,
        "is_claimed_by_me": offering.teacher_id == teacher_id,
        "slots": [
            {
                "weekday": slot.weekday,
                "start_period": slot.start_period,
                "end_period": slot.end_period,
            }
            for slot in sorted(
                offering.slots, key=lambda item: (item.weekday, item.start_period)
            )
        ],
    }


def list_claimable_offerings(db: Session, *, teacher_id: int, semester_id: int) -> list[dict]:
    """本教师可认领的班次：属于自己资格课程、未被他人认领、未取消。"""
    _get_teacher(db, teacher_id)
    _get_semester(db, semester_id)
    course_ids = qualified_course_ids(db, teacher_id=teacher_id)
    if not course_ids:
        return []
    stmt = (
        select(Offering)
        .where(
            Offering.semester_id == semester_id,
            Offering.course_id.in_(course_ids),
            Offering.status == OfferingStatus.OPEN,
            or_(Offering.teacher_id.is_(None), Offering.teacher_id == teacher_id),
        )
        .options(selectinload(Offering.course), selectinload(Offering.slots))
        .order_by(Offering.course_id, Offering.section_number, Offering.id)
    )
    offerings = list(db.scalars(stmt).all())
    counts = _enrolled_counts(db, [offering.id for offering in offerings])
    return [
        _offering_view(
            offering, teacher_id=teacher_id, enrolled_count=counts.get(offering.id, 0)
        )
        for offering in offerings
    ]


def list_my_offerings(
    db: Session, *, teacher_id: int, semester_id: int | None = None
) -> list[dict]:
    """教师自己的授课列表；不传学期时返回全部学期。"""
    _get_teacher(db, teacher_id)
    if semester_id is not None:
        _get_semester(db, semester_id)
    stmt = select(Offering).where(Offering.teacher_id == teacher_id)
    if semester_id is not None:
        stmt = stmt.where(Offering.semester_id == semester_id)
    stmt = stmt.options(
        selectinload(Offering.course), selectinload(Offering.slots)
    ).order_by(Offering.semester_id, Offering.course_id, Offering.section_number)
    offerings = list(db.scalars(stmt).all())
    counts = _enrolled_counts(db, [offering.id for offering in offerings])
    return [
        _offering_view(
            offering, teacher_id=teacher_id, enrolled_count=counts.get(offering.id, 0)
        )
        for offering in offerings
    ]
