"""最终版真实页面契约、权限、持久化结果与已发现缺陷的回归测试。"""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.database import get_db
from app.main import app
from app.models import (
    AccountRole,
    BillingJob,
    BillingStatus,
    Enrollment,
    GradeChange,
    OfferingStatus,
    Semester,
    SemesterStatus,
)
from app.services.auth import create_account, create_session
from app.services import billing, catalog, personnel, registration, teaching
from app.services.catalog import CatalogSnapshot
from app.schemas import DraftChoiceInput
from mock_billing.mock_billing_service import ChargeRequest
from mock_billing.persistent_store import PersistentBillingStore


@pytest.fixture
def clients(db, seeded, monkeypatch):
    def database():
        yield db

    app.dependency_overrides[get_db] = database
    snapshot = CatalogSnapshot(
        semester_code=seeded["semester"].code,
        starts_on=seeded["semester"].starts_on,
        ends_on=seeded["semester"].ends_on,
        courses=[
            dict(
                code=c.code,
                name=c.name,
                fee=c.fee,
                sections=[
                    dict(
                        section_number="01",
                        slots=[dict(weekday=1, start_period=1, end_period=2)],
                    )
                ],
            )
            for c in seeded["courses"]
        ],
    )
    monkeypatch.setattr(catalog, "fetch_catalog", lambda semester_code=None: snapshot)
    result = {}
    for role, subject, name in [
        ("student", seeded["students"][0].id, "user1"),
        ("student", seeded["students"][1].id, "user2"),
        ("registrar", 0, "admin"),
    ]:
        account = create_account(db, name, "Strong123", AccountRole(role), subject)
        account.must_change_password = False
        db.commit()
        session = create_session(db, account)
        client = TestClient(app)
        client.cookies.set("session_id", session.id)
        client.headers["X-CSRF-Token"] = session.csrf_token
        result[name] = client
    yield result
    for client in result.values():
        client.close()
    app.dependency_overrides.clear()


def choices(seeded):
    return [dict(offering_id=x.id, kind="primary") for x in seeded["offerings"][:4]] + [
        dict(offering_id=x.id, kind="alternate", alternate_priority=i)
        for i, x in enumerate(seeded["offerings"][4:6], 1)
    ]


def submit(client, term, selections):
    result = client.put(
        f"/semesters/{term}/schedule/draft",
        json={"expected_version": 1, "choices": selections},
    )
    assert result.status_code == 200, result.text
    response = client.post(
        f"/semesters/{term}/schedule/submit",
        json={"expected_version": result.json()["version"]},
    )
    assert response.status_code == 200, response.text
    return response.json()["version"]


def test_students_persist_choices_and_keep_accounts_isolated(db, seeded, clients):
    client = clients["user1"]
    term = seeded["semester"].id
    response = client.get(f"/api/v1/student/available-sections?semester_id={term}")
    assert response.status_code == 200 and len(response.json()["sections"]) == 8
    version = submit(client, term, choices(seeded))
    view = client.get(f"/api/v1/student/draft?semester_id={term}").json()
    assert len(view["results"]) == 4 and len(view["alternates"]) == 2
    assert (
        clients["user2"]
        .get(f"/api/v1/student/draft?semester_id={term}")
        .json()["items"]
        == []
    )
    schedule = db.get(registration.Schedule, view["schedule_id"])
    moment = schedule.last_submitted_at
    repeat = client.post(
        f"/semesters/{term}/schedule/submit", json={"expected_version": version}
    )
    assert repeat.status_code == 200 and repeat.json()["version"] == version
    assert schedule.last_submitted_at == moment
    drop = client.request(
        "DELETE",
        f'/semesters/{term}/schedule/offerings/{seeded["offerings"][0].id}',
        json={"expected_version": version},
    )
    assert drop.status_code == 200 and db.scalar(select(func.count(Enrollment.id))) == 3
    stale = client.put(
        f"/semesters/{term}/schedule/draft",
        json={"expected_version": version, "choices": choices(seeded)},
    )
    assert stale.status_code == 409
    delete = client.request(
        "DELETE",
        f"/semesters/{term}/schedule",
        json={"expected_version": drop.json()["version"]},
    )
    assert (
        delete.status_code == 200 and db.scalar(select(func.count(Enrollment.id))) == 0
    )
    assert (
        client.get(f"/api/v1/student/draft?semester_id={term}").json()["is_deleted"]
        is True
    )


