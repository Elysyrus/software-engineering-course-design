from fastapi.testclient import TestClient

from app.main import app


def test_health_endpoint():
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_business_endpoint_requires_authenticated_session():
    response = TestClient(app).post(
        "/semesters/1/schedule/submit", json={"expected_version": 1}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "未登录"

