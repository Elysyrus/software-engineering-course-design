"""任务 10：教师/成绩/账单真实接口的 HTTP 测试。

只挂载成员 4 的真实路由，不加载 web/web_routes.py 的模拟接口，
以证明页面切换到真实数据后拿到的是数据库里的结果。
"""

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
    BillingJob,
    Enrollment,
    Grade,
    GradeChange,
    Offering,
    OfferingSlot,
    Schedule,
)
from app.routers import billing as billing_routes
from app.routers import teaching as teaching_routes
from app.services.auth import create_account, create_session


def _client(db) -> TestClient:
    app = FastAPI()

    def override_get_db():
        yield db

    # 与 app/main.py 一致：业务异常统一转成 {"detail": "..."}
    @app.exception_handler(BusinessError)
    async def business_error_handler(_request, exc: BusinessError):
        return JSONResponse(
            status_code=exc.status_code, content={"detail": exc.message}
        )

    app.dependency_overrides[get_db] = override_get_db
    app.include_router(teaching_routes.router)
    app.include_router(billing_routes.router)
    return TestClient(app)


def _session(db, login_number: str, role: AccountRole, subject_id: int):
    account = create_account(db, login_number, "Initial123", role, subject_id)
    account.must_change_password = False
    db.commit()
    return create_session(db, account)


def _teacher_session(db, seed_basic, key: str = "t1"):
    teacher = seed_basic.teachers[key]
    return _session(db, teacher.teacher_number, AccountRole.TEACHER, teacher.id)


def _student_session(db, seed_basic, index: int = 0):
    student = seed_basic.students[index]
    return _session(db, student.student_number, AccountRole.STUDENT, student.id)


def _enrol(db, student, offering, semester):
    schedule = Schedule(
        student_id=student.id, semester_id=semester.id, has_submitted=True
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


@pytest.fixture
def completed_offering(db, seed_basic) -> Offering:
    """已完成学期里由 t1 承担的班次，含两名正式学生。"""
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
        _enrol(db, student, offering, seed_basic.closed_semester)
    db.commit()
    return offering


def test_endpoints_require_login(db, seed_basic):
    client = _client(db)

    assert client.get("/api/v1/semesters").status_code == 401
    assert client.get("/api/v1/teacher/me/qualified-courses").status_code == 401
    assert client.get("/api/v1/student/me/grades").status_code == 401
    assert client.get("/api/v1/registrar/billing/jobs").status_code == 401


def test_real_app_serves_page_paths_from_member4_router():
    """页面原先调用的模拟接口已替换为真实实现，不再由 web_routes 提供。"""
    from app.main import app
    from app.routers import teaching as teaching_routes
    from web import web_routes

    endpoints = {
        route.path: route.endpoint
        for route in app.routes
        if getattr(route, "endpoint", None) is not None
    }
    for path in (
        "/api/v1/teacher/claimable-sections",
        "/api/v1/teacher/my-sections",
        "/api/v1/teacher/sections/{section_id}/claim",
        "/api/v1/teacher/sections/{section_id}/unclaim",
        "/api/v1/teacher/sections/{section_id}/roster",
        "/api/v1/teacher/sections/{section_id}/grades",
        "/api/v1/student/grades",
    ):
        assert endpoints[path].__module__ == teaching_routes.__name__

    # 成员 2 页面侧的模拟函数已删除
    assert not hasattr(web_routes, "api_claimable_sections")
    assert not hasattr(web_routes, "api_my_sections")
    assert not hasattr(web_routes, "api_get_student_grades")
    assert not hasattr(web_routes, "api_submit_grades")

    # 学生选课相关的模拟接口不属于成员 4，保持原样
    assert hasattr(web_routes, "api_get_results")

    # 真实 REST 风格接口仍然保留
    assert "/api/v1/teacher/offerings/{offering_id}/claim" in endpoints
    assert "/api/v1/student/me/grades" in endpoints


def test_semester_list_uses_real_data(db, seed_basic):
    client = _client(db)
    session = _teacher_session(db, seed_basic)

    response = client.get("/api/v1/semesters", cookies={"session_id": session.id})

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, dict)
    codes = {item["code"]: item["status"] for item in body["semesters"]}
    assert codes == {"2025-FALL": "closed", "2026-FALL": "open"}


