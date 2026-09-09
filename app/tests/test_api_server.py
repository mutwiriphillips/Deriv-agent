from datetime import datetime

from fastapi.testclient import TestClient

from app.api.server import app, latest_state, LatestStateHolder
from app.monitoring.dashboard import DashboardState


def test_health_returns_ok():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_status_before_any_tick_says_starting():
    latest_state.state = None
    latest_state.updated_at = None
    client = TestClient(app)
    response = client.get("/status")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "starting"


def test_status_after_a_tick_returns_dashboard_json():
    state = DashboardState(
        bot_status="RUNNING", account_balance=1000.0, daily_pnl=0.0, session_pnl=0.0,
        drawdown_fraction=0.0, active_strategy="baseline_trend", model_version="v1",
        api_status="CONNECTED", current_symbol="frxEURUSD", current_regime="TREND_UP",
        trades_today=3, win_rate=0.67, current_losing_streak=0,
    )
    latest_state.update(state)

    client = TestClient(app)
    response = client.get("/status")
    body = response.json()

    assert body["status"] == "running"
    assert body["dashboard"]["bot_status"] == "RUNNING"
    assert body["dashboard"]["current_symbol"] == "frxEURUSD"
    assert body["dashboard"]["trades_today"] == 3
    assert body["updated_at"] is not None


def test_latest_state_holder_updates_timestamp():
    holder = LatestStateHolder()
    assert holder.updated_at is None
    state = DashboardState(
        bot_status="RUNNING", account_balance=1000.0, daily_pnl=0.0, session_pnl=0.0,
        drawdown_fraction=0.0, active_strategy="x", model_version="v1", api_status="CONNECTED",
    )
    holder.update(state)
    assert holder.updated_at is not None
    parsed = datetime.fromisoformat(holder.updated_at)
    assert parsed.tzinfo is not None
