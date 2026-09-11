from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select

from app.errors import BusinessError
from app.models import (
    BillingJob,
    ChoiceKind,
    DraftChoice,
    Enrollment,
    FormalAlternate,
    Offering,
    OfferingStatus,
    Schedule,
    Semester,
    CoursePrerequisite,
    Grade,
    GradeValue,
    SemesterStatus,
)
from app.schemas import DraftChoiceInput
from app.services.registration import (
    close_registration,
    delete_schedule,
    drop_offering,
    save_draft,
    submit_schedule,
)


def choices(offerings, primary_count=4, alternate_count=2):
    result = [
        DraftChoiceInput(offering_id=item.id, kind="primary")
        for item in offerings[:primary_count]
    ]
    result.extend(
        DraftChoiceInput(
            offering_id=item.id,
            kind="alternate",
            alternate_priority=index,
        )
        for index, item in enumerate(
            offerings[primary_count : primary_count + alternate_count], start=1
        )
    )
    return result


def create_submitted(db, student, semester, offerings):
    schedule = save_draft(
        db,
        student_id=student.id,
        semester_id=semester.id,
        expected_version=1,
        choices=choices(offerings),
    )
    return submit_schedule(
        db,
        student_id=student.id,
        semester_id=semester.id,
        expected_version=schedule.version,
    )


def test_save_draft_does_not_take_capacity_or_change_queue_time(db, seeded):
    schedule = save_draft(
        db,
        student_id=seeded["students"][0].id,
        semester_id=seeded["semester"].id,
        expected_version=1,
        choices=choices(seeded["offerings"]),
    )

    assert schedule.version == 2
    assert schedule.last_submitted_at is None
    assert db.scalar(select(func.count(Enrollment.id))) == 0
    assert db.scalar(select(func.count(DraftChoice.id))) == 6


def test_first_submit_requires_exactly_four_primary_and_two_alternate(db, seeded):
    schedule = save_draft(
        db,
        student_id=seeded["students"][0].id,
        semester_id=seeded["semester"].id,
        expected_version=1,
        choices=choices(seeded["offerings"], primary_count=3, alternate_count=2),
    )

    with pytest.raises(BusinessError, match="首次提交必须恰好"):
        submit_schedule(
            db,
            student_id=seeded["students"][0].id,
            semester_id=seeded["semester"].id,
            expected_version=schedule.version,
        )

    db.rollback()
    assert db.scalar(select(func.count(Enrollment.id))) == 0
    assert db.get(Schedule, schedule.id).has_submitted is False


def test_successful_submit_creates_enrollments_and_formal_alternates(db, seeded):
    schedule = create_submitted(
        db, seeded["students"][0], seeded["semester"], seeded["offerings"]
    )

    assert schedule.has_submitted is True
    assert schedule.last_submitted_at is not None
    assert db.scalar(
        select(func.count(Enrollment.id)).where(Enrollment.schedule_id == schedule.id)
    ) == 4
    assert db.scalar(
        select(func.count(FormalAlternate.id)).where(FormalAlternate.schedule_id == schedule.id)
    ) == 2


def test_failed_change_to_full_offering_preserves_previous_result(db, seeded):
    schedule = create_submitted(
        db, seeded["students"][0], seeded["semester"], seeded["offerings"]
    )
    old_ids = set(
        db.scalars(select(Enrollment.offering_id).where(Enrollment.schedule_id == schedule.id))
    )
    full = seeded["offerings"][6]
    full.capacity = 1
    blocker = Schedule(
        student_id=seeded["students"][1].id,
        semester_id=seeded["semester"].id,
        has_submitted=True,
        last_submitted_at=datetime.now(UTC),
    )
    db.add(blocker)
    db.flush()
    db.add(Enrollment(schedule_id=blocker.id, offering_id=full.id, course_id=full.course_id))
    db.commit()

    changed = [seeded["offerings"][0], seeded["offerings"][1], seeded["offerings"][2], full]
    draft_choices = choices(changed + [seeded["offerings"][4], seeded["offerings"][5]])
    schedule = save_draft(
        db,
        student_id=seeded["students"][0].id,
        semester_id=seeded["semester"].id,
        expected_version=schedule.version,
        choices=draft_choices,
    )
    with pytest.raises(BusinessError, match="已满"):
        submit_schedule(
            db,
            student_id=seeded["students"][0].id,
            semester_id=seeded["semester"].id,
            expected_version=schedule.version,
        )
    db.rollback()

    current_ids = set(
        db.scalars(select(Enrollment.offering_id).where(Enrollment.schedule_id == schedule.id))
    )
    assert current_ids == old_ids


