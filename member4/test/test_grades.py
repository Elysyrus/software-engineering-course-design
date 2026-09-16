"""任务 6：成绩录入、修改、留空与变更记录测试。"""

import pytest
from sqlalchemy import func, select

from app.errors import BusinessError
from app.models import (
    Enrollment,
    Grade,
    GradeChange,
    GradeValue,
    Offering,
    OfferingSlot,
    Schedule,
    Student,
)
from app.services.teaching import list_offering_grades, save_offering_grades


@pytest.fixture
def graded_offering(db, seed_basic) -> Offering:
    """已完成学期里由 t1 承担的班次，含 3 名正式学生。"""
    offering = Offering(
        semester_id=seed_basic.closed_semester.id,
        course_id=seed_basic.courses["algorithm"].id,
        teacher_id=seed_basic.teachers["t1"].id,
        section_number="01",
        capacity=10,
    )
    offering.slots.append(OfferingSlot(weekday=1, start_period=1, end_period=2))
    db.add(offering)
    db.flush()
    for student in seed_basic.students:
        schedule = Schedule(
            student_id=student.id,
            semester_id=seed_basic.closed_semester.id,
            has_submitted=True,
        )
        db.add(schedule)
        db.flush()
        db.add(
            Enrollment(
                schedule_id=schedule.id,
                offering_id=offering.id,
                course_id=offering.course_id,
            )
        )
    db.commit()
    return offering


def _entries(seed_basic, values):
    return [
        {"student_id": student.id, "value": value}
        for student, value in zip(seed_basic.students, values)
    ]


def test_first_entry_creates_grade_and_change_record(db, seed_basic, graded_offering):
    teacher = seed_basic.teachers["t1"]
    student = seed_basic.students[0]

    result = save_offering_grades(
        db,
        teacher_id=teacher.id,
        offering_id=graded_offering.id,
        entries=[{"student_id": student.id, "value": "A"}],
    )

    assert result["updated"] == 1
    assert result["changes"] == [
        {
            "student_id": student.id,
            "student_number": student.student_number,
            "previous_value": None,
            "new_value": "A",
        }
    ]
    grade = db.scalar(
        select(Grade).where(
            Grade.student_id == student.id, Grade.offering_id == graded_offering.id
        )
    )
    assert grade.value == GradeValue.A
    change = db.scalar(select(GradeChange))
    assert (change.previous_value, change.new_value) == (None, GradeValue.A)
    assert change.changed_by_teacher_id == teacher.id
    assert change.changed_at is not None


def test_modifying_grade_records_before_and_after_values(db, seed_basic, graded_offering):
    teacher = seed_basic.teachers["t1"]
    student = seed_basic.students[0]
    save_offering_grades(
        db,
        teacher_id=teacher.id,
        offering_id=graded_offering.id,
        entries=[{"student_id": student.id, "value": "B"}],
    )

    save_offering_grades(
        db,
        teacher_id=teacher.id,
        offering_id=graded_offering.id,
        entries=[{"student_id": student.id, "value": "A"}],
    )

    changes = db.scalars(select(GradeChange).order_by(GradeChange.id)).all()
    assert [(row.previous_value, row.new_value) for row in changes] == [
        (None, GradeValue.B),
        (GradeValue.B, GradeValue.A),
    ]


def test_clearing_grade_keeps_record_and_allows_later_reentry(db, seed_basic, graded_offering):
    teacher = seed_basic.teachers["t1"]
    student = seed_basic.students[0]
    save_offering_grades(
        db,
        teacher_id=teacher.id,
        offering_id=graded_offering.id,
        entries=[{"student_id": student.id, "value": "C"}],
    )

    cleared = save_offering_grades(
        db,
        teacher_id=teacher.id,
        offering_id=graded_offering.id,
        entries=[{"student_id": student.id, "value": ""}],
    )

    assert cleared["changes"][0]["new_value"] is None
    grade = db.scalar(
        select(Grade).where(
            Grade.student_id == student.id, Grade.offering_id == graded_offering.id
        )
    )
    assert grade.value is None
    assert db.scalar(select(func.count()).select_from(GradeChange)) == 2


def test_resubmitting_same_grade_writes_nothing(db, seed_basic, graded_offering):
    teacher = seed_basic.teachers["t1"]
    student = seed_basic.students[0]
    save_offering_grades(
        db,
        teacher_id=teacher.id,
        offering_id=graded_offering.id,
        entries=[{"student_id": student.id, "value": "D"}],
    )

    result = save_offering_grades(
        db,
        teacher_id=teacher.id,
        offering_id=graded_offering.id,
        entries=[{"student_id": student.id, "value": "D"}],
    )

    assert result == {"offering_id": graded_offering.id, "updated": 0, "unchanged": 1, "changes": []}
    assert db.scalar(select(func.count()).select_from(GradeChange)) == 1


