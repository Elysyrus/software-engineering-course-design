"""任务 2：教师资格与班次查询服务测试。"""

import pytest
from sqlalchemy import select

from app.errors import BusinessError
from app.models import (
    Enrollment,
    Offering,
    OfferingStatus,
    Schedule,
    TeacherQualification,
)
from app.services.teaching import (
    list_claimable_offerings,
    list_my_offerings,
    list_qualified_courses,
    qualified_course_ids,
)


def _enroll(db, student, offering):
    """把学生登记为该班次的正式选课结果，用于核对人数统计。"""
    schedule = Schedule(
        student_id=student.id, semester_id=offering.semester_id, has_submitted=True
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


def test_qualified_courses_follow_teacher_qualifications(db, seed_basic):
    teacher = seed_basic.teachers["t1"]
    assert [course.code for course in list_qualified_courses(db, teacher_id=teacher.id)] == [
        "CS201"
    ]
    assert qualified_course_ids(db, teacher_id=teacher.id) == {
        seed_basic.courses["algorithm"].id
    }

    teacher_without_qualification = seed_basic.teachers["t3"]
    assert list_qualified_courses(db, teacher_id=teacher_without_qualification.id) == []
    assert qualified_course_ids(db, teacher_id=teacher_without_qualification.id) == set()


def test_teacher_without_qualification_sees_no_claimable_offering(db, seed_basic):
    teacher = seed_basic.teachers["t3"]
    assert (
        list_claimable_offerings(db, teacher_id=teacher.id, semester_id=seed_basic.semester.id)
        == []
    )


def test_claimable_offerings_include_unclaimed_and_own_sections(db, seed_basic):
    algorithm = seed_basic.offerings["algorithm"]
    database = seed_basic.offerings["database"]

    t1_claimable = list_claimable_offerings(
        db, teacher_id=seed_basic.teachers["t1"].id, semester_id=seed_basic.semester.id
    )
    assert [item["offering_id"] for item in t1_claimable] == [algorithm.id]
    assert t1_claimable[0]["is_claimed_by_me"] is False
    assert t1_claimable[0]["course_code"] == "CS201"
    assert t1_claimable[0]["slots"] == [{"weekday": 1, "start_period": 1, "end_period": 2}]

    t2_claimable = list_claimable_offerings(
        db, teacher_id=seed_basic.teachers["t2"].id, semester_id=seed_basic.semester.id
    )
    assert [item["offering_id"] for item in t2_claimable] == [database.id]
    assert t2_claimable[0]["is_claimed_by_me"] is True


def test_claimable_offerings_exclude_sections_claimed_by_others(db, seed_basic):
    """别的教师已认领的班次不能被抢占，即使我具备资格。"""
    algorithm = seed_basic.offerings["algorithm"]
    algorithm.teacher_id = seed_basic.teachers["t2"].id
    db.commit()

    assert (
        list_claimable_offerings(
            db, teacher_id=seed_basic.teachers["t1"].id, semester_id=seed_basic.semester.id
        )
        == []
    )


def test_claimable_offerings_exclude_cancelled_and_other_semesters(db, seed_basic):
    seed_basic.offerings["algorithm"].status = OfferingStatus.CANCELLED
    db.commit()

    assert (
        list_claimable_offerings(
            db, teacher_id=seed_basic.teachers["t1"].id, semester_id=seed_basic.semester.id
        )
        == []
    )
    assert (
        list_claimable_offerings(
            db,
            teacher_id=seed_basic.teachers["t1"].id,
            semester_id=seed_basic.closed_semester.id,
        )
        == []
    )


def test_claimable_offerings_report_enrolled_count(db, seed_basic):
    algorithm = seed_basic.offerings["algorithm"]
    _enroll(db, seed_basic.students[0], algorithm)
    _enroll(db, seed_basic.students[1], algorithm)

    claimable = list_claimable_offerings(
        db, teacher_id=seed_basic.teachers["t1"].id, semester_id=seed_basic.semester.id
    )
    assert [item["enrolled_count"] for item in claimable] == [2]


def test_my_offerings_only_returns_own_sections(db, seed_basic):
    database = seed_basic.offerings["database"]

    t2_offerings = list_my_offerings(db, teacher_id=seed_basic.teachers["t2"].id)
    assert [item["offering_id"] for item in t2_offerings] == [database.id]
    assert t2_offerings[0]["is_claimed_by_me"] is True

    assert list_my_offerings(db, teacher_id=seed_basic.teachers["t1"].id) == []


def test_my_offerings_can_be_filtered_by_semester(db, seed_basic):
    teacher_id = seed_basic.teachers["t2"].id
    assert (
        list_my_offerings(db, teacher_id=teacher_id, semester_id=seed_basic.semester.id)
        != []
    )
    assert (
        list_my_offerings(
            db, teacher_id=teacher_id, semester_id=seed_basic.closed_semester.id
        )
        == []
    )


def test_unknown_teacher_or_semester_is_rejected(db, seed_basic):
    with pytest.raises(BusinessError) as missing_teacher:
        list_my_offerings(db, teacher_id=9999)
    assert missing_teacher.value.status_code == 404

    with pytest.raises(BusinessError) as missing_semester:
        list_claimable_offerings(
            db, teacher_id=seed_basic.teachers["t1"].id, semester_id=9999
        )
    assert missing_semester.value.status_code == 404


def test_qualification_rows_survive_repeated_queries(db, seed_basic):
    """只读查询不得改动资格数据。"""
    before = db.scalars(select(TeacherQualification.id).order_by(TeacherQualification.id)).all()
    list_claimable_offerings(
        db, teacher_id=seed_basic.teachers["t1"].id, semester_id=seed_basic.semester.id
    )
    list_my_offerings(db, teacher_id=seed_basic.teachers["t2"].id)
    after = db.scalars(select(TeacherQualification.id).order_by(TeacherQualification.id)).all()
    assert before == after
    assert db.scalars(select(Offering.id)).all() != []
