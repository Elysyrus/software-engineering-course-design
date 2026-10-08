"""最终版演示数据；重复运行不重置已有账号、成绩、选课或关闭状态。"""

import json
from datetime import date
from pathlib import Path
from sqlalchemy import select
from app.database import SessionLocal
from app.models import (
    AccountRole,
    Course,
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
from app.services.auth import create_account
from app.services.catalog import CatalogSnapshot, import_catalog
from app.services.personnel import init_registrar


def seed(db):
    init_registrar(db, "registrar", "Initial123")
    students = []
    teachers = []
    for i in range(1, 11):
        student = db.scalar(select(Student).where(Student.student_number == f"S{i:03}"))
        if student is None:
            student = Student(
                student_number=f"S{i:03}",
                name=f"演示学生{i}",
                birth_date=date(2004, 1, i),
                social_security_number=f"DEMO-S-{i:03}",
                graduation_date=date(2027, 6, 30),
                status="active",
            )
            db.add(student)
            db.flush()
            create_account(
                db,
                student.student_number,
                "Initial123",
                AccountRole.STUDENT,
                student.id,
            )
        students.append(student)
        teacher = db.scalar(select(Teacher).where(Teacher.teacher_number == f"T{i:03}"))
        if teacher is None:
            teacher = Teacher(
                teacher_number=f"T{i:03}",
                name=f"演示教师{i}",
                department="计算机学院",
                birth_date=date(1980, 1, i),
                social_security_number=f"DEMO-T-{i:03}",
                status="active",
            )
            db.add(teacher)
            db.flush()
            create_account(
                db,
                teacher.teacher_number,
                "Initial123",
                AccountRole.TEACHER,
                teacher.id,
            )
        teachers.append(teacher)
    db.commit()
    path = Path(__file__).resolve().parents[1] / "mock_catalog/catalog_data.json"
    snapshot = CatalogSnapshot.model_validate(
        json.loads(path.read_text(encoding="utf-8"))
    )
    existing = db.scalar(
        select(Semester).where(Semester.code == snapshot.semester_code)
    )
    semester = (
        existing
        if existing and existing.status == SemesterStatus.CLOSED
        else import_catalog(db, snapshot)
    )
    for i, item in enumerate(snapshot.courses):
        course = db.scalar(select(Course).where(Course.code == item.code))
        offering = db.scalar(
            select(Offering).where(
                Offering.semester_id == semester.id,
                Offering.course_id == course.id,
                Offering.section_number == "01",
            )
        )
        if not db.scalar(
            select(TeacherQualification.id).where(
                TeacherQualification.teacher_id == teachers[i].id,
                TeacherQualification.course_id == course.id,
            )
        ):
            db.add(TeacherQualification(teacher_id=teachers[i].id, course_id=course.id))
        if offering.teacher_id is None and semester.status == SemesterStatus.OPEN:
            offering.teacher_id = teachers[i].id
    history = db.scalar(select(Semester).where(Semester.code == "2025-FALL"))
    if history is None:
        history = Semester(
            code="2025-FALL",
            starts_on=date(2025, 9, 1),
            ends_on=date(2026, 1, 15),
            status=SemesterStatus.CLOSED,
        )
        db.add(history)
        db.flush()
    course = db.scalar(select(Course).where(Course.code == snapshot.courses[0].code))
    old = db.scalar(
        select(Offering).where(
            Offering.semester_id == history.id, Offering.course_id == course.id
        )
    )
    if old is None:
        old = Offering(
            semester_id=history.id,
            course_id=course.id,
            teacher_id=teachers[0].id,
            section_number="01",
            status=OfferingStatus.CLOSED,
            capacity=10,
        )
        old.slots = [OfferingSlot(weekday=1, start_period=1, end_period=2)]
        db.add(old)
        db.flush()
    for student in students[:3]:
        schedule = db.scalar(
            select(Schedule).where(
                Schedule.student_id == student.id, Schedule.semester_id == history.id
            )
        )
        if schedule is None:
            schedule = Schedule(
                student_id=student.id, semester_id=history.id, has_submitted=True
            )
            db.add(schedule)
            db.flush()
            db.add(
                Enrollment(
                    schedule_id=schedule.id, offering_id=old.id, course_id=course.id
                )
            )
            db.add(Grade(student_id=student.id, offering_id=old.id, value=GradeValue.B))
    db.commit()
    return semester


def main():
    with SessionLocal() as db:
        semester = seed(db)
        print(
            f"演示学期：{semester.code}；教务 registrar；学生 S001–S010；教师 T001–T010；初始密码 Initial123，首次登录必须改密。"
        )


if __name__ == "__main__":
    main()
