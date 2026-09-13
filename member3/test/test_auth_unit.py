from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import AccountRole, ServerSession
from app.services.auth import (
    authenticate_user,
    change_password,
    create_account,
    create_session,
    get_session_account,
    hash_password,
    validate_csrf,
    validate_password,
    verify_password,
)


def create_student_account(db: Session):
    return create_account(
        db,
        login_number="S20260001",
        password="Initial123",
        role=AccountRole.STUDENT,
        subject_id=101,
    )


@pytest.mark.parametrize("password", ["short1A", "12345678", "abcdefgh"])
def test_validate_password_rejects_weak_passwords(password: str):
    with pytest.raises(ValueError):
        validate_password(password)


def test_hash_password_never_stores_plaintext_and_verifies():
    password_hash = hash_password("Secure123")

    assert password_hash != "Secure123"
    assert verify_password("Secure123", password_hash) is True
    assert verify_password("Wrong123", password_hash) is False


def test_create_account_and_authenticate(db: Session):
    account = create_student_account(db)

    assert account.password_hash != "Initial123"
    assert account.must_change_password is True
    assert authenticate_user(db, "S20260001", "Initial123").id == account.id


def test_duplicate_login_and_bad_credentials_are_rejected(db: Session):
    create_student_account(db)

    with pytest.raises(HTTPException, match="账号已存在") as duplicate:
        create_student_account(db)
    assert duplicate.value.status_code == 409

    with pytest.raises(HTTPException, match="账号或密码错误") as bad_password:
        authenticate_user(db, "S20260001", "Wrong123")
    assert bad_password.value.status_code == 401


def test_disabled_account_cannot_authenticate(db: Session):
    account = create_student_account(db)
    account.active = False
    db.commit()

    with pytest.raises(HTTPException, match="账号已被禁用") as error:
        authenticate_user(db, account.login_number, "Initial123")
    assert error.value.status_code == 403


def test_session_resolves_account_and_rejects_expired_session(db: Session):
    account = create_student_account(db)
    session = create_session(db, account)

    assert get_session_account(db, session.id).id == account.id

    session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    # SQLite 取回 DateTime 时为无时区对象，模拟实际认证请求的读取路径。
    db.refresh(session)
    with pytest.raises(HTTPException, match="登录状态已过期") as error:
        get_session_account(db, session.id)
    assert error.value.status_code == 401


def test_change_password_revokes_all_existing_sessions(db: Session):
    account = create_student_account(db)
    first = create_session(db, account)
    second = create_session(db, account)

    change_password(db, account, "Initial123", "Changed123")
    db.refresh(account)
    db.refresh(first)
    db.refresh(second)

    assert account.must_change_password is False
    assert verify_password("Changed123", account.password_hash) is True
    assert first.revoked_at is not None
    assert second.revoked_at is not None


def test_validate_csrf_requires_the_matching_session_token(db: Session):
    account = create_student_account(db)
    session: ServerSession = create_session(db, account)

    validate_csrf(session, session.csrf_token)
    for token in (None, "forged-token"):
        with pytest.raises(HTTPException) as error:
            validate_csrf(session, token)
        assert error.value.status_code == 403
