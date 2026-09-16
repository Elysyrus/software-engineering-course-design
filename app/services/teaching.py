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
from app.services.registration import (
    lock_semester,
    require_open_semester,
    slots_conflict,
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


def offering_view(offering: Offering, *, teacher_id: int, enrolled_count: int) -> dict:
    """班次对外视图；接口层与查询服务共用同一份字段定义。"""
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
        offering_view(
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
        offering_view(
            offering, teacher_id=teacher_id, enrolled_count=counts.get(offering.id, 0)
        )
        for offering in offerings
    ]


def _lock_teacher(db: Session, teacher_id: int) -> Teacher:
    """按「学期 → 业务主体 → 班次」的顺序锁定教师记录。

    同一教师的并发认领由该行锁串行化，所以不必再锁定他名下的其他班次。
    """
    teacher = db.get(Teacher, teacher_id, with_for_update=True)
    if teacher is None:
        raise BusinessError("教师不存在", 404)
    if not teacher.active:
        raise BusinessError("教师档案已停用", 403)
    return teacher


def _locked_offering(db: Session, offering_id: int) -> Offering:
    offering = db.scalar(
        select(Offering).where(Offering.id == offering_id).with_for_update()
    )
    if offering is None:
        raise BusinessError("班次不存在", 404)
    return offering


def _conflicting_offering(
    db: Session, *, teacher_id: int, offering: Offering
) -> Offering | None:
    """同一学期内，该教师已承担且与本班次上课时间重叠的班次。"""
    others = db.scalars(
        select(Offering)
        .where(
            Offering.teacher_id == teacher_id,
            Offering.semester_id == offering.semester_id,
            Offering.id != offering.id,
            Offering.status == OfferingStatus.OPEN,
        )
        .options(selectinload(Offering.slots))
    ).all()
    for other in others:
        if slots_conflict(offering, other):
            return other
    return None


def claim_offering(db: Session, *, teacher_id: int, offering_id: int) -> Offering:
    """认领授课：校验学期开放、教师资格、班次归属与本人授课时间冲突。

    重复认领自己已承担的班次按幂等处理，不报错也不改变数据。
    """
    offering = db.get(Offering, offering_id)
    if offering is None:
        raise BusinessError("班次不存在", 404)

    require_open_semester(lock_semester(db, offering.semester_id))
    teacher = _lock_teacher(db, teacher_id)
    offering = _locked_offering(db, offering_id)

    if offering.teacher_id == teacher.id:
        db.commit()
        return offering
    if offering.teacher_id is not None:
        raise BusinessError("该班次已被其他教师认领")
    if offering.status != OfferingStatus.OPEN:
        raise BusinessError("班次已取消，不能认领")
    if offering.course_id not in qualified_course_ids(db, teacher_id=teacher.id):
        raise BusinessError("不具备该课程的授课资格", 403)

    conflicting = _conflicting_offering(db, teacher_id=teacher.id, offering=offering)
    if conflicting is not None:
        raise BusinessError(f"与已承担班次 {conflicting.id} 上课时间冲突")

    offering.teacher_id = teacher.id
    db.commit()
    return offering


def release_offering(db: Session, *, teacher_id: int, offering_id: int) -> Offering:
    """取消授课：只能取消自己承担的班次，且要求学期仍在开放状态。"""
    offering = db.get(Offering, offering_id)
    if offering is None:
        raise BusinessError("班次不存在", 404)

    require_open_semester(lock_semester(db, offering.semester_id))
    teacher = _lock_teacher(db, teacher_id)
    offering = _locked_offering(db, offering_id)

    if offering.teacher_id is None:
        raise BusinessError("该班次当前无人承担", 403)
    if offering.teacher_id != teacher.id:
        raise BusinessError("只能取消自己承担的班次", 403)

    offering.teacher_id = None
    db.commit()
    return offering
