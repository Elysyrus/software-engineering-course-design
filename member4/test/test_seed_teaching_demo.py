"""任务 3：教学演示数据脚本测试。"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import (
    Course,
    CoursePrerequisite,
    Enrollment,
    Grade,
    GradeValue,
    Offering,
    OfferingStatus,
    Schedule,
    Semester,
    SemesterStatus,
    Teacher,
    TeacherQualification,
)
from app.services.teaching import list_claimable_offerings
from scripts.seed_demo_data import seed_demo_data
from scripts.seed_teaching_demo import GRADE_PATTERN, seed_teaching_demo


def _factory(db: Session) -> sessionmaker:
    return sessionmaker(bind=db.get_bind(), expire_on_commit=False)


def test_seed_teaching_demo_builds_structure_and_history_once(db: Session):
    factory = _factory(db)
    seed_demo_data("Registrar123", session_factory=factory)

    first = seed_teaching_demo(session_factory=factory)
    second = seed_teaching_demo(session_factory=factory)

    assert first == {
        "courses": 0,
        "semesters": 2,
        "offerings": 12,
        "slots": 12,
        "qualifications": 10,
        "prerequisites": 1,
        "schedules": 10,
        "enrollments": 20,
        "grades": 20,
    }
    assert second == {
        "courses": 0,
        "semesters": 0,
        "offerings": 0,
        "slots": 0,
        "qualifications": 0,
        "prerequisites": 0,
        "schedules": 0,
        "enrollments": 0,
        "grades": 0,
    }
    assert db.scalar(select(func.count()).select_from(Semester)) == 2
    assert db.scalar(select(func.count()).select_from(Offering)) == 12
    assert db.scalar(select(func.count()).select_from(TeacherQualification)) == 10
    assert db.scalar(select(func.count()).select_from(CoursePrerequisite)) == 1
    assert db.scalar(select(func.count()).select_from(Enrollment)) == 20
    assert db.scalar(select(func.count()).select_from(Grade)) == 20


def test_seed_teaching_demo_marks_closed_semester_sections(db: Session):
    factory = _factory(db)
    seed_demo_data("Registrar123", session_factory=factory)
    seed_teaching_demo(session_factory=factory)

    closed = db.scalar(select(Semester).where(Semester.code == "2025-FALL"))
    open_semester = db.scalar(select(Semester).where(Semester.code == "2026-FALL"))
    assert closed.status == SemesterStatus.CLOSED
    assert open_semester.status == SemesterStatus.OPEN

    closed_statuses = db.execute(
        select(Offering.section_number, Offering.status)
        .where(Offering.semester_id == closed.id)
        .order_by(Offering.section_number)
    ).all()
    assert dict(closed_statuses) == {
        "01": OfferingStatus.OPEN,
        "02": OfferingStatus.CANCELLED,
    }

    # 开放学期保留未认领班次供认领演示
    unclaimed = db.scalars(
        select(Offering).where(
            Offering.semester_id == open_semester.id,
            Offering.teacher_id.is_(None),
            Offering.status == OfferingStatus.OPEN,
        )
    ).all()
    assert len(unclaimed) == 3


def test_seed_teaching_demo_history_reflects_grade_pattern(db: Session):
    factory = _factory(db)
    seed_demo_data("Registrar123", session_factory=factory)
    seed_teaching_demo(session_factory=factory)

    closed = db.scalar(select(Semester).where(Semester.code == "2025-FALL"))
    schedules = db.scalars(
        select(Schedule)
        .where(Schedule.semester_id == closed.id)
        .order_by(Schedule.student_id)
    ).all()
    assert len(schedules) == 10
    assert all(schedule.has_submitted for schedule in schedules)

    values = db.scalars(
        select(Grade.value)
        .join(Enrollment, Enrollment.offering_id == Grade.offering_id)
        .where(Enrollment.schedule_id == schedules[0].id, Grade.student_id == schedules[0].student_id)
    ).all()
    assert values == [GRADE_PATTERN[0], GRADE_PATTERN[2]]
    assert GradeValue.A in values


def test_seeded_qualifications_open_claimable_sections(db: Session):
    """种子数据必须让后续认领流程真正可用：有资格者看到班次，无资格者看不到。"""
    factory = _factory(db)
    seed_demo_data("Registrar123", session_factory=factory)
    seed_teaching_demo(session_factory=factory)

    open_semester = db.scalar(select(Semester).where(Semester.code == "2026-FALL"))
    teachers = db.scalars(select(Teacher).order_by(Teacher.teacher_number)).all()

    conflict_teacher = teachers[0]
    claimable = list_claimable_offerings(
        db, teacher_id=conflict_teacher.id, semester_id=open_semester.id
    )
    assert {item["course_code"] for item in claimable} == {"DEMO101", "DEMO102"}
    assert all(item["is_claimed_by_me"] is False for item in claimable)

    teacher_without_qualification = teachers[-1]
    assert (
        list_claimable_offerings(
            db,
            teacher_id=teacher_without_qualification.id,
            semester_id=open_semester.id,
        )
        == []
    )


def test_seeded_open_semester_has_no_teacher_time_conflict(db: Session):
    """预分配的班次之间不能自相矛盾：同一教师不应承担时间冲突的两个班次。"""
    factory = _factory(db)
    seed_demo_data("Registrar123", session_factory=factory)
    seed_teaching_demo(session_factory=factory)

    open_semester = db.scalar(select(Semester).where(Semester.code == "2026-FALL"))
    assigned = db.scalars(
        select(Offering)
        .where(Offering.semester_id == open_semester.id, Offering.teacher_id.is_not(None))
    ).all()
    by_teacher: dict[int, list[Offering]] = {}
    for offering in assigned:
        by_teacher.setdefault(offering.teacher_id, []).append(offering)

    for offerings in by_teacher.values():
        slots = [slot for offering in offerings for slot in offering.slots]
        for index, left in enumerate(slots):
            for right in slots[index + 1 :]:
                assert not (
                    left.weekday == right.weekday
                    and left.start_period <= right.end_period
                    and right.start_period <= left.end_period
                )
