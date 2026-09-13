### updated on 9.13 
### Achieve the basic capabilities of authentication, session management and authorization
from argon2 import PasswordHasher
password_hasher = PasswordHasher()
from argon2.exceptions import VerifyMismatchError
from app.models import Account, AccountRole, ServerSession


from fastapi import HTTPException
from sqlalchemy import select


from sqlalchemy import select, update


import secrets

from datetime import datetime, timedelta, UTC
from sqlalchemy.orm import Session

###阶段一


def validate_password(password: str) -> None:
    if len(password) < 8:
        raise ValueError("密码至少需要 8 位")

    if not any(char.isalpha() for char in password):
        raise ValueError("密码至少需要包含一个字母")

    if not any(char.isdigit() for char in password):
        raise ValueError("密码至少需要包含一个数字")

def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


###阶段二



def create_account(
    db: ServerSession,
    login_number: str,
    password: str,
    role: AccountRole,
    subject_id: int,
) -> Account:
    existing = db.scalar(
        select(Account).where(
            Account.login_number == login_number
        )
    )

    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail="账号已存在",
        )

    validate_password(password)

    account = Account(
        login_number=login_number,
        role=role,
        subject_id=subject_id,
        password_hash=hash_password(password),
    )

    db.add(account)
    db.commit()
    db.refresh(account) #reread accounts.id

    return account

def authenticate_user(
    db: ServerSession,
    login_number: str,
    password: str,
) -> Account:
    account = db.scalar(
        select(Account).where(
            Account.login_number == login_number
        )
    )

    if account is None:
        raise HTTPException(
            status_code=401,
            detail="账号或密码错误",
        )

    if not account.active:
        raise HTTPException(
            status_code=403,
            detail="账号已被禁用",
        )

    if not verify_password(
        password,
        account.password_hash,
    ):
        raise HTTPException(
            status_code=401,
            detail="账号或密码错误",
        )

    return account



###阶段三

def create_session(
    db: Session,
    account: Account,
) -> ServerSession:
    now = datetime.now(UTC)

    session = ServerSession(
        id=secrets.token_urlsafe(32),
        account_id=account.id,
        csrf_token=secrets.token_urlsafe(32),
        created_at=now,
        expires_at=now + timedelta(hours=8),
        revoked_at=None,
    )

    db.add(session)
    db.commit()
    db.refresh(session)

    return session

def get_session_account(
    db: Session,
    session_id: str,
) -> Account:

    session = db.get(ServerSession, session_id)

    if session is None:
        raise HTTPException(
            status_code=401,
            detail="登录状态无效",
        )

    if session.revoked_at is not None:
        raise HTTPException(
            status_code=401,
            detail="登录状态已失效",
        )
    now = datetime.now(UTC).replace(tzinfo=None)

    if session.expires_at <= now:
        raise HTTPException(
            status_code=401,
            detail="登录状态已过期",
        )

    account = db.get(Account, session.account_id)

    if account is None:
        raise HTTPException(
            status_code=401,
            detail="账号不存在",
        )

    if not account.active:
        raise HTTPException(
            status_code=403,
            detail="账号已被禁用",
        )

    return account

###阶段四

def invalidate_sessions(
    db: Session,
    account_id: int,
) -> None:
    now = datetime.now(UTC)

    db.execute(
        update(ServerSession)
        .where(
            ServerSession.account_id == account_id,
            ServerSession.revoked_at.is_(None),
        )
        .values(revoked_at=now)
    )

    db.commit()


def change_password(
    db: Session,
    account: Account,
    old_password: str,
    new_password: str,
) -> None:

    if not verify_password(
        old_password,
        account.password_hash,
    ):
        raise HTTPException(
            status_code=400,
            detail="原密码错误",
        )

    validate_password(new_password)

    account.password_hash = hash_password(new_password)
    account.must_change_password = False

    db.add(account)
    db.commit()

    invalidate_sessions(
        db,
        account.id,
    )



def logout(
    db: Session,
    session_id: str,
) -> None:

    session = db.get(
        ServerSession,
        session_id,
    )

    if session is None:
        return

    if session.revoked_at is None:
        session.revoked_at = datetime.now(UTC)
        db.add(session)
        db.commit()

def validate_csrf(
    session: ServerSession,
    csrf_token: str | None,
) -> None:

    if csrf_token is None:
        raise HTTPException(
            status_code=403,
            detail="缺少 CSRF 令牌",
        )

    if not secrets.compare_digest(
        session.csrf_token,
        csrf_token,
    ):
        raise HTTPException(
            status_code=403,
            detail="CSRF 令牌无效",
        )