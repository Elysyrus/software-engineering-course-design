"""教师授课模块：资格查询、可认领班次、我的授课列表。

对外约定见 docs/接口约定v0.2.md。本模块只读取教师自身的授课数据，
不修改学生方案；认领、取消和成绩写入口在同一文件中后补。
"""

from __future__ import annotations

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.errors import BusinessError
from app.models import (
    Course,
    Enrollment,
    Grade,
    GradeChange,
    GradeValue,
    Offering,
    OfferingStatus,
    Schedule,
    Semester,
    SemesterStatus,
    Student,
    Teacher,
    TeacherQualification,
)
from app.services.registration import (
    PASSING_GRADES,
    lock_semester,
    passed_course_ids,
    require_open_semester,
    slots_conflict,
)


def _get_teacher(db: Session, teacher_id: int) -> Teacher:
    teacher = db.get(Teacher, teacher_id)
    if teacher is None:
        raise BusinessError("教师不存在", 404)
    return teacher


def _get_semester(db: Session, semester_id: int) -> Semester:
    semester = db.get(Semester, semester_id)
    if semester is None:
        raise BusinessError("学期不存在", 404)
    return semester


def qualified_course_ids(db: Session, *, teacher_id: int) -> set[int]:
    """先修检查与认领校验共用的教师资格查询入口。"""
    return set(
        db.scalars(
            select(TeacherQualification.course_id).where(
                TeacherQualification.teacher_id == teacher_id
            )
        ).all()
    )


def list_qualified_courses(db: Session, *, teacher_id: int) -> list[Course]:
    """教师具备授课资格的课程，按课程编号排序。"""
    teacher = _get_teacher(db, teacher_id)
    return list(
        db.scalars(
            select(Course)
            .join(TeacherQualification, TeacherQualification.course_id == Course.id)
            .where(TeacherQualification.teacher_id == teacher.id)
            .order_by(Course.code)
        ).all()
    )


def _enrolled_counts(db: Session, offering_ids: list[int]) -> dict[int, int]:
    if not offering_ids:
        return {}
    rows = db.execute(
        select(Enrollment.offering_id, func.count(Enrollment.id))
        .where(Enrollment.offering_id.in_(offering_ids))
        .group_by(Enrollment.offering_id)
    ).all()
    return {offering_id: count for offering_id, count in rows}


def offering_view(offering: Offering, *, teacher_id: int, enrolled_count: int) -> dict:
    """班次对外视图；接口层与查询服务共用同一份字段定义。"""
    return {
        "offering_id": offering.id,
        "semester_id": offering.semester_id,
        "course_id": offering.course_id,
        "course_code": offering.course.code,
        "course_name": offering.course.name,
        "fee": offering.course.fee,
        "section_number": offering.section_number,
        "status": offering.status.value,
        "capacity": offering.capacity,
        "enrolled_count": enrolled_count,
        "teacher_id": offering.teacher_id,
        "is_claimed_by_me": offering.teacher_id == teacher_id,
        "slots": [
            {
                "weekday": slot.weekday,
                "start_period": slot.start_period,
                "end_period": slot.end_period,
            }
            for slot in sorted(
                offering.slots, key=lambda item: (item.weekday, item.start_period)
            )
        ],
    }


def list_claimable_offerings(db: Session, *, teacher_id: int, semester_id: int) -> list[dict]:
    """本教师可认领的班次：属于自己资格课程、未被他人认领、未取消。"""
    _get_teacher(db, teacher_id)
    _get_semester(db, semester_id)
    course_ids = qualified_course_ids(db, teacher_id=teacher_id)
    if not course_ids:
        return []
    stmt = (
        select(Offering)
        .where(
            Offering.semester_id == semester_id,
            Offering.course_id.in_(course_ids),
            Offering.status == OfferingStatus.OPEN,
            or_(Offering.teacher_id.is_(None), Offering.teacher_id == teacher_id),
        )
        .options(selectinload(Offering.course), selectinload(Offering.slots))
        .order_by(Offering.course_id, Offering.section_number, Offering.id)
    )
    offerings = list(db.scalars(stmt).all())
    counts = _enrolled_counts(db, [offering.id for offering in offerings])
    return [
        offering_view(
            offering, teacher_id=teacher_id, enrolled_count=counts.get(offering.id, 0)
        )
        for offering in offerings
    ]


