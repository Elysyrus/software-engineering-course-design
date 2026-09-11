from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, selectinload

from app.errors import BusinessError
from app.models import (
    BillingJob,
    ChoiceKind,
    CoursePrerequisite,
    DraftChoice,
    Enrollment,
    FormalAlternate,
    Grade,
    GradeValue,
    Offering,
    OfferingStatus,
    Schedule,
    Semester,
    SemesterStatus,
)
from app.schemas import DraftChoiceInput


PASSING_GRADES = {GradeValue.A, GradeValue.B, GradeValue.C, GradeValue.D}


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _lock_semester(db: Session, semester_id: int, *, exclusive: bool = False) -> Semester:
    stmt = select(Semester).where(Semester.id == semester_id).with_for_update(read=not exclusive)
    semester = db.scalar(stmt)
    if semester is None:
        raise BusinessError("学期不存在", 404)
    return semester


def _require_open(semester: Semester) -> None:
    if semester.status != SemesterStatus.OPEN:
        raise BusinessError("本学期选课已经关闭")


def _get_or_create_schedule(db: Session, student_id: int, semester_id: int) -> Schedule:
    stmt = (
        select(Schedule)
        .where(Schedule.student_id == student_id, Schedule.semester_id == semester_id)
        .with_for_update()
    )
    schedule = db.scalar(stmt)
    if schedule is None:
        schedule = Schedule(student_id=student_id, semester_id=semester_id)
        db.add(schedule)
        db.flush()
    return schedule


def _check_version(schedule: Schedule, expected_version: int) -> None:
    if schedule.version != expected_version:
        raise BusinessError("方案已在其他页面更新，请重新载入")


def _validate_choice_shape(choices: list[DraftChoiceInput]) -> None:
    offering_ids = [choice.offering_id for choice in choices]
    if len(offering_ids) != len(set(offering_ids)):
        raise BusinessError("同一班次不能重复选择", 422)
    priorities = sorted(
        choice.alternate_priority for choice in choices if choice.kind == "alternate"
    )
    if priorities != list(range(1, len(priorities) + 1)):
        raise BusinessError("备选优先级必须从 1 开始且连续", 422)


def _load_offerings(db: Session, offering_ids: list[int], semester_id: int, *, lock: bool) -> dict[int, Offering]:
    if not offering_ids:
        return {}
    stmt = (
        select(Offering)
        .where(Offering.id.in_(sorted(set(offering_ids))))
        .options(selectinload(Offering.course), selectinload(Offering.slots))
        .order_by(Offering.id)
    )
    if lock:
        stmt = stmt.with_for_update()
    offerings = {offering.id: offering for offering in db.scalars(stmt).all()}
    if len(offerings) != len(set(offering_ids)):
        raise BusinessError("存在无效班次", 422)
    if any(offering.semester_id != semester_id for offering in offerings.values()):
        raise BusinessError("不能选择其他学期的班次", 422)
    return offerings


def _slots_conflict(left: Offering, right: Offering) -> bool:
    return any(
        a.weekday == b.weekday
        and a.start_period <= b.end_period
        and b.start_period <= a.end_period
        for a in left.slots
        for b in right.slots
    )


def _passed_course_ids(db: Session, student_id: int, semester: Semester) -> set[int]:
    stmt = (
        select(Offering.course_id)
        .join(Grade, Grade.offering_id == Offering.id)
        .join(Semester, Semester.id == Offering.semester_id)
        .where(
            Grade.student_id == student_id,
            Grade.value.in_(PASSING_GRADES),
            Semester.ends_on < semester.starts_on,
        )
    )
    return set(db.scalars(stmt).all())


def _validate_primaries(
    db: Session,
    *,
    student_id: int,
    semester: Semester,
    primaries: list[Offering],
    schedule_id: int,
) -> None:
    course_ids = [offering.course_id for offering in primaries]
    if len(course_ids) != len(set(course_ids)):
        raise BusinessError("同一课程只能正式选择一个班次")
    for offering in primaries:
        if offering.status != OfferingStatus.OPEN:
            raise BusinessError(f"班次 {offering.id} 当前不可选")
    for index, left in enumerate(primaries):
        for right in primaries[index + 1 :]:
            if _slots_conflict(left, right):
                raise BusinessError(f"班次 {left.id} 与班次 {right.id} 上课时间冲突")

    required_rows = db.execute(
        select(CoursePrerequisite.course_id, CoursePrerequisite.prerequisite_course_id).where(
            CoursePrerequisite.course_id.in_(course_ids)
        )
    ).all()
    passed = _passed_course_ids(db, student_id, semester)
    missing = defaultdict(list)
    for course_id, prerequisite_id in required_rows:
        if prerequisite_id not in passed:
            missing[course_id].append(prerequisite_id)
    if missing:
        details = "; ".join(f"课程 {course}: 缺少 {ids}" for course, ids in sorted(missing.items()))
        raise BusinessError(f"先修课程未满足（{details}）")

    counts = dict(
        db.execute(
            select(Enrollment.offering_id, func.count(Enrollment.id))
            .where(Enrollment.offering_id.in_([item.id for item in primaries]))
            .group_by(Enrollment.offering_id)
        ).all()
    )
    existing_ids = set(
        db.scalars(select(Enrollment.offering_id).where(Enrollment.schedule_id == schedule_id)).all()
    )
    for offering in primaries:
        occupied_by_others = counts.get(offering.id, 0) - (1 if offering.id in existing_ids else 0)
        if occupied_by_others >= offering.capacity:
            raise BusinessError(f"班次 {offering.id} 已满")


