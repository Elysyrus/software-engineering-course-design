"""真实 MySQL：关闭忙碌、人员编号竞争、同一教师冲突认领。"""

import os
import threading
from datetime import date
from decimal import Decimal
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from app.database import Base
from app.models import (
    Course,
    Offering,
    OfferingSlot,
    Semester,
    Teacher,
    TeacherQualification,
)
from app.errors import BusinessError
from app.services.registration import close_registration, lock_semester
from app.services.teaching import claim_offering
from app.services.personnel import create_student

MYSQL_URL = os.getenv("MYSQL_TEST_DATABASE_URL")
pytestmark = [
    pytest.mark.mysql,
    pytest.mark.skipif(not MYSQL_URL, reason="未配置 MYSQL_TEST_DATABASE_URL"),
]


@pytest.fixture
def mysql_engine():
    if not (make_url(MYSQL_URL).database or "").endswith("_test"):
        pytest.fail("必须使用独立 _test 数据库")
    engine = create_engine(MYSQL_URL, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)
    engine.dispose()


def run_workers(function, args):
    gate = threading.Barrier(len(args))
    results = []
    errors = []

    def worker(arg):
        try:
            gate.wait(timeout=10)
            results.append(function(arg))
        except Exception as error:
            errors.append(error)

    workers = [threading.Thread(target=worker, args=(arg,)) for arg in args]
    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=20)
        assert not worker.is_alive()
    assert not errors, errors
    return results


def test_concurrent_personnel_creation_allocates_unique_numbers(mysql_engine):
    def create(index):
        with Session(mysql_engine, expire_on_commit=False) as db:
            return create_student(db, f"并发新增{index}", "Initial123").student_number

    result = run_workers(create, [1, 2, 3])
    assert len(set(result)) == 3


def test_close_refuses_active_registration_and_then_succeeds(mysql_engine):
    with Session(mysql_engine, expire_on_commit=False) as setup:
        term = Semester(
            code="BUSY", starts_on=date(2026, 9, 1), ends_on=date(2027, 1, 1)
        )
        setup.add(term)
        setup.commit()
        term_id = term.id
    with Session(mysql_engine) as active, Session(mysql_engine) as closing:
        lock_semester(active, term_id)
        with pytest.raises(BusinessError, match="稍后重试"):
            close_registration(closing, semester_id=term_id)
        active.rollback()
        assert close_registration(closing, semester_id=term_id) == ([], 0)


def test_same_teacher_cannot_concurrently_claim_conflicting_classes(mysql_engine):
    with Session(mysql_engine, expire_on_commit=False) as db:
        term = Semester(
            code="TEACHER-CONFLICT",
            starts_on=date(2026, 9, 1),
            ends_on=date(2027, 1, 1),
        )
        teacher = Teacher(teacher_number="RACE-T", name="并发教师")
        courses = [
            Course(code=f"RACE-C{i}", name=f"冲突课{i}", fee=Decimal("100"))
            for i in (1, 2)
        ]
        db.add_all([term, teacher, *courses])
        db.flush()
        offerings = []
        for course in courses:
            offering = Offering(
                semester_id=term.id,
                course_id=course.id,
                section_number="01",
                capacity=10,
            )
            offering.slots = [OfferingSlot(weekday=1, start_period=1, end_period=2)]
            db.add(offering)
            db.flush()
            offerings.append(offering)
            db.add(TeacherQualification(teacher_id=teacher.id, course_id=course.id))
        db.commit()
        teacher_id = teacher.id
        ids = [x.id for x in offerings]

    def claim(offering_id):
        with Session(mysql_engine) as db:
            try:
                claim_offering(db, teacher_id=teacher_id, offering_id=offering_id)
                return "success"
            except BusinessError as error:
                db.rollback()
                return error.message

    result = run_workers(claim, ids)
    assert result.count("success") == 1 and sum("冲突" in x for x in result) == 1
