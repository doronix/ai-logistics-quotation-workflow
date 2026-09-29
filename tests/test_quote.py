import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)
COMPLETE = {
    "origin": "Shenzhen",
    "destination": "Los Angeles",
    "weight_kg": 850,
    "volume_cbm": 3.2,
    "cargo_type": "general",
    "transport_mode": "air",
}


def test_health() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "ai-quotation-demo"}


def test_quote_schema_and_deterministic_price() -> None:
    first = client.post("/api/v1/rates/quote", json=COMPLETE)
    second = client.post("/api/v1/rates/quote", json=COMPLETE)

    assert first.status_code == 200
    assert first.json() == second.json()
    assert first.json()["total_price"] == 2137.4
    assert set(first.json()) == {
        "quote_id",
        "currency",
        "base_rate",
        "fuel_surcharge",
        "handling_fee",
        "total_price",
        "valid_until",
        "estimated_transit_days",
        "provider",
    }


def test_minimum_charge_and_default_air() -> None:
    response = client.post(
        "/api/v1/rates/quote",
        json={"origin": "A", "destination": "B", "weight_kg": 10},
    )
    assert response.status_code == 200
    assert response.json()["total_price"] == 1000.0
    assert response.json()["estimated_transit_days"] == 7


def test_dangerous_cargo_surcharge() -> None:
    general = client.post("/api/v1/rates/quote", json=COMPLETE).json()
    dangerous = client.post(
        "/api/v1/rates/quote", json={**COMPLETE, "cargo_type": "dangerous"}
    ).json()
    assert dangerous["handling_fee"] - general["handling_fee"] == 350.0


def test_missing_weight_and_volume_returns_422() -> None:
    response = client.post(
        "/api/v1/rates/quote",
        json={"origin": "Shenzhen", "destination": "Los Angeles"},
    )
    assert response.status_code == 422


def test_invalid_input_returns_422() -> None:
    response = client.post(
        "/api/v1/rates/quote",
        json={"origin": "Shenzhen", "destination": "Los Angeles", "weight_kg": -1},
    )
    assert response.status_code == 422


def test_error_500_simulation() -> None:
    response = client.post(
        "/api/v1/rates/quote", json={**COMPLETE, "simulate": "error_500"}
    )
    assert response.status_code == 500
    assert response.json()["detail"] == "simulated upstream failure"


def test_invalid_json_simulation() -> None:
    response = client.post(
        "/api/v1/rates/quote", json={**COMPLETE, "simulate": "invalid_json"}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.text == '{"quote_id":'


def test_invalid_business_simulation() -> None:
    response = client.post(
        "/api/v1/rates/quote", json={**COMPLETE, "simulate": "invalid_business"}
    )
    assert response.status_code == 200
    assert response.json()["total_price"] == 0


def test_none_simulation_is_a_normal_quote() -> None:
    response = client.post(
        "/api/v1/rates/quote", json={**COMPLETE, "simulate": "none"}
    )
    assert response.status_code == 200
    assert response.json()["total_price"] == 2137.4


def test_timeout_and_slow_are_bounded(monkeypatch) -> None:
    delays: list[int] = []
    monkeypatch.setattr("app.main.time.sleep", delays.append)

    assert client.post(
        "/api/v1/rates/quote", json={**COMPLETE, "simulate": "timeout"}
    ).status_code == 200
    assert client.post(
        "/api/v1/rates/quote", json={**COMPLETE, "simulate": "slow"}
    ).status_code == 200
    assert delays == [3, 1]


def test_request_id_is_preserved() -> None:
    response = client.post(
        "/api/v1/rates/quote",
        json=COMPLETE,
        headers={"X-Request-ID": "workflow-run-123"},
    )
    assert response.headers["X-Request-ID"] == "workflow-run-123"


def test_fixture_covers_required_cases() -> None:
    fixtures = json.loads(
        (Path(__file__).parent / "fixtures" / "inquiries.json").read_text(encoding="utf-8")
    )
    assert len(fixtures) >= 10
    assert {item["id"] for item in fixtures} >= {
        "complete_en",
        "missing_origin",
        "missing_destination",
        "weight_only",
        "volume_only",
        "ambiguous",
        "api_timeout",
        "api_500",
        "complete_zh",
        "conflicting_fields",
    }