def save_draft(
    db: Session,
    *,
    student_id: int,
    semester_id: int,
    expected_version: int,
    choices: list[DraftChoiceInput],
) -> Schedule:
    _validate_choice_shape(choices)
    semester = _lock_semester(db, semester_id)
    _require_open(semester)
    schedule = _get_or_create_schedule(db, student_id, semester_id)
    _check_version(schedule, expected_version)
    _load_offerings(db, [choice.offering_id for choice in choices], semester_id, lock=False)
    db.execute(delete(DraftChoice).where(DraftChoice.schedule_id == schedule.id))
    for choice in choices:
        db.add(
            DraftChoice(
                schedule_id=schedule.id,
                offering_id=choice.offering_id,
                kind=ChoiceKind(choice.kind),
                alternate_priority=choice.alternate_priority,
            )
        )
    schedule.is_deleted = False
    schedule.version += 1
    db.commit()
    return schedule


def get_schedule_view(db: Session, *, student_id: int, semester_id: int) -> dict:
    schedule = db.scalar(
        select(Schedule).where(
            Schedule.student_id == student_id, Schedule.semester_id == semester_id
        )
    )
    if schedule is None:
        return {
            "schedule_id": None,
            "semester_id": semester_id,
            "version": 1,
            "has_submitted": False,
            "is_deleted": False,
            "draft": [],
            "enrolled": [],
            "formal_alternates": [],
        }
    draft_rows = db.scalars(
        select(DraftChoice).where(DraftChoice.schedule_id == schedule.id).order_by(DraftChoice.id)
    ).all()
    enrollment_rows = db.scalars(
        select(Enrollment).where(Enrollment.schedule_id == schedule.id).order_by(Enrollment.offering_id)
    ).all()
    alternate_rows = db.scalars(
        select(FormalAlternate)
        .where(FormalAlternate.schedule_id == schedule.id)
        .order_by(FormalAlternate.priority)
    ).all()
    return {
        "schedule_id": schedule.id,
        "semester_id": semester_id,
        "version": schedule.version,
        "has_submitted": schedule.has_submitted,
        "is_deleted": schedule.is_deleted,
        "draft": [
            {
                "offering_id": row.offering_id,
                "kind": row.kind.value,
                "priority": row.alternate_priority,
            }
            for row in draft_rows
        ],
        "enrolled": [
            {"offering_id": row.offering_id, "kind": "enrolled", "priority": None}
            for row in enrollment_rows
        ],
        "formal_alternates": [
            {"offering_id": row.offering_id, "kind": "alternate", "priority": row.priority}
            for row in alternate_rows
        ],
    }


def submit_schedule(
    db: Session,
    *,
    student_id: int,
    semester_id: int,
    expected_version: int,
) -> Schedule:
    semester = _lock_semester(db, semester_id)
    _require_open(semester)
    schedule = _get_or_create_schedule(db, student_id, semester_id)
    _check_version(schedule, expected_version)
    draft = db.scalars(
        select(DraftChoice).where(DraftChoice.schedule_id == schedule.id).order_by(DraftChoice.id)
    ).all()
    primary_rows = [row for row in draft if row.kind == ChoiceKind.PRIMARY]
    alternate_rows = sorted(
        (row for row in draft if row.kind == ChoiceKind.ALTERNATE),
        key=lambda row: row.alternate_priority or 0,
    )
    if not schedule.has_submitted and (len(primary_rows) != 4 or len(alternate_rows) != 2):
        raise BusinessError("首次提交必须恰好包含 4 个主选和 2 个备选", 422)
    if schedule.has_submitted and (len(primary_rows) > 4 or len(alternate_rows) > 2):
        raise BusinessError("后续提交最多包含 4 个主选和 2 个备选", 422)
    offerings = _load_offerings(
        db, [row.offering_id for row in draft], semester_id, lock=True
    )
    primaries = [offerings[row.offering_id] for row in primary_rows]
    _validate_primaries(
        db,
        student_id=student_id,
        semester=semester,
        primaries=primaries,
        schedule_id=schedule.id,
    )

    db.execute(delete(Enrollment).where(Enrollment.schedule_id == schedule.id))
    db.execute(delete(FormalAlternate).where(FormalAlternate.schedule_id == schedule.id))
    for offering in primaries:
        db.add(
            Enrollment(
                schedule_id=schedule.id,
                offering_id=offering.id,
                course_id=offering.course_id,
            )
        )
    for row in alternate_rows:
        db.add(
            FormalAlternate(
                schedule_id=schedule.id,
                offering_id=row.offering_id,
                priority=row.alternate_priority,
            )
        )
    schedule.has_submitted = True
    schedule.is_deleted = False
    schedule.last_submitted_at = _utcnow()
    schedule.version += 1
    db.commit()
    return schedule