def test_drop_allows_less_than_four_and_does_not_use_alternate(db, seeded):
    schedule = create_submitted(
        db, seeded["students"][0], seeded["semester"], seeded["offerings"]
    )
    dropped_id = seeded["offerings"][0].id
    old_time = schedule.last_submitted_at
    schedule = drop_offering(
        db,
        student_id=seeded["students"][0].id,
        semester_id=seeded["semester"].id,
        offering_id=dropped_id,
        expected_version=schedule.version,
    )

    remaining = list(
        db.scalars(select(Enrollment.offering_id).where(Enrollment.schedule_id == schedule.id))
    )
    assert len(remaining) == 3
    assert dropped_id not in remaining
    assert schedule.last_submitted_at >= old_time
    assert db.scalar(
        select(func.count(FormalAlternate.id)).where(FormalAlternate.schedule_id == schedule.id)
    ) == 2


def test_stale_page_version_is_rejected_without_changes(db, seeded):
    schedule = save_draft(
        db,
        student_id=seeded["students"][0].id,
        semester_id=seeded["semester"].id,
        expected_version=1,
        choices=choices(seeded["offerings"]),
    )
    with pytest.raises(BusinessError, match="重新载入"):
        save_draft(
            db,
            student_id=seeded["students"][0].id,
            semester_id=seeded["semester"].id,
            expected_version=1,
            choices=[],
        )
    db.rollback()
    assert db.get(Schedule, schedule.id).version == 2


def test_time_conflict_rejects_whole_submission(db, seeded):
    left, right = seeded["offerings"][:2]
    right.slots[0].weekday = left.slots[0].weekday
    right.slots[0].start_period = left.slots[0].start_period
    right.slots[0].end_period = left.slots[0].end_period
    db.commit()
    schedule = save_draft(
        db,
        student_id=seeded["students"][0].id,
        semester_id=seeded["semester"].id,
        expected_version=1,
        choices=choices(seeded["offerings"]),
    )

    with pytest.raises(BusinessError, match="上课时间冲突"):
        submit_schedule(
            db,
            student_id=seeded["students"][0].id,
            semester_id=seeded["semester"].id,
            expected_version=schedule.version,
        )
    db.rollback()
    assert db.scalar(select(func.count(Enrollment.id))) == 0


def test_prerequisite_requires_previous_passing_grade(db, seeded):
    target = seeded["courses"][1]
    prerequisite = seeded["courses"][0]
    db.add(CoursePrerequisite(course_id=target.id, prerequisite_course_id=prerequisite.id))
    db.commit()
    selected = seeded["offerings"][1:7]
    schedule = save_draft(
        db,
        student_id=seeded["students"][0].id,
        semester_id=seeded["semester"].id,
        expected_version=1,
        choices=choices(selected),
    )
    with pytest.raises(BusinessError, match="先修课程未满足"):
        submit_schedule(
            db,
            student_id=seeded["students"][0].id,
            semester_id=seeded["semester"].id,
            expected_version=schedule.version,
        )
    db.rollback()

    previous = Semester(
        code="2026-SPRING", starts_on=date(2026, 2, 1), ends_on=date(2026, 6, 30), status=SemesterStatus.CLOSED
    )
    db.add(previous)
    db.flush()
    old_offering = Offering(
        semester_id=previous.id,
        course_id=prerequisite.id,
        teacher_id=seeded["teacher"].id,
        section_number="01",
        capacity=10,
        status=OfferingStatus.CLOSED,
    )
    db.add(old_offering)
    db.flush()
    db.add(
        Grade(
            student_id=seeded["students"][0].id,
            offering_id=old_offering.id,
            value=GradeValue.A,
        )
    )
    db.commit()

    submitted = submit_schedule(
        db,
        student_id=seeded["students"][0].id,
        semester_id=seeded["semester"].id,
        expected_version=schedule.version,
    )
    assert submitted.has_submitted is True