def test_claimable_offerings_default_to_open_semester(db, seed_basic):
    """省略 semester_id 时应回落到当前开放学期，而不是返回 422。"""
    client = _client(db)
    session = _teacher_session(db, seed_basic, "t1")
    cookies = {"session_id": session.id}

    without_param = client.get("/api/v1/teacher/offerings/claimable", cookies=cookies)
    with_param = client.get(
        f"/api/v1/teacher/offerings/claimable?semester_id={seed_basic.semester.id}",
        cookies=cookies,
    )

    assert without_param.status_code == 200
    body = without_param.json()
    assert body["semester_id"] == seed_basic.semester.id
    assert body["offerings"] == with_param.json()["offerings"]


def test_read_endpoints_accept_requests_without_query_parameters(db, seed_basic):
    """查询类接口的过滤参数都应是可选的，避免调用方漏传就拿到 422。"""
    client = _client(db)
    teacher_cookies = {"session_id": _teacher_session(db, seed_basic, "t1").id}
    student_cookies = {"session_id": _student_session(db, seed_basic).id}
    registrar_session = _session(db, "registrar", AccountRole.REGISTRAR, 0)
    registrar_cookies = {"session_id": registrar_session.id}

    for url, cookies in (
        ("/api/v1/semesters", teacher_cookies),
        ("/api/v1/teacher/me/qualified-courses", teacher_cookies),
        ("/api/v1/teacher/offerings/claimable", teacher_cookies),
        ("/api/v1/teacher/offerings/mine", teacher_cookies),
        ("/api/v1/teacher/claimable-sections", teacher_cookies),
        ("/api/v1/teacher/my-sections", teacher_cookies),
        ("/api/v1/student/me/grades", student_cookies),
        ("/api/v1/student/grades", student_cookies),
        ("/api/v1/registrar/billing/jobs", registrar_cookies),
    ):
        response = client.get(url, cookies=cookies)
        assert response.status_code == 200, f"{url} -> {response.status_code}"
        assert isinstance(response.json(), dict), f"{url} 必须返回 object"


def test_teacher_reads_qualifications_and_claimable_offerings(db, seed_basic):
    client = _client(db)
    session = _teacher_session(db, seed_basic, "t1")

    courses = client.get(
        "/api/v1/teacher/me/qualified-courses", cookies={"session_id": session.id}
    )
    claimable = client.get(
        f"/api/v1/teacher/offerings/claimable?semester_id={seed_basic.semester.id}",
        cookies={"session_id": session.id},
    )

    assert courses.status_code == 200
    assert [item["course_code"] for item in courses.json()["courses"]] == ["CS201"]
    assert claimable.status_code == 200
    assert [item["course_code"] for item in claimable.json()["offerings"]] == ["CS201"]