def list_my_offerings(
    db: Session, *, teacher_id: int, semester_id: int | None = None
) -> list[dict]:
    """教师自己的授课列表；不传学期时返回全部学期。"""
    _get_teacher(db, teacher_id)
    if semester_id is not None:
        _get_semester(db, semester_id)
    stmt = select(Offering).where(Offering.teacher_id == teacher_id)
    if semester_id is not None:
        stmt = stmt.where(Offering.semester_id == semester_id)
    stmt = stmt.options(
        selectinload(Offering.course), selectinload(Offering.slots)
    ).order_by(Offering.semester_id, Offering.course_id, Offering.section_number)
    offerings = list(db.scalars(stmt).all())
    counts = _enrolled_counts(db, [offering.id for offering in offerings])
    return [
        offering_view(
            offering, teacher_id=teacher_id, enrolled_count=counts.get(offering.id, 0)
        )
        for offering in offerings
    ]


def get_offering_roster(db: Session, *, teacher_id: int, offering_id: int) -> dict:
    """所教班次的正式学生名单。

    只统计已提交且未删除方案中的选课记录：草稿被删除或被退回的方案不进名单。
    """
    offering = _require_own_offering(db, teacher_id=teacher_id, offering_id=offering_id)
    rows = db.execute(
        select(Student.id, Student.student_number, Student.name, Enrollment.enrolled_at)
        .join(Schedule, Schedule.student_id == Student.id)
        .join(Enrollment, Enrollment.schedule_id == Schedule.id)
        .where(
            Enrollment.offering_id == offering.id,
            Schedule.has_submitted.is_(True),
            Schedule.is_deleted.is_(False),
        )
        .order_by(Student.student_number)
    ).all()
    counts = _enrolled_counts(db, [offering.id])
    return {
        "offering": offering_view(
            offering, teacher_id=teacher_id, enrolled_count=counts.get(offering.id, 0)
        ),
        "students": [
            {
                "student_id": student_id,
                "student_number": student_number,
                "name": name,
                "enrolled_at": enrolled_at,
            }
            for student_id, student_number, name, enrolled_at in rows
        ],
    }


def _require_own_offering(db: Session, *, teacher_id: int, offering_id: int) -> Offering:
    """班次存在性、教师存在性与授课归属的统一校验。"""
    _get_teacher(db, teacher_id)
    offering = db.get(Offering, offering_id)
    if offering is None:
        raise BusinessError("班次不存在", 404)
    if offering.teacher_id != teacher_id:
        raise BusinessError("只能访问自己承担班次的数据", 403)
    return offering


def require_completed_semester(semester: Semester) -> None:
    """成绩只录入已完成学期；与选课入口的"学期开放"判断正好相反。"""
    if semester.status != SemesterStatus.CLOSED:
        raise BusinessError("只能录入已完成学期的成绩")


def _formal_students(db: Session, offering: Offering) -> dict[int, str]:
    rows = db.execute(
        select(Student.id, Student.student_number)
        .join(Schedule, Schedule.student_id == Student.id)
        .join(Enrollment, Enrollment.schedule_id == Schedule.id)
        .where(
            Enrollment.offering_id == offering.id,
            Schedule.has_submitted.is_(True),
            Schedule.is_deleted.is_(False),
        )
    ).all()
    return {student_id: student_number for student_id, student_number in rows}


