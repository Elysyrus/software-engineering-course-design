from datetime import date

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth_contract import CurrentUser, current_user, require_registrar, require_student, require_teacher
from app.database import get_db
from app.models import Account, AccountRole, Student, Teacher
from app.services.auth import create_account, create_session, verify_password
from web.web_routes import web_router


def create_active_account(
    db: Session, login_number: str, role: AccountRole, subject_id: int, *, must_change_password: bool = False
):
    if role == AccountRole.STUDENT:
        db.add(Student(id=subject_id, student_number=login_number, name="测试学生"))
    elif role == AccountRole.TEACHER:
        db.add(Teacher(id=subject_id, teacher_number=login_number, name="测试教师", department="测试院系"))
    db.commit()
    account = create_account(db, login_number, "Initial123", role, subject_id)
    account.must_change_password = must_change_password
    db.commit()
    return account


def build_auth_app(db: Session) -> FastAPI:
    """最小 HTTP 应用：覆盖数据库依赖，验证认证依赖链而非业务模块。"""
    app = FastAPI()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db

    @app.get("/me")
    def me(user: CurrentUser = Depends(current_user)):
        return {"role": user.role, "subject_id": user.subject_id}

    @app.get("/student-only")
    def student_only(student_id: int = Depends(require_student)):
        return {"subject_id": student_id}

    @app.get("/teacher-only")
    def teacher_only(teacher_id: int = Depends(require_teacher)):
        return {"subject_id": teacher_id}

    @app.get("/registrar-only")
    def registrar_only(registrar_id: int = Depends(require_registrar)):
        return {"subject_id": registrar_id}

    return app


def build_login_app(db: Session) -> FastAPI:
    app = FastAPI()

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    app.include_router(web_router)
    return app


def test_cookie_session_is_converted_to_current_user(db: Session):
    account = create_active_account(db, "S20260002", AccountRole.STUDENT, 202)
    session = create_session(db, account)
    client = TestClient(build_auth_app(db))

    response = client.get("/me", cookies={"session_id": session.id})

    assert response.status_code == 200
    assert response.json() == {"role": "student", "subject_id": 202}


def test_missing_cookie_is_unauthenticated(db: Session):
    response = TestClient(build_auth_app(db)).get("/me")

    assert response.status_code == 401
    assert response.json()["detail"] == "未登录"


def test_role_guards_allow_only_the_matching_role(db: Session):
    student = create_active_account(db, "S20260003", AccountRole.STUDENT, 203)
    teacher = create_active_account(db, "T20260001", AccountRole.TEACHER, 301)
    registrar = create_active_account(db, "R20260001", AccountRole.REGISTRAR, 1)
    client = TestClient(build_auth_app(db))

    assert client.get("/student-only", cookies={"session_id": create_session(db, student).id}).status_code == 200
    assert client.get("/teacher-only", cookies={"session_id": create_session(db, student).id}).status_code == 403
    assert client.get("/teacher-only", cookies={"session_id": create_session(db, teacher).id}).status_code == 200
    assert client.get("/registrar-only", cookies={"session_id": create_session(db, registrar).id}).status_code == 200


def test_disabling_an_account_blocks_an_existing_cookie(db: Session):
    account = create_active_account(db, "S20260004", AccountRole.STUDENT, 204)
    session = create_session(db, account)
    account.active = False
    db.commit()

    response = TestClient(build_auth_app(db)).get("/me", cookies={"session_id": session.id})

    assert response.status_code == 403
    assert response.json()["detail"] == "账号已被禁用"


def test_disabling_the_linked_student_blocks_an_existing_cookie(db: Session):
    account = create_active_account(db, "S20260007", AccountRole.STUDENT, 207)
    session = create_session(db, account)
    student = db.get(Student, 207)
    student.active = False
    db.commit()

    response = TestClient(build_auth_app(db)).get("/me", cookies={"session_id": session.id})

    assert response.status_code == 403
    assert response.json()["detail"] == "关联人员已被禁用"


def test_first_login_cannot_use_regular_current_user(db: Session):
    account = create_active_account(
        db, "S20260005", AccountRole.STUDENT, 205, must_change_password=True
    )
    session = create_session(db, account)

    response = TestClient(build_auth_app(db)).get("/me", cookies={"session_id": session.id})

    assert response.status_code == 403
    assert response.json()["detail"] == "首次登录必须先修改密码"


def test_login_change_password_and_logout_routes_require_csrf(db: Session):
    create_active_account(
        db, "S20260006", AccountRole.STUDENT, 206, must_change_password=True
    )
    client = TestClient(build_login_app(db))

    login = client.post("/api/v1/auth/login", json={"username": "S20260006", "password": "Initial123"})
    assert login.status_code == 200
    assert login.json() == {"role": "student", "is_first_login": True}
    assert "session_id" in login.headers["set-cookie"]
    assert "HttpOnly" in login.headers["set-cookie"]

    missing_csrf = client.post(
        "/api/v1/auth/change-password",
        json={"old_password": "Initial123", "new_password": "Changed123"},
    )
    assert missing_csrf.status_code == 403

    csrf_token = client.cookies.get("csrf_token")
    changed = client.post(
        "/api/v1/auth/change-password",
        headers={"X-CSRF-Token": csrf_token},
        json={"old_password": "Initial123", "new_password": "Changed123"},
    )
    assert changed.status_code == 200
    assert client.cookies.get("session_id") is None

    relogin = client.post("/api/v1/auth/login", json={"username": "S20260006", "password": "Changed123"})
    assert relogin.status_code == 200
    logout_response = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": client.cookies.get("csrf_token")})
    assert logout_response.status_code == 200
    assert client.cookies.get("session_id") is None


