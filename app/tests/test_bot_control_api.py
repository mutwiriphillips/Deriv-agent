from fastapi.testclient import TestClient

from app.api.server import app, bot_controller


def test_strategies_endpoint_is_read_only_and_unauthenticated():
    client = TestClient(app)
    response = client.get("/strategies")
    assert response.status_code == 200
    body = response.json()
    assert "available_strategies" in body
    assert "active_strategy" in body


def test_control_disabled_by_default_returns_401(monkeypatch):
    monkeypatch.setattr("app.api.server.settings.control_api_key", "")
    client = TestClient(app)
    response = client.post("/control/stop")
    assert response.status_code == 401
    assert "disabled" in response.json()["detail"]


def test_control_rejects_wrong_key(monkeypatch):
    monkeypatch.setattr("app.api.server.settings.control_api_key", "correct-key")
    client = TestClient(app)
    response = client.post("/control/stop", headers={"X-API-Key": "wrong-key"})
    assert response.status_code == 401


def test_control_accepts_correct_key_and_actually_stops(monkeypatch):
    monkeypatch.setattr("app.api.server.settings.control_api_key", "correct-key")
    bot_controller.start()  # ensure known starting state
    client = TestClient(app)

    response = client.post("/control/stop", headers={"X-API-Key": "correct-key"})
    assert response.status_code == 200
    assert response.json()["running"] is False
    assert bot_controller.running is False  # the actual shared controller changed, not just the response


def test_control_start_after_stop(monkeypatch):
    monkeypatch.setattr("app.api.server.settings.control_api_key", "correct-key")
    bot_controller.stop()
    client = TestClient(app)
    response = client.post("/control/start", headers={"X-API-Key": "correct-key"})
    assert response.status_code == 200
    assert response.json()["running"] is True


def test_control_select_strategy_changes_active_strategy(monkeypatch):
    monkeypatch.setattr("app.api.server.settings.control_api_key", "correct-key")
    client = TestClient(app)
    response = client.post("/control/select-strategy?name=random", headers={"X-API-Key": "correct-key"})
    assert response.status_code == 200
    assert response.json()["active_strategy"] == "random"
    assert bot_controller.active_strategy_name == "random"


def test_control_select_unknown_strategy_returns_400(monkeypatch):
    monkeypatch.setattr("app.api.server.settings.control_api_key", "correct-key")
    client = TestClient(app)
    response = client.post("/control/select-strategy?name=not_real", headers={"X-API-Key": "correct-key"})
    assert response.status_code == 400