def list_offering_grades(db: Session, *, teacher_id: int, offering_id: int) -> dict:
    """班次成绩单：正式学生名单与当前成绩，供录入页面展示。"""
    offering = _require_own_offering(db, teacher_id=teacher_id, offering_id=offering_id)
    rows = db.execute(
        select(
            Student.id,
            Student.student_number,
            Student.name,
            Grade.value,
        )
        .join(Schedule, Schedule.student_id == Student.id)
        .join(Enrollment, Enrollment.schedule_id == Schedule.id)
        .outerjoin(
            Grade,
            and_(Grade.student_id == Student.id, Grade.offering_id == offering.id),
        )
        .where(
            Enrollment.offering_id == offering.id,
            Schedule.has_submitted.is_(True),
            Schedule.is_deleted.is_(False),
        )
        .order_by(Student.student_number)
    ).all()
    semester = _get_semester(db, offering.semester_id)
    counts = _enrolled_counts(db, [offering.id])
    # 最后一次成绩变更的操作教师与时间，用于页面显示"最后更新人 / 时间"
    latest_changes = {
        student_id: {"updated_at": changed_at, "updated_by": teacher_name}
        for student_id, changed_at, teacher_name in db.execute(
            select(GradeChange.student_id, GradeChange.changed_at, Teacher.name)
            .join(Teacher, Teacher.id == GradeChange.changed_by_teacher_id)
            .where(GradeChange.offering_id == offering.id)
            .order_by(GradeChange.id)
        ).all()
    }
    return {
        "offering": offering_view(
            offering, teacher_id=teacher_id, enrolled_count=counts.get(offering.id, 0)
        ),
        "semester_status": semester.status.value,
        "editable": semester.status == SemesterStatus.CLOSED,
        "students": [
            {
                "student_id": student_id,
                "student_number": student_number,
                "name": name,
                "value": value.value if value is not None else None,
                "updated_at": latest_changes.get(student_id, {}).get("updated_at"),
                "updated_by": latest_changes.get(student_id, {}).get("updated_by"),
            }
            for student_id, student_number, name, value in rows
        ],
    }


def list_grade_changes(
    db: Session, *, teacher_id: int, offering_id: int, student_id: int | None = None
) -> dict:
    """成绩变更记录：首次录入、修改与留空都会追加一条，按时间倒序返回。"""
    offering = _require_own_offering(db, teacher_id=teacher_id, offering_id=offering_id)
    if student_id is not None and db.get(Student, student_id) is None:
        raise BusinessError("学生不存在", 404)

    stmt = (
        select(
            GradeChange.id,
            GradeChange.student_id,
            Student.student_number,
            Student.name,
            GradeChange.previous_value,
            GradeChange.new_value,
            GradeChange.changed_by_teacher_id,
            Teacher.name,
            GradeChange.changed_at,
        )
        .join(Student, Student.id == GradeChange.student_id)
        .join(Teacher, Teacher.id == GradeChange.changed_by_teacher_id)
        .where(GradeChange.offering_id == offering.id)
        .order_by(GradeChange.id.desc())
    )
    if student_id is not None:
        stmt = stmt.where(GradeChange.student_id == student_id)

    rows = db.execute(stmt).all()
    return {
        "offering_id": offering.id,
        "course_code": offering.course.code,
        "course_name": offering.course.name,
        "student_id": student_id,
        "total": len(rows),
        "changes": [
            {
                "change_id": change_id,
                "student_id": row_student_id,
                "student_number": student_number,
                "student_name": student_name,
                "previous_value": previous.value if previous is not None else None,
                "new_value": new.value if new is not None else None,
                "changed_by_teacher_id": changed_by_teacher_id,
                "changed_by": changed_by,
                "changed_at": changed_at,
            }
            for (
                change_id,
                row_student_id,
                student_number,
                student_name,
                previous,
                new,
                changed_by_teacher_id,
                changed_by,
                changed_at,
            ) in rows
        ],
    }


def _normalize_grade(raw_value: object) -> GradeValue | None:
    """空字符串与 None 都表示留空；其余必须是 A-F/I 之一。"""
    if raw_value is None:
        return None
    if isinstance(raw_value, str):
        raw_value = raw_value.strip()
        if raw_value == "":
            return None
    allowed = {value.value for value in GradeValue}
    if raw_value not in allowed:
        raise BusinessError(f"成绩 {raw_value} 不是允许的取值", 422)
    return GradeValue(raw_value)