def test_student_cannot_call_teacher_endpoints(db, seed_basic):
    client = _client(db)
    session = _student_session(db, seed_basic)

    response = client.get(
        "/api/v1/teacher/me/qualified-courses", cookies={"session_id": session.id}
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "仅教师可访问"


def test_claim_without_csrf_token_is_rejected(db, seed_basic):
    client = _client(db)
    session = _teacher_session(db, seed_basic, "t1")
    algorithm = seed_basic.offerings["algorithm"]

    response = client.post(
        f"/api/v1/teacher/offerings/{algorithm.id}/claim",
        cookies={"session_id": session.id},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "缺少 CSRF 令牌"
    assert algorithm.teacher_id is None


def test_claim_and_release_through_api(db, seed_basic):
    client = _client(db)
    session = _teacher_session(db, seed_basic, "t1")
    algorithm = seed_basic.offerings["algorithm"]
    cookies = {"session_id": session.id}
    headers = {"X-CSRF-Token": session.csrf_token}

    claimed = client.post(
        f"/api/v1/teacher/offerings/{algorithm.id}/claim",
        cookies=cookies,
        headers=headers,
    )
    assert claimed.status_code == 200
    assert claimed.json()["teacher_id"] == seed_basic.teachers["t1"].id
    assert algorithm.teacher_id == seed_basic.teachers["t1"].id

    released = client.post(
        f"/api/v1/teacher/offerings/{algorithm.id}/release",
        cookies=cookies,
        headers=headers,
    )
    assert released.status_code == 200
    assert released.json()["teacher_id"] is None


def test_grade_update_requires_completed_semester(db, seed_basic):
    client = _client(db)
    session = _teacher_session(db, seed_basic, "t2")
    database = seed_basic.offerings["database"]

    response = client.put(
        f"/api/v1/teacher/offerings/{database.id}/grades",
        json={"grades": [{"student_id": seed_basic.students[0].id, "value": "A"}]},
        cookies={"session_id": session.id},
        headers={"X-CSRF-Token": session.csrf_token},
    )

    assert response.status_code == 409
    assert "已完成学期" in response.json()["detail"]


def test_grade_update_saves_grade_and_change_record(db, seed_basic, completed_offering):
    client = _client(db)
    session = _teacher_session(db, seed_basic, "t1")
    student = seed_basic.students[0]

    response = client.put(
        f"/api/v1/teacher/offerings/{completed_offering.id}/grades",
        json={"grades": [{"student_id": student.id, "value": "A"}]},
        cookies={"session_id": session.id},
        headers={"X-CSRF-Token": session.csrf_token},
    )

    assert response.status_code == 200
    assert response.json()["updated"] == 1
    assert db.scalar(select(func.count()).select_from(Grade)) == 1
    assert db.scalar(select(func.count()).select_from(GradeChange)) == 1

    sheet = client.get(
        f"/api/v1/teacher/offerings/{completed_offering.id}/grades",
        cookies={"session_id": session.id},
    )
    assert sheet.status_code == 200
    assert sheet.json()["editable"] is True
    assert sheet.json()["students"][0]["value"] == "A"


def test_student_reads_only_own_grades(db, seed_basic, completed_offering):
    client = _client(db)
    teacher_session = _teacher_session(db, seed_basic, "t1")
    student = seed_basic.students[0]
    client.put(
        f"/api/v1/teacher/offerings/{completed_offering.id}/grades",
        json={"grades": [{"student_id": student.id, "value": "B"}]},
        cookies={"session_id": teacher_session.id},
        headers={"X-CSRF-Token": teacher_session.csrf_token},
    )
    student_session = _student_session(db, seed_basic, 0)

    response = client.get(
        "/api/v1/student/me/grades", cookies={"session_id": student_session.id}
    )
    other = _client(db).get(
        "/api/v1/student/me/grades",
        cookies={"session_id": _student_session(db, seed_basic, 1).id},
    )

    assert response.status_code == 200
    assert [item["value"] for item in response.json()["grades"]] == ["B"]
    assert response.json()["grades"][0]["passed"] is True
    assert other.json()["grades"] == []


def test_billing_endpoints_require_registrar(db, seed_basic):
    client = _client(db)
    teacher_session = _teacher_session(db, seed_basic, "t1")

    forbidden = client.get(
        "/api/v1/registrar/billing/jobs", cookies={"session_id": teacher_session.id}
    )
    assert forbidden.status_code == 403


def test_billing_dispatch_flow_through_api(db, seed_basic, monkeypatch):
    """派发接口用假的发送器替换真实 HTTP 调用，避免测试依赖计费服务进程。"""
    from app.services import billing as billing_service

    sent: list[int] = []
    monkeypatch.setattr(billing_service, "http_sender", lambda job: sent.append(job.id))
    client = _client(db)
    registrar_session = _session(db, "registrar", AccountRole.REGISTRAR, 0)
    job = BillingJob(
        semester_id=seed_basic.closed_semester.id,
        student_id=seed_basic.students[0].id,
        amount=Decimal("120.00"),
        schedule_snapshot="[]",
    )
    db.add(job)
    db.commit()
    cookies = {"session_id": registrar_session.id}

    listed = client.get("/api/v1/registrar/billing/jobs", cookies=cookies)
    assert listed.status_code == 200
    assert [item["status"] for item in listed.json()["jobs"]] == ["pending"]

    dispatched = client.post(
        "/api/v1/registrar/billing/dispatch",
        cookies=cookies,
        headers={"X-CSRF-Token": registrar_session.csrf_token},
    )

    assert dispatched.status_code == 200
    assert dispatched.json()["sent"] == 1
    assert sent == [job.id]
    assert job.status.value == "sent"
