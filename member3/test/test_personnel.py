from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Account, AccountRole, Course, Offering, Schedule, Semester, Student, Teacher
from app.services.personnel import (
    create_student,
    create_teacher,
    delete_or_deactivate_student,
    delete_or_deactivate_teacher,
    get_student,
    get_teacher,
    list_students,
    list_teachers,
    update_student,
    update_teacher,
)


def test_create_student_creates_matching_account(db: Session):
    student = create_student(db, "张三", "Initial123")
    account = db.scalar(select(Account).where(Account.subject_id == student.id))

    assert student.student_number == f"{date.today().year}0001"
    assert account.login_number == student.student_number
    assert account.role == AccountRole.STUDENT
    assert account.must_change_password is True


def test_create_teacher_creates_matching_account(db: Session):
    teacher = create_teacher(db, "李老师", "计算机学院", "Initial123")
    account = db.scalar(select(Account).where(Account.subject_id == teacher.id))

    assert teacher.teacher_number == f"T{date.today().year}0001"
    assert account.login_number == teacher.teacher_number
    assert account.role == AccountRole.TEACHER


def test_list_get_and_update_personnel(db: Session):
    first = create_student(db, "张三", "Initial123")
    second = create_student(db, "李四", "Initial123")
    teacher = create_teacher(db, "王老师", "软件学院", "Initial123")

    assert [student.id for student in list_students(db, name="张")] == [first.id]
    assert [student.id for student in list_students(db, student_number=second.student_number)] == [second.id]
    assert [item.id for item in list_teachers(db, department="软件学院")] == [teacher.id]

    updated_student = update_student(db, first.id, name="张新名", active=False)
    updated_teacher = update_teacher(db, teacher.id, name="王教授", department="计算机学院")
    assert updated_student.name == "张新名"
    assert updated_student.active is False
    assert updated_teacher.name == "王教授"
    assert updated_teacher.department == "计算机学院"
    assert get_student(db, first.id).id == first.id
    assert get_teacher(db, teacher.id).id == teacher.id


def test_get_personnel_rejects_missing_record(db: Session):
    with pytest.raises(ValueError, match="学生不存在"):
        get_student(db, 999)
    with pytest.raises(ValueError, match="教师不存在"):
        get_teacher(db, 999)


def test_delete_personnel_without_history_removes_person_and_account(db: Session):
    student = create_student(db, "可删除学生", "Initial123")
    teacher = create_teacher(db, "可删除教师", "数学学院", "Initial123")

    assert delete_or_deactivate_student(db, student.id) == "deleted"
    assert delete_or_deactivate_teacher(db, teacher.id) == "deleted"
    assert db.get(Student, student.id) is None
    assert db.get(Teacher, teacher.id) is None
    assert db.scalar(select(Account).where(Account.subject_id == student.id)) is None
    assert db.scalar(select(Account).where(Account.subject_id == teacher.id)) is None


def test_student_with_schedule_is_deactivated_and_sessions_are_revoked(db: Session):
    student = create_student(db, "保留历史学生", "Initial123")
    account = db.scalar(select(Account).where(Account.subject_id == student.id))
    semester = Semester(code="2026-FALL", starts_on=date(2026, 9, 1), ends_on=date(2027, 1, 15))
    db.add(semester)
    db.flush()
    db.add(Schedule(student_id=student.id, semester_id=semester.id))
    db.commit()

    assert delete_or_deactivate_student(db, student.id) == "deactivated"
    db.refresh(student)
    db.refresh(account)
    assert student.active is False
    assert account.active is False


def test_teacher_with_offering_is_deactivated(db: Session):
    teacher = create_teacher(db, "保留历史教师", "计算机学院", "Initial123")
    account = db.scalar(select(Account).where(Account.subject_id == teacher.id))
    semester = Semester(code="2027-SPRING", starts_on=date(2027, 2, 20), ends_on=date(2027, 7, 1))
    course = Course(code="CS901", name="测试课程", fee=100)
    db.add_all([semester, course])
    db.flush()
    db.add(
        Offering(
            semester_id=semester.id,
            course_id=course.id,
            teacher_id=teacher.id,
            section_number="01",
            capacity=20,
        )
    )
    db.commit()

    assert delete_or_deactivate_teacher(db, teacher.id) == "deactivated"
    db.refresh(teacher)
    db.refresh(account)
    assert teacher.active is False
    assert account.active is False
