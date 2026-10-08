"""页面接口测试：成员 2 模板原先调用的模拟接口现已改为真实实现。"""

from decimal import Decimal

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.database import get_db
from app.errors import BusinessError
from app.models import (
    AccountRole,
    Enrollment,
    Grade,
    GradeChange,
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


def _teacher(db, seed_basic, key: str = "t1"):
    teacher = seed_basic.teachers[key]
    return _session(db, teacher.teacher_number, AccountRole.TEACHER, teacher.id)


def _student(db, seed_basic, index: int = 0):
    student = seed_basic.students[index]
    return _session(db, student.student_number, AccountRole.STUDENT, student.id)


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


def test_claimable_sections_use_default_open_semester(db, seed_basic):
    """页面不带学期参数调用，接口回落到当前开放学期并返回真实班次。"""
    client = _client(db)
    session = _teacher(db, seed_basic, "t1")

    response = client.get(
        "/api/v1/teacher/claimable-sections", cookies={"session_id": session.id}
    )

    assert response.status_code == 200
    body = response.json()
    offerings = body["offerings"]
    assert body["semester_id"] == seed_basic.semester.id
    assert [item["course_code"] for item in offerings] == ["CS201"]
    assert offerings[0]["id"] == seed_basic.offerings["algorithm"].id
    assert offerings[0]["teacher_id"] is None
    assert offerings[0]["is_mine"] is False
    assert offerings[0]["capacity"] == 10
    assert offerings[0]["schedule_time"] == "周一 1-2节"
    assert Decimal(offerings[0]["fee"]) == Decimal("100.00")
    # 学分与教室在真实模型里不存在，已从响应与页面中移除
    assert "credits" not in offerings[0]
    assert "classroom" not in offerings[0]


def test_my_sections_returns_real_offerings_with_semester(db, seed_basic):
    client = _client(db)
    session = _teacher(db, seed_basic, "t2")

    response = client.get(
        "/api/v1/teacher/my-sections", cookies={"session_id": session.id}
    )

    assert response.status_code == 200
    offerings = response.json()["offerings"]
    assert [item["id"] for item in offerings] == [seed_basic.offerings["database"].id]
    assert offerings[0]["course_code"] == "CS202"
    assert offerings[0]["semester_code"] == "2026-FALL"
    assert offerings[0]["is_mine"] is True


def test_claim_and_unclaim_page_endpoints(db, seed_basic):
    client = _client(db)
    session = _teacher(db, seed_basic, "t1")
    algorithm = seed_basic.offerings["algorithm"]
    cookies = {"session_id": session.id}
    headers = {"X-CSRF-Token": session.csrf_token}

    without_csrf = client.post(
        f"/api/v1/teacher/sections/{algorithm.id}/claim", cookies=cookies
    )
    assert without_csrf.status_code == 403

    claimed = client.post(
        f"/api/v1/teacher/sections/{algorithm.id}/claim",
        cookies=cookies,
        headers=headers,
    )
    assert claimed.status_code == 200
    assert claimed.json()["section_id"] == algorithm.id
    assert algorithm.teacher_id == seed_basic.teachers["t1"].id

    unclaimed = client.post(
        f"/api/v1/teacher/sections/{algorithm.id}/unclaim",
        cookies=cookies,
        headers=headers,
    )
    assert unclaimed.status_code == 200
    assert algorithm.teacher_id is None


def test_roster_page_endpoint_returns_real_students(db, seed_basic, completed_offering):
    client = _client(db)
    session = _teacher(db, seed_basic, "t1")

    response = client.get(
        f"/api/v1/teacher/sections/{completed_offering.id}/roster",
        cookies={"session_id": session.id},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["offering_id"] == completed_offering.id
    assert [item["student_number"] for item in body["students"]] == [
        "20260001",
        "20260002",
    ]
    assert body["students"][0]["full_name"] == "学生1"
    assert body["students"][0]["enrolled_at"] is not None


def test_grade_page_endpoint_accepts_letter_grades(db, seed_basic, completed_offering):
    client = _client(db)
    session = _teacher(db, seed_basic, "t1")
    first, second = seed_basic.students[:2]
    cookies = {"session_id": session.id}
    headers = {"X-CSRF-Token": session.csrf_token}

    saved = client.post(
        f"/api/v1/teacher/sections/{completed_offering.id}/grades",
        json={
            "grades": [
                {"student_id": first.id, "value": "A"},
                {"student_id": second.id, "value": None},
            ]
        },
        cookies=cookies,
        headers=headers,
    )

    assert saved.status_code == 200
    assert saved.json()["updated"] == 1
    assert db.scalar(select(func.count()).select_from(Grade)) == 1
    assert db.scalar(select(func.count()).select_from(GradeChange)) == 1

    sheet = client.get(
        f"/api/v1/teacher/sections/{completed_offering.id}/grades", cookies=cookies
    )
    assert sheet.status_code == 200
    sheet_body = sheet.json()
    assert sheet_body["offering_id"] == completed_offering.id
    assert sheet_body["editable"] is True
    rows = sheet_body["students"]
    assert [item["value"] for item in rows] == ["A", None]
    assert rows[0]["updated_by"] == "杨欣怡"
    assert rows[0]["updated_at"] is not None


def test_grade_page_endpoint_rejects_legacy_score_payload(db, seed_basic, completed_offering):
    """旧版模拟接口用 score(0-100)，直接放行会被当成留空而清掉成绩，必须拒绝。"""
    client = _client(db)
    session = _teacher(db, seed_basic, "t1")

    response = client.post(
        f"/api/v1/teacher/sections/{completed_offering.id}/grades",
        json={"grades": [{"student_id": seed_basic.students[0].id, "score": 92}]},
        cookies={"session_id": session.id},
        headers={"X-CSRF-Token": session.csrf_token},
    )

    assert response.status_code == 422
    assert db.scalar(select(func.count()).select_from(Grade)) == 0


def test_student_grades_page_endpoint_returns_letter_grades(
    db, seed_basic, completed_offering
):
    client = _client(db)
    teacher_session = _teacher(db, seed_basic, "t1")
    client.post(
        f"/api/v1/teacher/sections/{completed_offering.id}/grades",
        json={"grades": [{"student_id": seed_basic.students[0].id, "value": "B"}]},
        cookies={"session_id": teacher_session.id},
        headers={"X-CSRF-Token": teacher_session.csrf_token},
    )
    student_session = _student(db, seed_basic, 0)

    response = client.get(
        "/api/v1/student/grades", cookies={"session_id": student_session.id}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total_count"] == 1
    assert body["passed_count"] == 1
    assert body["items"][0]["semester"] == "2025-FALL"
    assert body["items"][0]["course_code"] == "CS201"
    assert body["items"][0]["value"] == "B"
    assert body["items"][0]["is_passed"] is True
    # 学分与 GPA 在真实模型里不存在
    assert "credits" not in body["items"][0]
    assert "gpa_point" not in body["items"][0]


def test_real_pages_render_after_template_changes(db, seed_basic):
    """模板改动后三个教师页与学生成绩页仍能正常渲染（模板语法错误会变成 500）。"""
    from web.web_routes import web_router

    app = FastAPI()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    app.include_router(web_router)
    client = TestClient(app)
    teacher_cookies = {"session_id": _teacher(db, seed_basic, "t1").id}
    student_cookies = {"session_id": _student(db, seed_basic, 0).id}

    for path in ("/teacher/claim", "/teacher/roster", "/teacher/grades"):
        response = client.get(path, cookies=teacher_cookies)
        assert response.status_code == 200, path
    assert client.get("/student/grades", cookies=student_cookies).status_code == 200
