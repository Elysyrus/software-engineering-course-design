"""真实双事务并发认领测试：需要名称以 _test 结尾的专用 MySQL 库。

未配置 MYSQL_TEST_DATABASE_URL 时自动跳过，与 tests/test_mysql_concurrency.py 一致。
"""

import os
import threading
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.database import Base
from app.errors import BusinessError
from app.models import (
    Course,
    Offering,
    OfferingSlot,
    Semester,
    Teacher,
    TeacherQualification,
)
from app.services.teaching import claim_offering


MYSQL_TEST_URL = os.getenv("MYSQL_TEST_DATABASE_URL")


@pytest.mark.mysql
@pytest.mark.skipif(not MYSQL_TEST_URL, reason="未配置 MYSQL_TEST_DATABASE_URL")
def test_two_teachers_compete_for_the_same_offering():
    url = make_url(MYSQL_TEST_URL)
    if not (url.database or "").endswith("_test"):
        pytest.fail("并发测试只允许使用名称以 _test 结尾的独立数据库")
    engine = create_engine(MYSQL_TEST_URL, pool_pre_ping=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as db:
            semester = Semester(
                code="CLAIM-CONCURRENCY",
                starts_on=date(2026, 9, 1),
                ends_on=date(2027, 1, 15),
            )
            course = Course(code="CLAIM101", name="并发认领课程", fee=Decimal("100.00"))
            teachers = [
                Teacher(teacher_number=f"T-CLAIM-{index}", name=f"并发教师{index}")
                for index in (1, 2)
            ]
            db.add_all([semester, course, *teachers])
            db.flush()
            offering = Offering(
                semester_id=semester.id,
                course_id=course.id,
                section_number="01",
                capacity=10,
            )
            offering.slots.append(OfferingSlot(weekday=1, start_period=1, end_period=2))
            db.add(offering)
            db.flush()
            db.add_all(
                [
                    TeacherQualification(teacher_id=teacher.id, course_id=course.id)
                    for teacher in teachers
                ]
            )
            db.commit()
            offering_id = offering.id
            teacher_ids = [teacher.id for teacher in teachers]

        barrier = threading.Barrier(2)
        results: list[str] = []
        result_lock = threading.Lock()

        def claim(teacher_id: int) -> None:
            with Session(engine, expire_on_commit=False) as worker_db:
                barrier.wait()
                try:
                    claim_offering(worker_db, teacher_id=teacher_id, offering_id=offering_id)
                    outcome = "success"
                except BusinessError as exc:
                    worker_db.rollback()
                    outcome = exc.message
                with result_lock:
                    results.append(outcome)

        workers = [threading.Thread(target=claim, args=(teacher_id,)) for teacher_id in teacher_ids]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=15)
            assert not worker.is_alive()

        assert results.count("success") == 1
        assert sum("其他教师" in result for result in results) == 1

        with Session(engine) as db:
            offering = db.get(Offering, offering_id)
            assert offering.teacher_id in teacher_ids
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