def save_offering_grades(
    db: Session, *, teacher_id: int, offering_id: int, entries: list[dict]
) -> dict:
    """录入、修改或留空本班次学生成绩，并为每处变化写入变更记录。

    先整批校验再写入：任何一条不合法都不会留下半完成数据。
    重复提交相同成绩视为幂等，不产生多余的变更记录。
    """
    offering = db.get(Offering, offering_id)
    if offering is None:
        raise BusinessError("班次不存在", 404)

    require_completed_semester(lock_semester(db, offering.semester_id))
    teacher = _lock_teacher(db, teacher_id)
    offering = _locked_offering(db, offering_id)
    if offering.teacher_id != teacher.id:
        raise BusinessError("只能录入自己承担班次的学生成绩", 403)

    roster = _formal_students(db, offering)
    parsed: list[tuple[int, GradeValue | None]] = []
    seen: set[int] = set()
    for entry in entries:
        student_id = entry["student_id"]
        if student_id in seen:
            raise BusinessError("同一学生只能提交一条成绩", 422)
        seen.add(student_id)
        if student_id not in roster:
            raise BusinessError("只能为本班次正式选上的学生录入成绩", 403)
        parsed.append((student_id, _normalize_grade(entry.get("value"))))

    changes: list[dict] = []
    unchanged = 0
    for student_id, value in parsed:
        grade = db.scalar(
            select(Grade)
            .where(Grade.student_id == student_id, Grade.offering_id == offering.id)
            .with_for_update()
        )
        if grade is None:
            if value is None:
                # 从未录入且仍然留空，不留下无意义的变更记录
                unchanged += 1
                continue
            db.add(Grade(student_id=student_id, offering_id=offering.id, value=value))
            db.flush()
            previous = None
        else:
            if grade.value == value:
                unchanged += 1
                continue
            previous = grade.value
            grade.value = value

        db.add(
            GradeChange(
                student_id=student_id,
                offering_id=offering.id,
                previous_value=previous,
                new_value=value,
                changed_by_teacher_id=teacher.id,
            )
        )
        changes.append(
            {
                "student_id": student_id,
                "student_number": roster[student_id],
                "previous_value": previous.value if previous is not None else None,
                "new_value": value.value if value is not None else None,
            }
        )

    db.commit()
    return {
        "offering_id": offering.id,
        "updated": len(changes),
        "unchanged": unchanged,
        "changes": changes,
    }


def latest_completed_semester(db: Session) -> Semester | None:
    """最近一个已完成学期（"上一已完成学期"口径）。

    成绩录入只允许发生在已完成学期，本函数是"该录哪个学期"的统一入口，
    供 ``list_gradable_offerings`` 与其它需要默认学期的调用方复用。
    """
    return db.scalar(
        select(Semester)
        .where(Semester.status == SemesterStatus.CLOSED)
        .order_by(Semester.ends_on.desc(), Semester.id.desc())
        .limit(1)
    )


def list_gradable_offerings(db: Session, *, teacher_id: int) -> dict:
    """上一已完成学期中、由本教师承担、可以录入成绩的班次。"""
    _get_teacher(db, teacher_id)
    semester = latest_completed_semester(db)
    if semester is None:
        return {"semester_id": None, "semester_code": None, "offerings": []}
    return {
        "semester_id": semester.id,
        "semester_code": semester.code,
        "offerings": list_my_offerings(
            db, teacher_id=teacher_id, semester_id=semester.id
        ),
    }


def list_student_grades(
    db: Session, *, student_id: int, semester_id: int | None = None
) -> dict:
    """学生查看自己的成绩；不传学期时返回全部已有成绩。"""
    student = db.get(Student, student_id)
    if student is None:
        raise BusinessError("学生不存在", 404)
    if semester_id is not None:
        _get_semester(db, semester_id)

    stmt = (
        select(
            Semester.id,
            Semester.code,
            Course.id,
            Course.code,
            Course.name,
            Offering.id,
            Offering.section_number,
            Grade.value,
        )
        .select_from(Grade)
        .join(Offering, Offering.id == Grade.offering_id)
        .join(Course, Course.id == Offering.course_id)
        .join(Semester, Semester.id == Offering.semester_id)
        .where(Grade.student_id == student.id)
        .order_by(Semester.ends_on.desc(), Course.code)
    )
    if semester_id is not None:
        stmt = stmt.where(Offering.semester_id == semester_id)

    grades = [
        {
            "semester_id": semester_row_id,
            "semester_code": semester_code,
            "course_id": course_id,
            "course_code": course_code,
            "course_name": course_name,
            "offering_id": offering_id,
            "section_number": section_number,
            "value": value.value if value is not None else None,
            "passed": value in PASSING_GRADES,
        }
        for (
            semester_row_id,
            semester_code,
            course_id,
            course_code,
            course_name,
            offering_id,
            section_number,
            value,
        ) in db.execute(stmt).all()
    ]
    return {
        "student_id": student.id,
        "student_number": student.student_number,
        "semester_id": semester_id,
        "grades": grades,
    }


