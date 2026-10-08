"""成绩变更记录查询与"上一已完成学期可录成绩班次"接口测试。"""

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.database import get_db
from app.errors import BusinessError
from app.models import (
    AccountRole,
    Enrollment,
    Offering,
    OfferingSlot,
    Schedule,
)
from app.routers import teaching as teaching_routes
from app.services.auth import create_account, create_session


def _client(db) -> TestClient:
    app = FastAPI()

    def override_get_db():
        yield db

    @app.exception_handler(BusinessError)
    async def business_error_handler(_request, exc: BusinessError):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

    app.dependency_overrides[get_db] = override_get_db
    app.include_router(teaching_routes.router)
    return TestClient(app)


def _session(db, login_number: str, role: AccountRole, subject_id: int):
    account = create_account(db, login_number, "Initial123", role, subject_id)
    account.must_change_password = False
    db.commit()
    return create_session(db, account)


def _teacher_session(db, seed_basic, key: str = "t1"):
    teacher = seed_basic.teachers[key]
    return _session(db, teacher.teacher_number, AccountRole.TEACHER, teacher.id)


@pytest.fixture
def completed_offering(db, seed_basic) -> Offering:
    """已完成学期里由 t1 承担、含两名正式学生的班次。"""
    offering = Offering(
        semester_id=seed_basic.closed_semester.id,
        course_id=seed_basic.courses["algorithm"].id,
        teacher_id=seed_basic.teachers["t1"].id,
        section_number="01",
        capacity=10,
    )
    offering.slots.append(OfferingSlot(weekday=1, start_period=1, end_period=2))
    db.add(offering)
    db.flush()
    for student in seed_basic.students[:2]:
        schedule = Schedule(
            student_id=student.id,
            semester_id=seed_basic.closed_semester.id,
            has_submitted=True,
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
    return offering


def _save(client, session, offering_id: int, student_id: int, value):
    return client.put(
        f"/api/v1/teacher/offerings/{offering_id}/grades",
        json={"grades": [{"student_id": student_id, "value": value}]},
        cookies={"session_id": session.id},
        headers={"X-CSRF-Token": session.csrf_token},
    )


def test_change_history_keeps_enter_modify_and_clear(db, seed_basic, completed_offering):
    """首次录入、修改与留空都会留下记录，且带变更前后值与操作教师。"""
    client = _client(db)
    session = _teacher_session(db, seed_basic, "t1")
    student = seed_basic.students[0]
    cookies = {"session_id": session.id}

    assert _save(client, session, completed_offering.id, student.id, "A").status_code == 200
    assert _save(client, session, completed_offering.id, student.id, "B").status_code == 200
    assert _save(client, session, completed_offering.id, student.id, None).status_code == 200

    response = client.get(
        f"/api/v1/teacher/offerings/{completed_offering.id}/grade-changes",
        cookies=cookies,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["offering_id"] == completed_offering.id
    assert body["course_code"] == "CS201"
    assert body["total"] == 3
    # 最新一条在最前：B -> 留空
    assert [(item["previous_value"], item["new_value"]) for item in body["changes"]] == [
        ("B", None),
        ("A", "B"),
        (None, "A"),
    ]
    first = body["changes"][0]
    assert first["student_number"] == "20260001"
    assert first["student_name"] == "学生1"
    assert first["changed_by"] == "杨欣怡"
    assert first["changed_by_teacher_id"] == seed_basic.teachers["t1"].id
    assert first["changed_at"] is not None


def test_change_history_filters_by_student(db, seed_basic, completed_offering):
    client = _client(db)
    session = _teacher_session(db, seed_basic, "t1")
    first, second = seed_basic.students[:2]
    _save(client, session, completed_offering.id, first.id, "A")
    _save(client, session, completed_offering.id, second.id, "C")

    filtered = client.get(
        f"/api/v1/teacher/offerings/{completed_offering.id}/grade-changes",
        params={"student_id": second.id},
        cookies={"session_id": session.id},
    )

    assert filtered.status_code == 200
    body = filtered.json()
    assert body["student_id"] == second.id
    assert body["total"] == 1
    assert body["changes"][0]["student_id"] == second.id
    assert body["changes"][0]["new_value"] == "C"


def test_change_history_is_limited_to_own_offering(db, seed_basic, completed_offering):
    client = _client(db)
    other_teacher = _teacher_session(db, seed_basic, "t2")
    student = _teacher_session(db, seed_basic, "t1")

    forbidden = client.get(
        f"/api/v1/teacher/offerings/{completed_offering.id}/grade-changes",
        cookies={"session_id": other_teacher.id},
    )
    missing = client.get(
        "/api/v1/teacher/offerings/9999/grade-changes",
        cookies={"session_id": student.id},
    )
    student_role = client.get(
        f"/api/v1/teacher/offerings/{completed_offering.id}/grade-changes",
        cookies={
            "session_id": _session(
                db,
                seed_basic.students[0].student_number,
                AccountRole.STUDENT,
                seed_basic.students[0].id,
            ).id
        },
    )

    assert forbidden.status_code == 403
    assert "自己承担班次" in forbidden.json()["detail"]
    assert missing.status_code == 404
    assert student_role.status_code == 403


def test_change_history_page_path_matches_rest_path(db, seed_basic, completed_offering):
    client = _client(db)
    session = _teacher_session(db, seed_basic, "t1")
    _save(client, session, completed_offering.id, seed_basic.students[0].id, "D")
    cookies = {"session_id": session.id}

    rest = client.get(
        f"/api/v1/teacher/offerings/{completed_offering.id}/grade-changes",
        cookies=cookies,
    )
    page = client.get(
        f"/api/v1/teacher/sections/{completed_offering.id}/grade-changes",
        cookies=cookies,
    )

    assert page.status_code == 200
    assert page.json() == rest.json()


def test_gradable_sections_use_latest_completed_semester(db, seed_basic, completed_offering):
    """成绩录入入口默认指向上一已完成学期，且只列出本人的班次。"""
    client = _client(db)
    owner = _teacher_session(db, seed_basic, "t1")
    other = _teacher_session(db, seed_basic, "t2")

    mine = client.get(
        "/api/v1/teacher/gradable-sections", cookies={"session_id": owner.id}
    )
    theirs = client.get(
        "/api/v1/teacher/gradable-sections", cookies={"session_id": other.id}
    )

    assert mine.status_code == 200
    body = mine.json()
    assert body["semester_id"] == seed_basic.closed_semester.id
    assert body["semester_code"] == "2025-FALL"
    assert [item["offering_id"] for item in body["offerings"]] == [completed_offering.id]
    assert body["offerings"][0]["enrolled_count"] == 2

    # 别的教师在已完成学期没有班次，返回空列表而不是报错
    assert theirs.status_code == 200
    assert theirs.json()["semester_id"] == seed_basic.closed_semester.id
    assert theirs.json()["offerings"] == []


def test_gradable_sections_without_completed_semester(db, seed_basic):
    client = _client(db)
    session = _teacher_session(db, seed_basic, "t1")
    db.delete(seed_basic.closed_semester)
    db.commit()

    response = client.get(
        "/api/v1/teacher/gradable-sections", cookies={"session_id": session.id}
    )

    assert response.status_code == 200
    assert response.json() == {"semester_id": None, "semester_code": None, "offerings": []}
