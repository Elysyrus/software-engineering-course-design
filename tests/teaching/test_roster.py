"""任务 5：所教班次正式学生名单测试。"""

import pytest

from app.errors import BusinessError
from app.models import Enrollment, Schedule
from app.services.teaching import get_offering_roster


def _record_enrollment(db, student, offering, *, submitted=True, deleted=False):
    """直接构造方案与选课记录，用于覆盖草稿、已删除方案等边界。"""
    schedule = Schedule(
        student_id=student.id,
        semester_id=offering.semester_id,
        has_submitted=submitted,
        is_deleted=deleted,
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


def test_roster_returns_formal_students_sorted_by_number(db, seed_basic):
    database = seed_basic.offerings["database"]
    teacher = seed_basic.teachers["t2"]
    _record_enrollment(db, seed_basic.students[1], database)
    _record_enrollment(db, seed_basic.students[0], database)

    roster = get_offering_roster(db, teacher_id=teacher.id, offering_id=database.id)

    assert [item["student_number"] for item in roster["students"]] == [
        "20260001",
        "20260002",
    ]
    assert roster["students"][0]["name"] == "学生1"
    assert roster["students"][0]["enrolled_at"] is not None
    assert roster["offering"]["offering_id"] == database.id
    assert roster["offering"]["course_code"] == "CS202"
    assert roster["offering"]["enrolled_count"] == 2


def test_roster_skips_drafts_and_deleted_schedules(db, seed_basic):
    database = seed_basic.offerings["database"]
    teacher = seed_basic.teachers["t2"]
    _record_enrollment(db, seed_basic.students[0], database)
    _record_enrollment(db, seed_basic.students[1], database, submitted=False)
    _record_enrollment(db, seed_basic.students[2], database, deleted=True)

    roster = get_offering_roster(db, teacher_id=teacher.id, offering_id=database.id)

    assert [item["student_number"] for item in roster["students"]] == ["20260001"]


def test_roster_does_not_leak_other_offerings(db, seed_basic):
    database = seed_basic.offerings["database"]
    network = seed_basic.offerings["network"]
    teacher = seed_basic.teachers["t2"]
    network.teacher_id = teacher.id
    db.commit()
    _record_enrollment(db, seed_basic.students[0], database)
    _record_enrollment(db, seed_basic.students[1], network)

    roster = get_offering_roster(db, teacher_id=teacher.id, offering_id=database.id)

    assert [item["student_number"] for item in roster["students"]] == ["20260001"]


def test_roster_is_empty_without_enrollments(db, seed_basic):
    database = seed_basic.offerings["database"]
    teacher = seed_basic.teachers["t2"]

    roster = get_offering_roster(db, teacher_id=teacher.id, offering_id=database.id)

    assert roster["students"] == []
    assert roster["offering"]["enrolled_count"] == 0


def test_roster_rejects_other_teachers_offering(db, seed_basic):
    database = seed_basic.offerings["database"]
    other_teacher = seed_basic.teachers["t1"]

    with pytest.raises(BusinessError) as error:
        get_offering_roster(db, teacher_id=other_teacher.id, offering_id=database.id)

    assert error.value.status_code == 403
    assert "自己承担" in error.value.message


def test_roster_rejects_unclaimed_offering(db, seed_basic):
    algorithm = seed_basic.offerings["algorithm"]
    teacher = seed_basic.teachers["t1"]

    with pytest.raises(BusinessError) as error:
        get_offering_roster(db, teacher_id=teacher.id, offering_id=algorithm.id)

    assert error.value.status_code == 403


def test_roster_rejects_unknown_teacher_or_offering(db, seed_basic):
    database = seed_basic.offerings["database"]

    with pytest.raises(BusinessError) as missing_offering:
        get_offering_roster(db, teacher_id=seed_basic.teachers["t2"].id, offering_id=9999)
    assert missing_offering.value.status_code == 404

    with pytest.raises(BusinessError) as missing_teacher:
        get_offering_roster(db, teacher_id=9999, offering_id=database.id)
    assert missing_teacher.value.status_code == 404
