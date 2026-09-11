from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models import Course, Offering, OfferingSlot, Semester, Student, Teacher


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    Base.metadata.drop_all(engine)


@pytest.fixture
def seeded(db: Session):
    semester = Semester(
        code="2026-FALL", starts_on=date(2026, 9, 1), ends_on=date(2027, 1, 15)
    )
    teacher = Teacher(teacher_number="T001", name="张老师", department="计算机")
    students = [
        Student(student_number=f"S{i:03d}", name=f"学生{i}") for i in range(1, 15)
    ]
    courses = [
        Course(code=f"CS{i:03d}", name=f"课程{i}", fee=Decimal("1000.00"))
        for i in range(1, 9)
    ]
    db.add_all([semester, teacher, *students, *courses])
    db.flush()
    offerings = []
    for index, course in enumerate(courses, start=1):
        offering = Offering(
            semester_id=semester.id,
            course_id=course.id,
            teacher_id=teacher.id,
            section_number="01",
            capacity=10,
        )
        offering.slots.append(
            OfferingSlot(weekday=((index - 1) % 5) + 1, start_period=index * 2 - 1, end_period=index * 2)
        )
        offerings.append(offering)
    db.add_all(offerings)
    db.commit()
    return {
        "semester": semester,
        "teacher": teacher,
        "students": students,
        "courses": courses,
        "offerings": offerings,
    }

