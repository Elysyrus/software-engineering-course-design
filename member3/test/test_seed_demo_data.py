from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Account, AccountRole, Course, Student, Teacher
from app.services.auth import verify_password
from scripts.seed_demo_data import seed_demo_data


def test_seed_demo_data_creates_records_once_and_reuses_them(db: Session):
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)

    first = seed_demo_data("Registrar123", session_factory=factory)
    second = seed_demo_data("OtherPassword123", session_factory=factory)

    assert first == {"registrar": 1, "students": 3, "teachers": 2, "courses": 3}
    assert second == {"registrar": 0, "students": 0, "teachers": 0, "courses": 0}
    assert db.scalar(select(func.count()).select_from(Student)) == 3
    assert db.scalar(select(func.count()).select_from(Teacher)) == 2
    assert db.scalar(select(func.count()).select_from(Course)) == 3

    registrar = db.scalar(select(Account).where(Account.role == AccountRole.REGISTRAR))
    assert registrar.login_number == "registrar"
    assert verify_password("Registrar123", registrar.password_hash) is True

    demo_student = db.scalar(select(Account).where(Account.role == AccountRole.STUDENT))
    assert demo_student is not None
    assert demo_student.must_change_password is True
    assert verify_password("Initial123", demo_student.password_hash) is True