def test_page_entries_require_real_login_and_matching_role(db: Session):
    student = create_active_account(db, "S20260008", AccountRole.STUDENT, 208)
    teacher = create_active_account(db, "T20260002", AccountRole.TEACHER, 302)
    registrar = create_active_account(db, "R20260002", AccountRole.REGISTRAR, 2)
    client = TestClient(build_login_app(db))

    assert client.get("/student/courses").status_code == 401
    assert client.get("/student/courses", cookies={"session_id": create_session(db, student).id}).status_code == 200
    assert client.get("/student/courses", cookies={"session_id": create_session(db, teacher).id}).status_code == 403
    assert client.get("/teacher/claim", cookies={"session_id": create_session(db, teacher).id}).status_code == 200
    assert client.get("/admin/users", cookies={"session_id": create_session(db, registrar).id}).status_code == 200


def test_first_login_can_open_change_password_page_only(db: Session):
    account = create_active_account(
        db, "S20260009", AccountRole.STUDENT, 209, must_change_password=True
    )
    session = create_session(db, account)
    client = TestClient(build_login_app(db))

    assert client.get("/change-password", cookies={"session_id": session.id}).status_code == 200
    blocked = client.get("/student/courses", cookies={"session_id": session.id})
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "首次登录必须先修改密码"


def test_registrar_can_create_student_and_teacher_via_admin_api(db: Session):
    registrar = create_active_account(db, "R20260003", AccountRole.REGISTRAR, 3)
    registrar_session = create_session(db, registrar)
    client = TestClient(build_login_app(db))
    auth = {"X-CSRF-Token": registrar_session.csrf_token}
    cookies = {"session_id": registrar_session.id}

    student = client.post(
        "/api/v1/admin/users",
        cookies=cookies,
        headers=auth,
        json={"full_name": "新学生", "role": "student"},
    )
    teacher = client.post(
        "/api/v1/admin/users",
        cookies=cookies,
        headers=auth,
        json={"full_name": "新教师", "role": "teacher", "department": "计算机学院"},
    )

    assert student.status_code == 201
    assert student.json()["login_number"].startswith(str(date.today().year))
    assert teacher.status_code == 201
    assert teacher.json()["login_number"].startswith(f"T{date.today().year}")

    users = client.get("/api/v1/admin/users", cookies=cookies)
    assert users.status_code == 200
    assert {item["role"] for item in users.json()} == {"student", "teacher"}


def test_admin_user_creation_requires_registrar_and_csrf(db: Session):
    student = create_active_account(db, "S20260010", AccountRole.STUDENT, 210)
    session = create_session(db, student)
    client = TestClient(build_login_app(db))
    body = {"full_name": "不可创建", "role": "student"}

    assert client.post("/api/v1/admin/users", json=body).status_code == 401
    assert client.post(
        "/api/v1/admin/users",
        cookies={"session_id": session.id},
        headers={"X-CSRF-Token": session.csrf_token},
        json=body,
    ).status_code == 403
    assert client.get("/api/v1/admin/users").status_code == 401


def test_registrar_can_manage_user_lifecycle_via_admin_api(db: Session):
    registrar = create_active_account(db, "R20260004", AccountRole.REGISTRAR, 4)
    registrar_session = create_session(db, registrar)
    client = TestClient(build_login_app(db))
    cookies = {"session_id": registrar_session.id}
    headers = {"X-CSRF-Token": registrar_session.csrf_token}

    created = client.post(
        "/api/v1/admin/users",
        cookies=cookies,
        headers=headers,
        json={"full_name": "待管理学生", "role": "student"},
    )
    account_id = created.json()["id"]

    detail = client.get(f"/api/v1/admin/users/{account_id}", cookies=cookies)
    assert detail.status_code == 200
    assert detail.json()["full_name"] == "待管理学生"

    updated = client.patch(
        f"/api/v1/admin/users/{account_id}",
        cookies=cookies,
        headers=headers,
        json={"full_name": "已更新学生"},
    )
    assert updated.status_code == 200
    assert updated.json()["full_name"] == "已更新学生"

    missing_csrf = client.patch(
        f"/api/v1/admin/users/{account_id}/status",
        cookies=cookies,
        json={"is_active": False},
    )
    assert missing_csrf.status_code == 403

    disabled = client.patch(
        f"/api/v1/admin/users/{account_id}/status",
        cookies=cookies,
        headers=headers,
        json={"is_active": False},
    )
    assert disabled.status_code == 200
    assert disabled.json()["is_active"] is False

    reset = client.post(
        f"/api/v1/admin/users/{account_id}/reset-password",
        cookies=cookies,
        headers=headers,
    )
    assert reset.status_code == 200
    account = db.get(Account, account_id)
    assert account.must_change_password is True
    assert verify_password("Initial123", account.password_hash) is True

    deleted = client.delete(
        f"/api/v1/admin/users/{account_id}",
        cookies=cookies,
        headers=headers,
    )
    assert deleted.status_code == 200
    assert deleted.json()["action"] == "deleted"
    assert client.get(f"/api/v1/admin/users/{account_id}", cookies=cookies).status_code == 404