def test_first_submit_shape_and_csrf_on_every_core_write(seeded, clients):
    term = seeded["semester"].id
    client = clients["user1"]
    selections = choices(seeded)
    saved = client.put(
        f"/semesters/{term}/schedule/draft",
        json={"expected_version": 1, "choices": selections[:4]},
    ).json()
    assert (
        client.post(
            f"/semesters/{term}/schedule/submit",
            json={"expected_version": saved["version"]},
        ).status_code
        == 422
    )
    token = client.headers.pop("X-CSRF-Token")
    for method, path, body in [
        (
            "PUT",
            f"/semesters/{term}/schedule/draft",
            {"expected_version": 2, "choices": []},
        ),
        ("POST", f"/semesters/{term}/schedule/submit", {"expected_version": 2}),
        ("DELETE", f"/semesters/{term}/schedule", {"expected_version": 2}),
        ("DELETE", f"/semesters/{term}/schedule/offerings/1", {"expected_version": 2}),
    ]:
        assert client.request(method, path, json=body).status_code == 403
    client.headers["X-CSRF-Token"] = "wrong"
    assert (
        client.put(
            f"/semesters/{term}/schedule/draft",
            json={"expected_version": 2, "choices": []},
        ).status_code
        == 403
    )
    client.headers["X-CSRF-Token"] = token
    anonymous = TestClient(app)
    assert anonymous.get("/api/v1/student/draft").status_code == 401
    assert anonymous.get("/api/v1/admin/registration/closing-status").status_code == 401
    assert (
        anonymous.post(
            "/api/v1/admin/registration/close",
            json={"semester_id": term, "confirmation": "CLOSE-CONFIRM"},
        ).status_code
        == 401
    )
    assert client.get("/api/v1/admin/registration/closing-status").status_code == 403


def test_real_close_summary_and_failed_bills_recover(db, seeded, clients, tmp_path):
    term = seeded["semester"].id
    for student in seeded["students"][:3]:
        saved = registration.save_draft(
            db,
            student_id=student.id,
            semester_id=term,
            expected_version=1,
            choices=[DraftChoiceInput(**x) for x in choices(seeded)],
        )
        registration.submit_schedule(
            db, student_id=student.id, semester_id=term, expected_version=saved.version
        )
    admin = clients["admin"]
    response = admin.post(
        "/api/v1/admin/registration/close",
        json={"semester_id": term, "confirmation": "CLOSE-CONFIRM"},
    )
    assert response.status_code == 200 and response.json()["billing_jobs_created"] == 3
    summary = admin.get(
        f"/api/v1/admin/registration/closing-status?semester_id={term}"
    ).json()
    assert (
        summary["opened_sections_count"] == 4
        and summary["cancelled_sections_count"] == 4
        and summary["jobs_total"] == 3
    )
    assert summary["billing_status"] == "PENDING"
    moment = datetime.now(UTC)

    def offline(job):
        raise billing.BillingSendError("模拟断线")

    assert billing.dispatch_pending_jobs(db, sender=offline, now=moment)["failed"] == 3
    store = PersistentBillingStore(tmp_path / "billing.db")

    def send(job):
        outcome, _ = store.receive(ChargeRequest(**billing.build_payload(job)))
        assert outcome in ("created", "duplicate")

    assert (
        billing.dispatch_pending_jobs(
            db, sender=send, now=moment + timedelta(seconds=60)
        )["sent"]
        == 3
    )
    assert len(PersistentBillingStore(tmp_path / "billing.db").charges()) == 3
    summary = billing.billing_summary(db, semester_id=term)
    assert summary["billing_status"] == "ALL_SENT" and all(
        x["sent_at"] for x in summary["bills"]
    )
    assert (
        clients["user1"]
        .put(
            f"/semesters/{term}/schedule/draft",
            json={"expected_version": 3, "choices": []},
        )
        .status_code
        == 409
    )
    assert (
        admin.post(
            "/api/v1/admin/registration/close",
            json={"semester_id": term, "confirmation": "CLOSE-CONFIRM"},
        ).json()["billing_jobs_created"]
        == 3
    )


