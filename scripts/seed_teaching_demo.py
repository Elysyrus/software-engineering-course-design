"""演示用的教学结构数据：学期、班次、时段、授课资格与历史成绩。

在 ``scripts.seed_demo_data`` 之后运行：

    ./.venv/Scripts/python.exe -m scripts.seed_teaching_demo

脚本可重复执行：已存在的学期、班次、资格、选课与成绩都会原样保留，
不会覆盖教师在页面上做出的修改。
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import (
    Account,
    AccountRole,
    Course,
    CoursePrerequisite,
    Enrollment,
    Grade,
    GradeValue,
    Offering,
    OfferingSlot,
    OfferingStatus,
    Schedule,
    Semester,
    SemesterStatus,
    Student,
    Teacher,
    TeacherQualification,
)
from scripts.seed_demo_data import DEMO_COURSES


# (学期编号, 开始日期, 结束日期, 状态)
SEMESTERS = (
    ("2025-FALL", date(2025, 9, 1), date(2026, 1, 15), SemesterStatus.CLOSED),
    ("2026-FALL", date(2026, 9, 1), date(2027, 1, 15), SemesterStatus.OPEN),
)

# (课程编号, 班次号, 星期, 起始节次, 结束节次, 是否预先分配教师)
#
# DEMO101-01 与 DEMO102-01 的上课时间相同，用来演示教师授课时间冲突；
# 预先分配的班次留给花名册与成绩演示，未分配的班次留给认领演示。
SECTIONS = (
    ("DEMO101", "01", 1, 1, 2, False),
    ("DEMO101", "02", 3, 3, 4, True),
    ("DEMO102", "01", 1, 1, 2, False),
    ("DEMO102", "02", 4, 5, 6, True),
    ("DEMO103", "01", 3, 3, 4, False),
    ("DEMO103", "02", 5, 1, 2, True),
)

# 已关闭学期里演示先修链条：修完并通过 DEMO101 才能选 DEMO102
PREREQUISITE = ("DEMO102", "DEMO101")

# 历史成绩按学生顺序循环取用，F 与留空表示未通过，用于验证先修判断
GRADE_PATTERN = (
    GradeValue.A,
    GradeValue.B,
    GradeValue.C,
    GradeValue.D,
    GradeValue.F,
    None,
)

HISTORY_COURSES = ("DEMO101", "DEMO103")

# 开放学期里已有教师承担的班次；给它们放正式选课，关闭后才能生成非零账单
OPEN_SEMESTER_SECTIONS = (
    ("DEMO101", "02"),
    ("DEMO102", "02"),
    ("DEMO103", "02"),
)

# 关闭时不足 3 人的班次会被取消，演示数据按 4 人准备，留出余量
OPEN_SEMESTER_STUDENT_COUNT = 4

# 具备多门课程资格、用于演示"认领后与已有授课时间冲突"的教师序号
CONFLICT_DEMO_COURSE_CODES = ("DEMO101", "DEMO102")


def _qualification_plan(teacher_count: int) -> dict[int, tuple[str, ...]]:
    """教师序号 → 具备授课资格的课程编号。

    第一位教师同时具备两门上课时间冲突的课程资格，用来演示认领冲突；
    最后一位教师不具备任何资格，用来演示无资格拒绝认领。
    """
    course_codes = list(dict.fromkeys(section[0] for section in SECTIONS))
    if teacher_count <= 0:
        return {}
    if teacher_count == 1:
        return {0: tuple(course_codes)}
    plan = {0: CONFLICT_DEMO_COURSE_CODES, teacher_count - 1: ()}
    for index in range(1, teacher_count - 1):
        plan[index] = (course_codes[index % len(course_codes)],)
    return plan


def _get_or_create_semester(db: Session, spec, created: dict[str, int]) -> Semester:
    code, starts_on, ends_on, status = spec
    semester = db.scalar(select(Semester).where(Semester.code == code))
    if semester is None:
        semester = Semester(code=code, starts_on=starts_on, ends_on=ends_on, status=status)
        db.add(semester)
        db.flush()
        created["semesters"] += 1
    return semester


def _ensure_courses(db: Session, created: dict[str, int]) -> dict[str, Course]:
    courses = {course.code: course for course in db.scalars(select(Course)).all()}
    for code, name, description, fee in DEMO_COURSES:
        if code not in courses:
            course = Course(code=code, name=name, description=description, fee=fee)
            db.add(course)
            db.flush()
            courses[code] = course
            created["courses"] += 1
    return courses


def _ensure_offerings(
    db: Session,
    *,
    semester: Semester,
    courses: dict[str, Course],
    teachers: list[Teacher],
    created: dict[str, int],
) -> dict[tuple[str, str], Offering]:
    """按学期建立班次与时段，并按规则分配教师或标记取消。"""
    courses_by_code = {code: course for code, course in courses.items()}
    qualification_pool: dict[str, list[Teacher]] = {}
    for index, assigned_codes in _qualification_plan(len(teachers)).items():
        teacher = teachers[index]
        for code in assigned_codes:
            qualification_pool.setdefault(code, []).append(teacher)

    offerings: dict[tuple[str, str], Offering] = {}
    for course_code, section_number, weekday, start_period, end_period, preassigned in SECTIONS:
        course = courses_by_code[course_code]
        offering = db.scalar(
            select(Offering).where(
                Offering.semester_id == semester.id,
                Offering.course_id == course.id,
                Offering.section_number == section_number,
            )
        )
        if offering is None:
            offering = Offering(
                semester_id=semester.id,
                course_id=course.id,
                section_number=section_number,
                capacity=30,
            )
            offering.slots.append(
                OfferingSlot(
                    weekday=weekday, start_period=start_period, end_period=end_period
                )
            )
            db.add(offering)
            db.flush()
            created["offerings"] += 1
            created["slots"] += 1

        candidates = qualification_pool.get(course_code, [])
        if semester.status == SemesterStatus.CLOSED:
            # 关闭学期只保留"01"班次供成绩录入演示，无人认领的"02"按关闭结果标记取消
            should_assign = section_number == "01"
        else:
            should_assign = preassigned
        if should_assign and offering.teacher_id is None and candidates:
            # 用班次号选人，让同一门课的不同班次落到不同教师，便于演示"已被他人认领"
            offering.teacher_id = candidates[int(section_number) % len(candidates)].id
        elif not should_assign and semester.status == SemesterStatus.CLOSED:
            # 关闭学期里无人认领的班次按关闭流程的结果标记为已取消
            offering.status = OfferingStatus.CANCELLED

        offerings[(course_code, section_number)] = offering
    db.flush()
    return offerings


def _ensure_qualifications(
    db: Session, *, courses: dict[str, Course], teachers: list[Teacher], created: dict[str, int]
) -> None:
    for index, assigned_codes in _qualification_plan(len(teachers)).items():
        teacher = teachers[index]
        for code in assigned_codes:
            exists = db.scalar(
                select(TeacherQualification.id).where(
                    TeacherQualification.teacher_id == teacher.id,
                    TeacherQualification.course_id == courses[code].id,
                )
            )
            if exists is None:
                db.add(
                    TeacherQualification(teacher_id=teacher.id, course_id=courses[code].id)
                )
                created["qualifications"] += 1
    db.flush()


def _ensure_prerequisite(db: Session, *, courses: dict[str, Course], created: dict[str, int]) -> None:
    course_code, prerequisite_code = PREREQUISITE
    existing = db.scalar(
        select(CoursePrerequisite.id).where(
            CoursePrerequisite.course_id == courses[course_code].id,
            CoursePrerequisite.prerequisite_course_id == courses[prerequisite_code].id,
        )
    )
    if existing is None:
        db.add(
            CoursePrerequisite(
                course_id=courses[course_code].id,
                prerequisite_course_id=courses[prerequisite_code].id,
            )
        )
        created["prerequisites"] += 1
        db.flush()


def _ensure_history(
    db: Session,
    *,
    semester: Semester,
    offerings: dict[tuple[str, str], Offering],
    created: dict[str, int],
) -> None:
    """在已完成学期里造出正式选课与历史成绩，供成绩录入与先修检查演示。"""
    students = db.scalars(select(Student).order_by(Student.student_number)).all()
    for index, student in enumerate(students):
        schedule = db.scalar(
            select(Schedule).where(
                Schedule.student_id == student.id, Schedule.semester_id == semester.id
            )
        )
        if schedule is None:
            schedule = Schedule(
                student_id=student.id, semester_id=semester.id, has_submitted=True
            )
            db.add(schedule)
            db.flush()
            created["schedules"] += 1
        for offset, course_code in enumerate(HISTORY_COURSES):
            offering = offerings[(course_code, "01")]
            if offering.status != OfferingStatus.OPEN or offering.teacher_id is None:
                continue
            enrolled = db.scalar(
                select(Enrollment.id).where(
                    Enrollment.schedule_id == schedule.id,
                    Enrollment.offering_id == offering.id,
                )
            )
            if enrolled is None:
                db.add(
                    Enrollment(
                        schedule_id=schedule.id,
                        offering_id=offering.id,
                        course_id=offering.course_id,
                    )
                )
                created["enrollments"] += 1
            grade = db.scalar(
                select(Grade).where(
                    Grade.student_id == student.id, Grade.offering_id == offering.id
                )
            )
            if grade is None:
                db.add(
                    Grade(
                        student_id=student.id,
                        offering_id=offering.id,
                        value=GRADE_PATTERN[(index + offset * 2) % len(GRADE_PATTERN)],
                    )
                )
                created["grades"] += 1
    db.flush()


def _ensure_open_semester_enrollments(
    db: Session,
    *,
    semester: Semester,
    offerings: dict[tuple[str, str], Offering],
    created: dict[str, int],
) -> None:
    """给开放学期里已有教师的班次放正式选课。

    没有这些选课，关闭选课时所有班次都会因不足 3 人而被取消，账单快照为空、
    金额为 0，"关闭 -> 生成账单 -> 发送重试"就演示不出结果。
    未认领的班次仍然留空，不占用认领演示用的班次。
    """
    students = db.scalars(
        select(Student)
        .order_by(Student.student_number)
        .limit(OPEN_SEMESTER_STUDENT_COUNT)
    ).all()
    submitted_at = datetime.now(UTC)
    for student in students:
        schedule = db.scalar(
            select(Schedule).where(
                Schedule.student_id == student.id,
                Schedule.semester_id == semester.id,
            )
        )
        if schedule is None:
            schedule = Schedule(
                student_id=student.id,
                semester_id=semester.id,
                has_submitted=True,
                last_submitted_at=submitted_at,
            )
            db.add(schedule)
            db.flush()
            created["schedules"] += 1
        for course_code, section_number in OPEN_SEMESTER_SECTIONS:
            offering = offerings.get((course_code, section_number))
            if offering is None or offering.status != OfferingStatus.OPEN:
                continue
            if offering.teacher_id is None:
                continue
            enrolled = db.scalar(
                select(Enrollment.id).where(
                    Enrollment.schedule_id == schedule.id,
                    Enrollment.offering_id == offering.id,
                )
            )
            if enrolled is None:
                db.add(
                    Enrollment(
                        schedule_id=schedule.id,
                        offering_id=offering.id,
                        course_id=offering.course_id,
                    )
                )
                created["enrollments"] += 1
    db.flush()


def seed_teaching_demo(
    session_factory: Callable[[], Session] = SessionLocal,
) -> dict[str, int]:
    """创建或复用教学演示数据，返回本次新建数量。"""
    created = {
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
    with session_factory() as db:
        courses = _ensure_courses(db, created)
        teachers = list(
            db.scalars(select(Teacher).order_by(Teacher.teacher_number)).all()
        )
        _ensure_prerequisite(db, courses=courses, created=created)
        if teachers:
            _ensure_qualifications(
                db, courses=courses, teachers=teachers, created=created
            )

        for spec in SEMESTERS:
            semester = _get_or_create_semester(db, spec, created)
            offerings = _ensure_offerings(
                db,
                semester=semester,
                courses=courses,
                teachers=teachers,
                created=created,
            )
            if semester.status == SemesterStatus.CLOSED and teachers:
                _ensure_history(
                    db, semester=semester, offerings=offerings, created=created
                )
            if semester.status == SemesterStatus.OPEN and teachers:
                _ensure_open_semester_enrollments(
                    db, semester=semester, offerings=offerings, created=created
                )
        db.commit()
    return created


def _describe_demo_state(session_factory: Callable[[], Session] = SessionLocal) -> list[str]:
    """输出演示要点：每个教师的资格与开放学期仍可认领的班次。"""
    with session_factory() as db:
        login_numbers = {
            account.subject_id: account.login_number
            for account in db.scalars(
                select(Account).where(Account.role == AccountRole.TEACHER)
            ).all()
        }
        lines = []
        rows = db.execute(
            select(Teacher.teacher_number, Course.code)
            .join(TeacherQualification, TeacherQualification.teacher_id == Teacher.id)
            .join(Course, Course.id == TeacherQualification.course_id)
            .order_by(Teacher.teacher_number, Course.code)
        ).all()
        for teacher_number, course_code in rows:
            lines.append(f"  资格：{teacher_number} → {course_code}")

        open_semester = db.scalar(
            select(Semester).where(Semester.status == SemesterStatus.OPEN).order_by(Semester.code)
        )
        if open_semester is not None:
            claimable = db.execute(
                select(Course.code, Offering.section_number)
                .join(Offering, Offering.course_id == Course.id)
                .where(
                    Offering.semester_id == open_semester.id,
                    Offering.teacher_id.is_(None),
                    Offering.status == OfferingStatus.OPEN,
                )
                .order_by(Course.code, Offering.section_number)
            ).all()
            lines.append(
                f"  {open_semester.code} 待认领班次："
                + "、".join(f"{code}-{section}" for code, section in claimable)
            )
        return lines


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化教学演示数据")
    parser.parse_args()
    created = seed_teaching_demo()
    print("教学演示数据初始化完成：" + "，".join(f"{key}={value}" for key, value in created.items()))
    for line in _describe_demo_state():
        print(line)
    print("已关闭学期 2025-FALL 含历史选课与成绩，可用于成绩录入与先修检查演示。")
    print(
        "开放学期 2026-FALL 的已认领班次各有 4 名正式学生："
        "关闭选课会保留这些班次并生成非零账单，可用于计费发送与重试演示。"
    )


if __name__ == "__main__":
    main()
