import os
import threading
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.database import Base
from app.errors import BusinessError
from app.models import Course, Enrollment, Offering, OfferingSlot, Semester, Student, Teacher
from app.schemas import DraftChoiceInput
from app.services.registration import save_draft, submit_schedule


MYSQL_TEST_URL = os.getenv("MYSQL_TEST_DATABASE_URL")


@pytest.mark.mysql
@pytest.mark.skipif(not MYSQL_TEST_URL, reason="未配置 MYSQL_TEST_DATABASE_URL")
def test_two_transactions_compete_for_last_seat():
    url = make_url(MYSQL_TEST_URL)
    if not (url.database or "").endswith("_test"):
        pytest.fail("并发测试只允许使用名称以 _test 结尾的独立数据库")
    engine = create_engine(MYSQL_TEST_URL, pool_pre_ping=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as db:
            semester = Semester(
                code="CONCURRENCY-TEST",
                starts_on=date(2026, 9, 1),
                ends_on=date(2027, 1, 15),
            )
            teacher = Teacher(teacher_number="T-CONCURRENT", name="并发测试教师")
            students = [
                Student(student_number="S-CONCURRENT-1", name="并发学生1"),
                Student(student_number="S-CONCURRENT-2", name="并发学生2"),
            ]
            courses = [
                Course(code=f"CC{i}", name=f"并发课程{i}", fee=Decimal("100.00"))
                for i in range(6)
            ]
            db.add_all([semester, teacher, *students, *courses])
            db.flush()
            offerings = []
            for index, course in enumerate(courses):
                offering = Offering(
                    semester_id=semester.id,
                    course_id=course.id,
                    teacher_id=teacher.id,
                    section_number="01",
                    capacity=1 if index == 0 else 10,
                )
                offering.slots.append(
                    OfferingSlot(weekday=index + 1, start_period=1, end_period=2)
                )
                offerings.append(offering)
            db.add_all(offerings)
            db.commit()
            semester_id = semester.id
            student_ids = [student.id for student in students]
            offering_ids = [offering.id for offering in offerings]

            request_choices = [
                *[
                    DraftChoiceInput(offering_id=offering_id, kind="primary")
                    for offering_id in offering_ids[:4]
                ],
                DraftChoiceInput(
                    offering_id=offering_ids[4], kind="alternate", alternate_priority=1
                ),
                DraftChoiceInput(
                    offering_id=offering_ids[5], kind="alternate", alternate_priority=2
                ),
            ]
            versions = {}
            for student_id in student_ids:
                schedule = save_draft(
                    db,
                    student_id=student_id,
                    semester_id=semester_id,
                    expected_version=1,
                    choices=request_choices,
                )
                versions[student_id] = schedule.version

        barrier = threading.Barrier(2)
        results = []
        result_lock = threading.Lock()

        def submit(student_id):
            with Session(engine, expire_on_commit=False) as worker_db:
                barrier.wait()
                try:
                    submit_schedule(
                        worker_db,
                        student_id=student_id,
                        semester_id=semester_id,
                        expected_version=versions[student_id],
                    )
                    outcome = "success"
                except BusinessError as exc:
                    worker_db.rollback()
                    outcome = exc.message
                with result_lock:
                    results.append(outcome)

        workers = [threading.Thread(target=submit, args=(student_id,)) for student_id in student_ids]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=15)
            assert not worker.is_alive()

        assert results.count("success") == 1
        assert sum("已满" in result for result in results) == 1
        with Session(engine) as db:
            count = db.scalar(
                select(func.count(Enrollment.id)).where(
                    Enrollment.offering_id == offering_ids[0]
                )
            )
            assert count == 1
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()

