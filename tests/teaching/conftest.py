from collections.abc import Generator
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import (
    Course,
    Offering,
    OfferingSlot,
    Semester,
    SemesterStatus,
    Student,
    Teacher,
    TeacherQualification,
)


@pytest.fixture
def db() -> Generator[Session, None, None]:
    """成员 4 模块的内存数据库。

    显式打开 SQLite 外键约束，让 ``ondelete`` 行为在测试里真实生效，
    与 ``app/database.py`` 对 SQLite 连接的设置保持一致。
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    Base.metadata.drop_all(engine)


@pytest.fixture
def seed_basic(db: Session) -> SimpleNamespace:
    """教师、课程、学生、学期与班次的基础场景。

    - 开放学期 ``2026-FALL``，含三个班次：算法（无人认领）、数据库（t2 已认领）、
      网络（无人认领），其中算法与网络的上课时间冲突。
    - 教师 t1 具备算法资格，t2 具备数据库资格；t3 没有任何资格。
    """
    open_semester = Semester(
        code="2026-FALL", starts_on=date(2026, 9, 1), ends_on=date(2027, 1, 15)
    )
    closed_semester = Semester(
        code="2025-FALL",
        starts_on=date(2025, 9, 1),
        ends_on=date(2026, 1, 15),
        status=SemesterStatus.CLOSED,
    )
    algorithm = Course(code="CS201", name="算法设计", fee=Decimal("100.00"))
    database = Course(code="CS202", name="数据库系统", fee=Decimal("120.00"))
    network = Course(code="CS203", name="计算机网络", fee=Decimal("90.00"))
    t1 = Teacher(teacher_number="T20260001", name="杨欣怡", department="计算机学院")
    t2 = Teacher(teacher_number="T20260002", name="周嘉铭", department="软件学院")
    t3 = Teacher(teacher_number="T20260003", name="赵梦琪", department="计算机学院")
    students = [
        Student(student_number=f"2026000{index}", name=f"学生{index}") for index in range(1, 4)
    ]
    db.add_all([open_semester, closed_semester, algorithm, database, network, t1, t2, t3, *students])
    db.flush()

    offering_algorithm = Offering(
        semester_id=open_semester.id,
        course_id=algorithm.id,
        section_number="01",
        capacity=10,
    )
    offering_algorithm.slots.append(OfferingSlot(weekday=1, start_period=1, end_period=2))
    offering_database = Offering(
        semester_id=open_semester.id,
        course_id=database.id,
        teacher_id=t2.id,
        section_number="01",
        capacity=10,
    )
    offering_database.slots.append(OfferingSlot(weekday=2, start_period=3, end_period=4))
    offering_network = Offering(
        semester_id=open_semester.id,
        course_id=network.id,
        section_number="01",
        capacity=10,
    )
    offering_network.slots.append(OfferingSlot(weekday=1, start_period=1, end_period=2))
    db.add_all([offering_algorithm, offering_database, offering_network])
    db.add_all(
        [
            TeacherQualification(teacher_id=t1.id, course_id=algorithm.id),
            TeacherQualification(teacher_id=t2.id, course_id=database.id),
        ]
    )
    db.commit()
    return SimpleNamespace(
        semester=open_semester,
        closed_semester=closed_semester,
        courses={"algorithm": algorithm, "database": database, "network": network},
        teachers={"t1": t1, "t2": t2, "t3": t3},
        students=students,
        offerings={
            "algorithm": offering_algorithm,
            "database": offering_database,
            "network": offering_network,
        },
    )
