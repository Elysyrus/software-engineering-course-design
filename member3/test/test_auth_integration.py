from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.auth_contract import CurrentUser, current_user, require_registrar, require_student, require_teacher
from app.database import get_db
from app.models import AccountRole
from app.services.auth import create_account, create_session


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


def test_cookie_session_is_converted_to_current_user(db: Session):
    account = create_account(db, "S20260002", "Initial123", AccountRole.STUDENT, 202)
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
    student = create_account(db, "S20260003", "Initial123", AccountRole.STUDENT, 203)
    teacher = create_account(db, "T20260001", "Initial123", AccountRole.TEACHER, 301)
    registrar = create_account(db, "R20260001", "Initial123", AccountRole.REGISTRAR, 1)
    client = TestClient(build_auth_app(db))

    assert client.get("/student-only", cookies={"session_id": create_session(db, student).id}).status_code == 200
    assert client.get("/teacher-only", cookies={"session_id": create_session(db, student).id}).status_code == 403
    assert client.get("/teacher-only", cookies={"session_id": create_session(db, teacher).id}).status_code == 200
    assert client.get("/registrar-only", cookies={"session_id": create_session(db, registrar).id}).status_code == 200


def test_disabling_an_account_blocks_an_existing_cookie(db: Session):
    account = create_account(db, "S20260004", "Initial123", AccountRole.STUDENT, 204)
    session = create_session(db, account)
    account.active = False
    db.commit()

    response = TestClient(build_auth_app(db)).get("/me", cookies={"session_id": session.id})

    assert response.status_code == 403
    assert response.json()["detail"] == "账号已被禁用"

