from fastapi.testclient import TestClient

from or_aws_fleet.api import app


def test_health_endpoint() -> None:
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}