def completed_course_ids(db: Session, *, student_id: int, before_semester_id: int) -> set[int]:
    """先修检查约定：取该学期开始前已完成的学期中，当前有效的通过成绩课程。

    与成员 1 的选课事务共用同一实现，避免先修口径出现第二个版本。
    """
    semester = _get_semester(db, before_semester_id)
    return passed_course_ids(db, student_id=student_id, semester=semester)


def has_passed_course(db: Session, *, student_id: int, course_id: int, before_semester_id: int) -> bool:
    """单个先修条件的判断入口，供先修检查与页面提示复用。"""
    return course_id in completed_course_ids(
        db, student_id=student_id, before_semester_id=before_semester_id
    )


def _lock_teacher(db: Session, teacher_id: int) -> Teacher:
    """按「学期 → 业务主体 → 班次」的顺序锁定教师记录。

    同一教师的并发认领由该行锁串行化，所以不必再锁定他名下的其他班次。
    """
    teacher = db.get(Teacher, teacher_id, with_for_update=True)
    if teacher is None:
        raise BusinessError("教师不存在", 404)
    if not teacher.active:
        raise BusinessError("教师档案已停用", 403)
    return teacher


def _locked_offering(db: Session, offering_id: int) -> Offering:
    offering = db.scalar(
        select(Offering).where(Offering.id == offering_id).with_for_update()
    )
    if offering is None:
        raise BusinessError("班次不存在", 404)
    return offering


def _conflicting_offering(
    db: Session, *, teacher_id: int, offering: Offering
) -> Offering | None:
    """同一学期内，该教师已承担且与本班次上课时间重叠的班次。"""
    others = db.scalars(
        select(Offering)
        .where(
            Offering.teacher_id == teacher_id,
            Offering.semester_id == offering.semester_id,
            Offering.id != offering.id,
            Offering.status == OfferingStatus.OPEN,
        )
        .options(selectinload(Offering.slots))
    ).all()
    for other in others:
        if slots_conflict(offering, other):
            return other
    return None


def claim_offering(db: Session, *, teacher_id: int, offering_id: int) -> Offering:
    """认领授课：校验学期开放、教师资格、班次归属与本人授课时间冲突。

    重复认领自己已承担的班次按幂等处理，不报错也不改变数据。
    """
    offering = db.get(Offering, offering_id)
    if offering is None:
        raise BusinessError("班次不存在", 404)

    require_open_semester(lock_semester(db, offering.semester_id))
    teacher = _lock_teacher(db, teacher_id)
    offering = _locked_offering(db, offering_id)

    if offering.teacher_id == teacher.id:
        db.commit()
        return offering
    if offering.teacher_id is not None:
        raise BusinessError("该班次已被其他教师认领")
    if offering.status != OfferingStatus.OPEN:
        raise BusinessError("班次已取消，不能认领")
    if offering.course_id not in qualified_course_ids(db, teacher_id=teacher.id):
        raise BusinessError("不具备该课程的授课资格", 403)

    conflicting = _conflicting_offering(db, teacher_id=teacher.id, offering=offering)
    if conflicting is not None:
        raise BusinessError(f"与已承担班次 {conflicting.id} 上课时间冲突")

    offering.teacher_id = teacher.id
    db.commit()
    return offering


def release_offering(db: Session, *, teacher_id: int, offering_id: int) -> Offering:
    """取消授课：只能取消自己承担的班次，且要求学期仍在开放状态。"""
    offering = db.get(Offering, offering_id)
    if offering is None:
        raise BusinessError("班次不存在", 404)

    require_open_semester(lock_semester(db, offering.semester_id))
    teacher = _lock_teacher(db, teacher_id)
    offering = _locked_offering(db, offering_id)

    if offering.teacher_id is None:
        raise BusinessError("该班次当前无人承担", 403)
    if offering.teacher_id != teacher.id:
        raise BusinessError("只能取消自己承担的班次", 403)

    offering.teacher_id = None
    db.commit()
    return offering