def test_personnel_complete_fields_and_number_not_reused(db, clients):
    admin = clients["admin"]
    data = dict(
        full_name="<img src=x onerror=alert(1)>",
        role="student",
        birth_date="2004-01-01",
        social_security_number="DEMO-123",
        graduation_date="2027-06-30",
        status="on_leave",
    )
    response = admin.post("/api/v1/admin/users", json=data)
    assert response.status_code == 201
    created = response.json()
    stored = admin.get("/api/v1/admin/users/" + str(created["id"])).json()
    assert (
        stored["birth_date"] == data["birth_date"]
        and stored["social_security_number"] == "DEMO-123"
        and stored["status"] == "on_leave"
    )
    assert (
        admin.patch(
            "/api/v1/admin/users/" + str(created["id"]),
            json={"birth_date": "2099-01-01"},
        ).status_code
        == 422
    )
    assert (
        admin.patch(
            "/api/v1/admin/users/" + str(created["id"]),
            json={"birth_date": "2005-01-01", "graduation_date": "2000-01-01"},
        ).status_code
        == 422
    )
    assert admin.delete("/api/v1/admin/users/" + str(created["id"])).status_code == 200
    second = admin.post("/api/v1/admin/users", json=data).json()
    assert second["login_number"] != created["login_number"]
    assert "师生资料" in admin.get("/admin/users").text
    assert "/admin/close-registration" in admin.get("/admin/users").text


def test_teacher_history_causes_deactivation_even_after_release(db, seeded):
    teacher = seeded["teacher"]
    offering = seeded["offerings"][0]
    db.add(
        GradeChange(
            student_id=seeded["students"][0].id,
            offering_id=offering.id,
            changed_by_teacher_id=teacher.id,
        )
    )
    for item in seeded["offerings"]:
        item.teacher_id = None
    db.commit()
    assert personnel.delete_or_deactivate_teacher(db, teacher.id) == "deactivated"
    assert db.get(type(teacher), teacher.id) is not None


def test_future_closed_registration_is_not_completed(db, seeded):
    future = Semester(
        code="FUTURE",
        starts_on=date(2099, 1, 1),
        ends_on=date(2099, 6, 1),
        status=SemesterStatus.CLOSED,
    )
    db.add(future)
    db.commit()
    assert teaching.latest_completed_semester(db) is None
    with pytest.raises(registration.BusinessError):
        teaching.require_completed_semester(future)


def test_catalog_fault_is_visible_without_touching_schedule(
    seeded, clients, monkeypatch
):
    def fail(code):
        raise registration.BusinessError("目录不可用", 503)

    monkeypatch.setattr(catalog, "fetch_catalog", fail)
    client = clients["user1"]
    assert client.get("/api/v1/student/available-sections").status_code == 503
    assert client.get("/api/v1/student/draft").json()["version"] == 1


def test_catalog_transport_rejects_bad_payloads(monkeypatch):
    monkeypatch.setattr(
        httpx,
        "get",
        lambda *a, **kw: httpx.Response(
            200,
            json={"data": {"bad": True}},
            request=httpx.Request("GET", "http://catalog"),
        ),
    )
    with pytest.raises(registration.BusinessError) as error:
        catalog.fetch_catalog()
    assert error.value.status_code == 503


def test_catalog_import_is_repeatable_and_keeps_assignment(db):
    path = Path(__file__).resolve().parents[1] / "mock_catalog/catalog_data.json"
    snapshot = CatalogSnapshot.model_validate(
        json.loads(path.read_text(encoding="utf-8"))
    )
    term = catalog.import_catalog(db, snapshot)
    catalog.import_catalog(db, snapshot)
    assert db.scalar(select(func.count(registration.Offering.id))) == 8
    assert term.status == SemesterStatus.OPEN


def test_persistent_billing_deduplicates_after_restart(tmp_path):
    path = tmp_path / "external.db"
    request = ChargeRequest(
        semester_id=1, student_id=2, amount=Decimal("100"), schedule_snapshot="[]"
    )
    assert PersistentBillingStore(path).receive(request)[0] == "created"
    assert PersistentBillingStore(path).receive(request)[0] == "duplicate"
    conflict = request.model_copy(update={"amount": Decimal("200")})
    assert PersistentBillingStore(path).receive(conflict)[0] == "conflict"
    assert len(PersistentBillingStore(path).charges()) == 1
    assert len(PersistentBillingStore(path).conflicts()) == 1
