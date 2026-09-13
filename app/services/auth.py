### updated on 9.13 
### Achieve the basic capabilities of authentication, session management and authorization
from argon2 import PasswordHasher
password_hasher = PasswordHasher()
from argon2.exceptions import VerifyMismatchError
from app.models import Account, AccountRole, ServerSession, Student, Teacher


from fastapi import HTTPException
from sqlalchemy import select, update


import secrets

from datetime import datetime, timedelta, UTC
from sqlalchemy.orm import Session

###stage 1


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


###stage 2



def create_account(
    db: Session,
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
    # 写入但不提交：创建人员资料和账号时可由外层事务统一回滚。
    db.flush()

    return account

def authenticate_user(
    db: Session,
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



###stage 3

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
    *,
    allow_password_change: bool = False,
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
    # SQLite returns a naive datetime even when the Python value was UTC-aware.
    # Keep the comparison compatible with both SQLite and timezone-aware engines.
    now = datetime.now(UTC)
    if session.expires_at.tzinfo is None:
        now = now.replace(tzinfo=None)

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

    if account.role == AccountRole.STUDENT:
        subject = db.get(Student, account.subject_id)
    elif account.role == AccountRole.TEACHER:
        subject = db.get(Teacher, account.subject_id)
    else:
        subject = None

    if account.role in {AccountRole.STUDENT, AccountRole.TEACHER}:
        if subject is None:
            raise HTTPException(status_code=401, detail="关联人员不存在")
        if not subject.active:
            raise HTTPException(status_code=403, detail="关联人员已被禁用")

    if account.must_change_password and not allow_password_change:
        raise HTTPException(status_code=403, detail="首次登录必须先修改密码")

    return account

###stage 4

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
    invalidate_sessions(
        db,
        account.id,
    )
    db.commit()



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
