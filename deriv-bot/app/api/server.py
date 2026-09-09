"""
Minimal HTTP surface (spec Part 1's "FastAPI for optional local API/dashboard").

Deliberately read-only: /health for liveness/uptime checks, /status for the
current dashboard snapshot as JSON. No control endpoints (stop trading,
reset emergency stop) are exposed here — an unauthenticated "stop the bot"
or "reset emergency stop" endpoint is a bigger risk than the convenience is
worth. Add those later behind real auth if you want remote control, not as
an afterthought bolted onto a monitoring endpoint.

The trading loop writes into `latest_state` (a tiny in-process holder); this
app just reads it. No shared database, no locking needed beyond what a
single asyncio event loop already gives you for free.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import FastAPI

from app.monitoring.dashboard import DashboardState

app = FastAPI(title="Deriv Trading Bot Status")


class LatestStateHolder:
    """Single mutable slot the trading loop writes to and the API reads from."""

    def __init__(self):
        self.state: DashboardState | None = None
        self.updated_at: str | None = None

    def update(self, state: DashboardState) -> None:
        self.state = state
        self.updated_at = datetime.now(timezone.utc).isoformat()


latest_state = LatestStateHolder()


@app.get("/health")
async def health():
    """Liveness only — deliberately does not say anything about trading state, just that the process is up."""
    return {"status": "ok"}


@app.get("/status")
async def status():
    """
    Current dashboard snapshot as JSON. Returns a clear "not started yet"
    response rather than a 404/500 if the trading loop hasn't run a single
    tick yet — that's a normal state right after startup, not an error.
    """
    if latest_state.state is None:
        return {"status": "starting", "message": "no tick has completed yet"}
    return {
        "status": "running",
        "updated_at": latest_state.updated_at,
        "dashboard": asdict(latest_state.state),
    }
