"""任务 7：学生成绩查询与先修数据约定测试。"""

from datetime import date
from types import SimpleNamespace

import pytest

from app.errors import BusinessError
from app.models import (
    Enrollment,
    Grade,
    GradeValue,
    Offering,
    OfferingSlot,
    Schedule,
    Semester,
    SemesterStatus,
    Student,
)
from app.services.teaching import (
    completed_course_ids,
    has_passed_course,
    latest_completed_semester,
    list_student_grades,
)


GRADE_VALUES = (
    GradeValue.A,
    GradeValue.B,
    GradeValue.C,
    GradeValue.D,
    GradeValue.F,
    GradeValue.I,
)


@pytest.fixture
def graded_scenario(db, seed_basic) -> SimpleNamespace:
    """已完成学期里的成绩场景。

    - 6 名学生分别得到 A/B/C/D/F/I；
    - student 1 另在 DEMO102 得 F；
    - student 1 在开放学期的 DEMO103 也有一条成绩，用于验证先修检查只认同期之前。
    """
    students = list(seed_basic.students) + [
        Student(student_number=f"2026000{index}", name=f"学生{index}") for index in range(4, 7)
    ]
    db.add_all(students[3:])
    db.flush()

    completed = Offering(
        semester_id=seed_basic.closed_semester.id,
        course_id=seed_basic.courses["algorithm"].id,
        teacher_id=seed_basic.teachers["t1"].id,
        section_number="01",
        capacity=20,
    )
    completed.slots.append(OfferingSlot(weekday=1, start_period=1, end_period=2))
    failed = Offering(
        semester_id=seed_basic.closed_semester.id,
        course_id=seed_basic.courses["database"].id,
        teacher_id=seed_basic.teachers["t2"].id,
        section_number="01",
        capacity=20,
    )
    failed.slots.append(OfferingSlot(weekday=2, start_period=3, end_period=4))
    current = Offering(
        semester_id=seed_basic.semester.id,
        course_id=seed_basic.courses["network"].id,
        section_number="02",
        capacity=20,
    )
    current.slots.append(OfferingSlot(weekday=3, start_period=3, end_period=4))
    db.add_all([completed, failed, current])
    db.flush()

    schedules: dict[tuple[int, int], Schedule] = {}

    def enrol(student, offering, semester, value):
        # 一名学生在一个学期只有一份方案，多门课共用同一份
        key = (student.id, semester.id)
        schedule = schedules.get(key)
        if schedule is None:
            schedule = Schedule(
                student_id=student.id, semester_id=semester.id, has_submitted=True
            )
            db.add(schedule)
            db.flush()
            schedules[key] = schedule
        db.add(
            Enrollment(
                schedule_id=schedule.id,
                offering_id=offering.id,
                course_id=offering.course_id,
            )
        )
        db.add(Grade(student_id=student.id, offering_id=offering.id, value=value))

    for student, value in zip(students, GRADE_VALUES):
        enrol(student, completed, seed_basic.closed_semester, value)
    enrol(students[0], failed, seed_basic.closed_semester, GradeValue.F)
    enrol(students[0], current, seed_basic.semester, GradeValue.A)
    db.commit()
    return SimpleNamespace(
        students=students, completed=completed, failed=failed, current=current
    )


def test_students_see_only_their_own_grades(db, seed_basic, graded_scenario):
    first = graded_scenario.students[0]
    second = graded_scenario.students[1]

    first_grades = list_student_grades(db, student_id=first.id)
    second_grades = list_student_grades(db, student_id=second.id)

    assert first_grades["student_number"] == "20260001"
    assert {item["course_code"] for item in first_grades["grades"]} == {"CS201", "CS202", "CS203"}
    assert [item["course_code"] for item in second_grades["grades"]] == ["CS201"]
    assert second_grades["grades"][0]["value"] == "B"


def test_passed_flag_follows_grade_value(db, graded_scenario):
    expected = {
        GradeValue.A: True,
        GradeValue.B: True,
        GradeValue.C: True,
        GradeValue.D: True,
        GradeValue.F: False,
        GradeValue.I: False,
    }
    for student, value in zip(graded_scenario.students, GRADE_VALUES):
        grades = list_student_grades(
            db, student_id=student.id, semester_id=graded_scenario.completed.semester_id
        )
        algorithm = [item for item in grades["grades"] if item["course_code"] == "CS201"]
        assert len(algorithm) == 1
        assert algorithm[0]["value"] == value.value
        assert algorithm[0]["passed"] is expected[value]


def test_grades_can_be_filtered_by_semester(db, seed_basic, graded_scenario):
    first = graded_scenario.students[0]

    completed_only = list_student_grades(
        db, student_id=first.id, semester_id=seed_basic.closed_semester.id
    )
    current_only = list_student_grades(
        db, student_id=first.id, semester_id=seed_basic.semester.id
    )

    assert {item["course_code"] for item in completed_only["grades"]} == {"CS201", "CS202"}
    assert [item["course_code"] for item in current_only["grades"]] == ["CS203"]


def test_completed_course_ids_only_count_passing_grades_before_semester(
    db, seed_basic, graded_scenario
):
    """先修检查只读取目标学期之前已完成学期中的通过成绩。"""
    first = graded_scenario.students[0]

    passed = completed_course_ids(
        db, student_id=first.id, before_semester_id=seed_basic.semester.id
    )

    assert passed == {seed_basic.courses["algorithm"].id}
    assert (
        has_passed_course(
            db,
            student_id=first.id,
            course_id=seed_basic.courses["algorithm"].id,
            before_semester_id=seed_basic.semester.id,
        )
        is True
    )
    assert (
        has_passed_course(
            db,
            student_id=first.id,
            course_id=seed_basic.courses["database"].id,
            before_semester_id=seed_basic.semester.id,
        )
        is False
    )
    assert (
        has_passed_course(
            db,
            student_id=first.id,
            course_id=seed_basic.courses["network"].id,
            before_semester_id=seed_basic.semester.id,
        )
        is False
    )


def test_latest_completed_semester_ignores_open_and_older_semesters(db, seed_basic):
    older = Semester(
        code="2024-FALL",
        starts_on=date(2024, 9, 1),
        ends_on=date(2025, 1, 15),
        status=SemesterStatus.CLOSED,
    )
    db.add(older)
    db.commit()

    latest = latest_completed_semester(db)

    assert latest is not None
    assert latest.code == "2025-FALL"


def test_unknown_student_is_rejected(db):
    with pytest.raises(BusinessError) as error:
        list_student_grades(db, student_id=9999)
    assert error.value.status_code == 404

    with pytest.raises(BusinessError) as missing_semester:
        list_student_grades(db, student_id=1, semester_id=9999)
    assert missing_semester.value.status_code == 404
