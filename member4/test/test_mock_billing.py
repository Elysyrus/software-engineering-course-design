"""任务 8：计费模拟接收服务测试。"""

import time

import pytest
from fastapi.testclient import TestClient

from mock_billing.mock_billing_service import app, store


def _payload(*, semester_id=1, student_id=7, amount="120.00", snapshot='[{"offering_id": 1}]'):
    return {
        "semester_id": semester_id,
        "student_id": student_id,
        "amount": amount,
        "schedule_snapshot": snapshot,
        "semester_code": "2026-FALL",
        "student_number": "20260007",
    }


@pytest.fixture
def client():
    store.reset()
    with TestClient(app) as test_client:
        yield test_client
    store.reset()


def test_first_charge_is_accepted(client):
    response = client.post("/billing/charges", json=_payload())

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "accepted"
    assert body["duplicate"] is False
    assert body["charge"]["amount"] == "120.00"
    assert client.get("/billing/charges").json()["count"] == 1


def test_identical_repeat_is_not_charged_twice(client):
    first = client.post("/billing/charges", json=_payload())
    second = client.post("/billing/charges", json=_payload())

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json()["status"] == "already_charged"
    assert second.json()["duplicate"] is True
    charges = client.get("/billing/charges").json()
    assert charges["count"] == 1
    assert charges["charges"][0]["attempts"] == 2


def test_amount_formatting_difference_is_still_a_duplicate(client):
    client.post("/billing/charges", json=_payload(amount="120.00"))

    response = client.post("/billing/charges", json=_payload(amount="120.0"))

    assert response.status_code == 200
    assert response.json()["duplicate"] is True


def test_different_amount_for_same_key_is_a_conflict(client):
    client.post("/billing/charges", json=_payload(amount="120.00"))

    response = client.post("/billing/charges", json=_payload(amount="150.00"))

    assert response.status_code == 409
    assert response.json()["detail"]["existing_amount"] == "120.00"
    assert response.json()["detail"]["received_amount"] == "150.00"
    conflicts = client.get("/billing/conflicts").json()
    assert conflicts["count"] == 1
    # 原账单保持不变，冲突只作为失败记录保留
    charge = client.get("/billing/charges/1/7").json()
    assert charge["amount"] == "120.00"
    assert charge["attempts"] == 1


def test_different_snapshot_for_same_key_is_a_conflict(client):
    client.post("/billing/charges", json=_payload(snapshot='[{"offering_id": 1}]'))

    response = client.post(
        "/billing/charges", json=_payload(snapshot='[{"offering_id": 2}]')
    )

    assert response.status_code == 409
    assert client.get("/billing/charges").json()["count"] == 1


def test_other_student_or_semester_is_a_separate_charge(client):
    client.post("/billing/charges", json=_payload())
    client.post("/billing/charges", json=_payload(student_id=8))
    client.post("/billing/charges", json=_payload(semester_id=2))

    assert client.get("/billing/charges").json()["count"] == 3


def test_unknown_charge_returns_404(client):
    assert client.get("/billing/charges/1/99").status_code == 404


@pytest.mark.parametrize("fault", ["503", "500"])
def test_fault_injection_rejects_without_storing(client, fault):
    response = client.post(f"/billing/charges?fault={fault}", json=_payload())

    assert response.status_code == int(fault)
    assert client.get("/billing/charges").json()["count"] == 0


def test_timeout_injection_delays_then_accepts(client):
    started = time.monotonic()

    response = client.post(
        "/billing/charges?fault=timeout&timeout_seconds=0.3", json=_payload()
    )

    assert response.status_code == 201
    # Windows 定时器精度约 15 毫秒，留出余量后仍能证明故障注入真的延迟了响应
    assert time.monotonic() - started >= 0.25
    assert client.get("/billing/charges").json()["count"] == 1


def test_negative_amount_is_rejected(client):
    response = client.post("/billing/charges", json=_payload(amount="-1.00"))

    assert response.status_code == 422
    assert client.get("/billing/charges").json()["count"] == 0


def test_reset_clears_charges_and_conflicts(client):
    client.post("/billing/charges", json=_payload())
    client.post("/billing/charges", json=_payload(amount="150.00"))

    assert client.post("/billing/reset").status_code == 200

    assert client.get("/billing/charges").json()["count"] == 0
    assert client.get("/billing/conflicts").json()["count"] == 0