def test_blank_first_entry_creates_no_change_record(db, seed_basic, graded_offering):
    teacher = seed_basic.teachers["t1"]

    result = save_offering_grades(
        db,
        teacher_id=teacher.id,
        offering_id=graded_offering.id,
        entries=_entries(seed_basic, [None, "", "  "]),
    )

    assert result["updated"] == 0
    assert db.scalar(select(func.count()).select_from(Grade)) == 0
    assert db.scalar(select(func.count()).select_from(GradeChange)) == 0


def test_multiple_students_are_saved_in_one_transaction(db, seed_basic, graded_offering):
    teacher = seed_basic.teachers["t1"]

    result = save_offering_grades(
        db,
        teacher_id=teacher.id,
        offering_id=graded_offering.id,
        entries=_entries(seed_basic, ["A", "F", None]),
    )

    assert result["updated"] == 2
    assert result["unchanged"] == 1
    assert db.scalar(select(func.count()).select_from(GradeChange)) == 2
    listed = list_offering_grades(
        db, teacher_id=teacher.id, offering_id=graded_offering.id
    )
    assert {item["student_number"]: item["value"] for item in listed["students"]} == {
        "20260001": "A",
        "20260002": "F",
        "20260003": None,
    }


def test_rejects_students_outside_the_roster_without_partial_write(
    db, seed_basic, graded_offering
):
    teacher = seed_basic.teachers["t1"]
    enrolled_student = seed_basic.students[0]
    outsider = Student(student_number="20260099", name="外班学生")
    db.add(outsider)
    db.commit()

    with pytest.raises(BusinessError) as error:
        save_offering_grades(
            db,
            teacher_id=teacher.id,
            offering_id=graded_offering.id,
            entries=[
                {"student_id": outsider.id, "value": "A"},
                {"student_id": enrolled_student.id, "value": "B"},
            ],
        )

    assert error.value.status_code == 403
    assert db.scalar(select(func.count()).select_from(Grade)) == 0
    assert db.scalar(select(func.count()).select_from(GradeChange)) == 0


def test_rejects_invalid_grade_value(db, seed_basic, graded_offering):
    teacher = seed_basic.teachers["t1"]
    student = seed_basic.students[0]

    with pytest.raises(BusinessError) as error:
        save_offering_grades(
            db,
            teacher_id=teacher.id,
            offering_id=graded_offering.id,
            entries=[{"student_id": student.id, "value": "E"}],
        )

    assert error.value.status_code == 422
    assert db.scalar(select(func.count()).select_from(Grade)) == 0


def test_rejects_duplicate_student_entries(db, seed_basic, graded_offering):
    teacher = seed_basic.teachers["t1"]
    student = seed_basic.students[0]

    with pytest.raises(BusinessError) as error:
        save_offering_grades(
            db,
            teacher_id=teacher.id,
            offering_id=graded_offering.id,
            entries=[
                {"student_id": student.id, "value": "A"},
                {"student_id": student.id, "value": "B"},
            ],
        )

    assert error.value.status_code == 422


def test_rejects_open_semester(db, seed_basic):
    """开放学期不能录成绩，只有已完成学期可以。"""
    offering = seed_basic.offerings["database"]
    teacher = seed_basic.teachers["t2"]
    student = seed_basic.students[0]

    with pytest.raises(BusinessError) as error:
        save_offering_grades(
            db,
            teacher_id=teacher.id,
            offering_id=offering.id,
            entries=[{"student_id": student.id, "value": "A"}],
        )

    assert error.value.status_code == 409
    assert "已完成学期" in error.value.message


def test_rejects_other_teacher_and_unknown_offering(db, seed_basic, graded_offering):
    student = seed_basic.students[0]

    with pytest.raises(BusinessError) as other_teacher:
        save_offering_grades(
            db,
            teacher_id=seed_basic.teachers["t2"].id,
            offering_id=graded_offering.id,
            entries=[{"student_id": student.id, "value": "A"}],
        )
    assert other_teacher.value.status_code == 403

    with pytest.raises(BusinessError) as missing_offering:
        save_offering_grades(
            db,
            teacher_id=seed_basic.teachers["t1"].id,
            offering_id=9999,
            entries=[{"student_id": student.id, "value": "A"}],
        )
    assert missing_offering.value.status_code == 404


def test_grade_sheet_marks_semester_as_editable(db, seed_basic, graded_offering):
    teacher = seed_basic.teachers["t1"]

    sheet = list_offering_grades(
        db, teacher_id=teacher.id, offering_id=graded_offering.id
    )

    assert sheet["editable"] is True
    assert sheet["semester_status"] == "closed"
    assert len(sheet["students"]) == 3
    assert all(item["value"] is None for item in sheet["students"])

    open_sheet = list_offering_grades(
        db, teacher_id=seed_basic.teachers["t2"].id, offering_id=seed_basic.offerings["database"].id
    )
    assert open_sheet["editable"] is False
    assert open_sheet["semester_status"] == "open"
