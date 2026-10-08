from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Account, AccountRole
from app.services.auth import verify_password
from scripts.init_registrar import initialize_registrar


def test_initialize_registrar_script_creates_and_reuses_registrar(db: Session):
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)

    first_id, first_login = initialize_registrar(
        "registrar",
        "Initial123",
        session_factory=factory,
    )
    second_id, second_login = initialize_registrar(
        "different-login",
        "Changed123",
        session_factory=factory,
    )

    account = db.scalar(
        select(Account).where(Account.role == AccountRole.REGISTRAR)
    )
    assert first_id == second_id == account.id
    assert first_login == second_login == "registrar"
    assert account.subject_id == account.id
    assert verify_password("Initial123", account.password_hash) is True
    assert verify_password("Changed123", account.password_hash) is False
