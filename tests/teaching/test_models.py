"""成员 4 新增数据模型的约束与生命周期测试。"""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models import GradeChange, GradeValue, TeacherQualification


def test_teacher_qualification_is_unique_per_teacher_and_course(db, seed_basic):
    """同一教师对同一课程只能有一条资格记录。"""
    course = seed_basic.courses["algorithm"]
    teacher = seed_basic.teachers["t1"]
    db.add(TeacherQualification(teacher_id=teacher.id, course_id=course.id))

    with pytest.raises(IntegrityError):
        db.flush()

    db.rollback()


def test_teacher_qualification_is_removed_with_its_teacher(db, seed_basic):
    """资格属于配置数据：教师被删除（无业务记录时）资格随之清理。"""
    teacher = seed_basic.teachers["t1"]
    db.delete(teacher)
    db.flush()

    remaining = db.scalars(
        select(TeacherQualification).where(TeacherQualification.teacher_id == teacher.id)
    ).all()
    assert remaining == []


def test_grade_change_keeps_before_and_after_values(db, seed_basic):
    """首次录入（前值为空）与清空成绩（新值为空）都要留下记录。"""
    student = seed_basic.students[0]
    offering = seed_basic.offerings["database"]
    teacher = seed_basic.teachers["t2"]
    db.add_all(
        [
            GradeChange(
                student_id=student.id,
                offering_id=offering.id,
                previous_value=None,
                new_value=GradeValue.A,
                changed_by_teacher_id=teacher.id,
            ),
            GradeChange(
                student_id=student.id,
                offering_id=offering.id,
                previous_value=GradeValue.A,
                new_value=None,
                changed_by_teacher_id=teacher.id,
            ),
        ]
    )
    db.commit()

    rows = db.scalars(
        select(GradeChange)
        .where(GradeChange.student_id == student.id, GradeChange.offering_id == offering.id)
        .order_by(GradeChange.id)
    ).all()
    assert [(row.previous_value, row.new_value) for row in rows] == [
        (None, GradeValue.A),
        (GradeValue.A, None),
    ]
    assert all(row.changed_at is not None for row in rows)


def test_grade_change_blocks_teacher_deletion(db, seed_basic):
    """成绩变更记录了操作教师，教务删除教师前必须检查该表。"""
    student = seed_basic.students[0]
    offering = seed_basic.offerings["database"]
    teacher = seed_basic.teachers["t2"]
    db.add(
        GradeChange(
            student_id=student.id,
            offering_id=offering.id,
            previous_value=None,
            new_value=GradeValue.B,
            changed_by_teacher_id=teacher.id,
        )
    )
    db.commit()

    with pytest.raises(IntegrityError):
        db.delete(teacher)
        db.flush()

    db.rollback()