def drop_offering(
    db: Session,
    *,
    student_id: int,
    semester_id: int,
    offering_id: int,
    expected_version: int,
) -> Schedule:
    semester = _lock_semester(db, semester_id)
    _require_open(semester)
    schedule = _get_or_create_schedule(db, student_id, semester_id)
    _check_version(schedule, expected_version)
    _load_offerings(db, [offering_id], semester_id, lock=True)
    enrollment = db.scalar(
        select(Enrollment).where(
            Enrollment.schedule_id == schedule.id, Enrollment.offering_id == offering_id
        )
    )
    if enrollment is None:
        raise BusinessError("该班次不在当前正式结果中", 404)
    db.delete(enrollment)
    db.execute(
        delete(DraftChoice).where(
            DraftChoice.schedule_id == schedule.id, DraftChoice.offering_id == offering_id
        )
    )
    schedule.last_submitted_at = _utcnow()
    schedule.version += 1
    db.commit()
    return schedule


def delete_schedule(
    db: Session,
    *,
    student_id: int,
    semester_id: int,
    expected_version: int,
) -> Schedule:
    semester = _lock_semester(db, semester_id)
    _require_open(semester)
    schedule = _get_or_create_schedule(db, student_id, semester_id)
    _check_version(schedule, expected_version)
    offering_ids = list(
        db.scalars(select(Enrollment.offering_id).where(Enrollment.schedule_id == schedule.id)).all()
    )
    _load_offerings(db, offering_ids, semester_id, lock=True)
    db.execute(delete(DraftChoice).where(DraftChoice.schedule_id == schedule.id))
    db.execute(delete(Enrollment).where(Enrollment.schedule_id == schedule.id))
    db.execute(delete(FormalAlternate).where(FormalAlternate.schedule_id == schedule.id))
    schedule.is_deleted = True
    schedule.version += 1
    schedule.last_submitted_at = _utcnow()
    db.commit()
    return schedule


def _enrollment_counts(db: Session, semester_id: int) -> Counter:
    rows = db.execute(
        select(Enrollment.offering_id, func.count(Enrollment.id))
        .join(Offering, Offering.id == Enrollment.offering_id)
        .where(Offering.semester_id == semester_id)
        .group_by(Enrollment.offering_id)
    ).all()
    return Counter(dict(rows))


def _cancel_offerings(db: Session, offering_ids: set[int]) -> Counter:
    lost = Counter()
    if not offering_ids:
        return lost
    enrollments = db.scalars(select(Enrollment).where(Enrollment.offering_id.in_(offering_ids))).all()
    for enrollment in enrollments:
        lost[enrollment.schedule_id] += 1
        db.delete(enrollment)
    for offering in db.scalars(select(Offering).where(Offering.id.in_(offering_ids))).all():
        offering.status = OfferingStatus.CANCELLED
    db.flush()
    return lost