def test_competing_for_last_seat_never_exceeds_capacity(db, seeded):
    target = seeded["offerings"][0]
    target.capacity = 1
    first = create_submitted(
        db, seeded["students"][0], seeded["semester"], seeded["offerings"]
    )
    second_draft = save_draft(
        db,
        student_id=seeded["students"][1].id,
        semester_id=seeded["semester"].id,
        expected_version=1,
        choices=choices(seeded["offerings"]),
    )
    with pytest.raises(BusinessError, match="已满"):
        submit_schedule(
            db,
            student_id=seeded["students"][1].id,
            semester_id=seeded["semester"].id,
            expected_version=second_draft.version,
        )
    db.rollback()

    assert db.scalar(
        select(func.count(Enrollment.id)).where(Enrollment.offering_id == target.id)
    ) == 1
    assert db.scalar(
        select(func.count(Enrollment.id)).where(Enrollment.schedule_id == first.id)
    ) == 4


def test_delete_releases_enrollments_and_marks_history(db, seeded):
    schedule = create_submitted(
        db, seeded["students"][0], seeded["semester"], seeded["offerings"]
    )
    schedule = delete_schedule(
        db,
        student_id=seeded["students"][0].id,
        semester_id=seeded["semester"].id,
        expected_version=schedule.version,
    )

    assert schedule.is_deleted is True
    assert schedule.has_submitted is True
    assert db.scalar(
        select(func.count(Enrollment.id)).where(Enrollment.schedule_id == schedule.id)
    ) == 0
    assert db.scalar(
        select(func.count(FormalAlternate.id)).where(FormalAlternate.schedule_id == schedule.id)
    ) == 0


def test_close_cancels_invalid_offerings_levels_and_creates_billing_jobs(db, seeded):
    schedule = create_submitted(
        db, seeded["students"][0], seeded["semester"], seeded["offerings"]
    )
    # 主选 1 无教师会被取消；备选 5 有三名既有学生，因此可以稳定接收替补。
    seeded["offerings"][0].teacher_id = None
    for student in seeded["students"][1:4]:
        extra = Schedule(
            student_id=student.id,
            semester_id=seeded["semester"].id,
            has_submitted=True,
            last_submitted_at=datetime.now(UTC),
        )
        db.add(extra)
        db.flush()
        for offering in seeded["offerings"][1:5]:
            db.add(
                Enrollment(
                    schedule_id=extra.id,
                    offering_id=offering.id,
                    course_id=offering.course_id,
                )
            )
    db.commit()

    cancelled, jobs = close_registration(db, semester_id=seeded["semester"].id)

    current_ids = set(
        db.scalars(select(Enrollment.offering_id).where(Enrollment.schedule_id == schedule.id))
    )
    assert seeded["offerings"][0].id in cancelled
    assert seeded["offerings"][4].id in current_ids
    assert seeded["offerings"][0].id not in current_ids
    assert seeded["semester"].status == SemesterStatus.CLOSED
    assert jobs == 4
    job = db.scalar(select(BillingJob).where(BillingJob.student_id == seeded["students"][0].id))
    assert str(job.amount) == "4000.00"
    assert seeded["offerings"][0].status == OfferingStatus.CANCELLED

    cancelled_again, existing_jobs = close_registration(db, semester_id=seeded["semester"].id)
    assert cancelled_again == cancelled
    assert existing_jobs == 4
    assert db.scalar(select(func.count(BillingJob.id))) == 4