def _try_level(
    db: Session,
    *,
    semester: Semester,
    lost: Counter,
    offerings: dict[int, Offering],
    require_minimum: bool,
) -> None:
    if not lost:
        return
    schedules = db.scalars(
        select(Schedule)
        .where(Schedule.id.in_(lost), Schedule.is_deleted.is_(False))
        .order_by(Schedule.last_submitted_at, Schedule.id)
        .with_for_update()
    ).all()
    counts = _enrollment_counts(db, semester.id)
    for schedule in schedules:
        alternatives = db.scalars(
            select(FormalAlternate)
            .where(
                FormalAlternate.schedule_id == schedule.id,
                FormalAlternate.consumed_at.is_(None),
            )
            .order_by(FormalAlternate.priority)
        ).all()
        for _ in range(lost[schedule.id]):
            enrolled_rows = db.execute(
                select(Enrollment.offering_id, Enrollment.course_id).where(
                    Enrollment.schedule_id == schedule.id
                )
            ).all()
            enrolled_course_ids = {row.course_id for row in enrolled_rows}
            enrolled_offerings = [offerings[row.offering_id] for row in enrolled_rows]
            selected = None
            for alternate in alternatives:
                offering = offerings[alternate.offering_id]
                if alternate.consumed_at is not None:
                    continue
                if offering.status != OfferingStatus.OPEN or offering.teacher_id is None:
                    continue
                if require_minimum and counts[offering.id] < 3:
                    continue
                if counts[offering.id] >= offering.capacity or offering.course_id in enrolled_course_ids:
                    continue
                if any(_slots_conflict(offering, current) for current in enrolled_offerings):
                    continue
                try:
                    _validate_primaries(
                        db,
                        student_id=schedule.student_id,
                        semester=semester,
                        primaries=[*enrolled_offerings, offering],
                        schedule_id=schedule.id,
                    )
                except BusinessError:
                    continue
                selected = (alternate, offering)
                break
            if selected is None:
                break
            alternate, offering = selected
            db.add(
                Enrollment(
                    schedule_id=schedule.id,
                    offering_id=offering.id,
                    course_id=offering.course_id,
                )
            )
            alternate.consumed_at = _utcnow()
            counts[offering.id] += 1
            db.flush()


def close_registration(db: Session, *, semester_id: int) -> tuple[list[int], int]:
    semester = _lock_semester(db, semester_id, exclusive=True)
    if semester.status == SemesterStatus.CLOSED:
        existing = db.scalar(
            select(func.count(BillingJob.id)).where(BillingJob.semester_id == semester_id)
        )
        cancelled = list(
            db.scalars(
                select(Offering.id).where(
                    Offering.semester_id == semester_id,
                    Offering.status == OfferingStatus.CANCELLED,
                )
            ).all()
        )
        return cancelled, int(existing or 0)

    offerings_list = db.scalars(
        select(Offering)
        .where(Offering.semester_id == semester_id)
        .options(selectinload(Offering.course), selectinload(Offering.slots))
        .order_by(Offering.id)
        .with_for_update()
    ).all()
    offerings = {offering.id: offering for offering in offerings_list}
    no_teacher = {item.id for item in offerings_list if item.teacher_id is None}
    lost_first = _cancel_offerings(db, no_teacher)
    _try_level(
        db,
        semester=semester,
        lost=lost_first,
        offerings=offerings,
        require_minimum=False,
    )

    counts = _enrollment_counts(db, semester_id)
    under_minimum = {
        item.id
        for item in offerings_list
        if item.status == OfferingStatus.OPEN and counts[item.id] < 3
    }
    lost_second = _cancel_offerings(db, under_minimum)
    _try_level(
        db,
        semester=semester,
        lost=lost_second,
        offerings=offerings,
        require_minimum=True,
    )

    for offering in offerings_list:
        if offering.status == OfferingStatus.OPEN:
            offering.status = OfferingStatus.CLOSED
    semester.status = SemesterStatus.CLOSED
    semester.closed_at = _utcnow()
    db.flush()

    jobs_created = 0
    schedules = db.scalars(
        select(Schedule).where(
            Schedule.semester_id == semester_id,
            Schedule.has_submitted.is_(True),
            Schedule.is_deleted.is_(False),
        )
    ).all()
    for schedule in schedules:
        rows = db.execute(
            select(Enrollment.offering_id, Offering.course_id, Offering.section_number)
            .join(Offering, Offering.id == Enrollment.offering_id)
            .where(Enrollment.schedule_id == schedule.id)
            .order_by(Enrollment.offering_id)
        ).all()
        amount = sum((offerings[row.offering_id].course.fee for row in rows), Decimal("0.00"))
        snapshot = json.dumps(
            [
                {
                    "offering_id": row.offering_id,
                    "course_id": row.course_id,
                    "section_number": row.section_number,
                }
                for row in rows
            ],
            ensure_ascii=False,
            sort_keys=True,
        )
        existing = db.scalar(
            select(BillingJob).where(
                BillingJob.semester_id == semester_id,
                BillingJob.student_id == schedule.student_id,
            )
        )
        if existing is None:
            db.add(
                BillingJob(
                    semester_id=semester_id,
                    student_id=schedule.student_id,
                    amount=amount,
                    schedule_snapshot=snapshot,
                )
            )
            jobs_created += 1
    db.commit()
    return sorted(no_teacher | under_minimum), jobs_created
