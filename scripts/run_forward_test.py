"""
Runnable forward-test loop against a live Deriv DEMO account.

This is the first script in the whole project that actually talks to the
real API rather than a mock. Run it locally (it cannot run inside the
sandbox this was built in — no network access to api.derivws.com there).

WHAT THIS DOES, every poll_interval_s:
  1. Fetches the latest candles for `SYMBOL` (data/historical_store.py)
  2. Builds a MarketContext (features/engine.py)
  3. Runs `STRATEGY` against it
  4. If it trades: places a real DEMO order, polls until settled
  5. Prints the dashboard (monitoring/dashboard.py)

WHAT THIS DELIBERATELY DOES NOT DO:
  - Trade on a real-money account (MODE is hardcoded to DEMO below; there is
    no code path in this script that can reach the real endpoint)
  - Run unattended for long periods without you watching it — this is for
    supervised forward testing (spec Part 20), not a "start and forget" bot

BEFORE RUNNING:
  1. cp .env.example .env, fill in DERIV_API_TOKEN (your DEMO token) and
     DERIV_APP_ID
  2. pip install -r requirements.txt
  3. Confirm you have some historical candles already (or let step 1 below
     pull them - it will, the first time)

WHAT TO WATCH FOR (the flagged assumptions from Phase 13/15 that only a
real response can confirm):
  - Does get_ws_url_for_account() return a usable URL under the key this
    script expects? If not, it will raise KeyError immediately - check
    rest_client.get_ws_url_for_account and fix the field name.
  - Does the printed PROPOSAL RESPONSE (uncomment the debug print below)
    actually include ask_price/payout, and are duration/duration_unit/basis
    accepted? If Deriv returns an error about an unrecognized proposal
    field, that's app/markets/ws_client.py's proposal() to fix.
  - Does SETTLEMENT show up correctly (is_sold/status/profit) after a
    contract expires? If proposal_open_contract's shape differs, that's
    execution/contract_monitor.py to fix.
"""
from __future__ import annotations

import asyncio
import sys

from app.config.settings import settings
from app.data.historical_store import fetch_and_store_candles, get_stored_range
from app.execution.auth_client import DerivAuthenticatedClient
from app.execution.order_manager import OrderManager, OrderManagerConfig
from app.features.types import CandleSeries
from app.markets.context import MarketType
from app.markets.rest_client import get_accounts, get_ws_url_for_account
from app.markets.ws_client import DerivPublicClient
from app.monitoring.dashboard import render_dashboard_text
from app.monitoring.session_stats import SessionStats
from app.orchestration.forward_test_runner import ForwardTestRunner
from app.risk.exposure import ExposureManager
from app.risk.governor import RiskGovernor
from app.risk.overtrading import CooldownTracker, DuplicateSignalGuard, RateLimiter
from app.strategies.baselines import SimpleTrendStrategy

# ---- Hardcoded, deliberately conservative defaults for a first supervised run ----
SYMBOL = "frxEURUSD"
DURATION_S = 60              # 1-minute candles
DURATION_CANDLES = 1         # contract resolves 1 candle (i.e. 60s) after entry
POLL_INTERVAL_S = 60.0       # check for a new candle once a minute
STARTING_BALANCE_FOR_GOVERNOR = 1000.0   # the governor's OWN tracking; it does not read your real balance
DB_PATH = "forward_test.db"


async def get_demo_ws_url() -> str:
    accounts = await get_accounts()
    demo_accounts = [a for a in accounts if a.get("is_virtual") or "demo" in str(a.get("account_type", "")).lower()]
    if not demo_accounts:
        raise RuntimeError(
            f"No demo account found in get_accounts() response: {accounts}. "
            "Check the actual field name Deriv uses to mark an account as demo/virtual "
            "and fix this filter."
        )
    account_id = demo_accounts[0]["account_id"] if "account_id" in demo_accounts[0] else demo_accounts[0].get("id")
    if account_id is None:
        raise RuntimeError(f"Could not find an account id field in: {demo_accounts[0]}")
    return await get_ws_url_for_account(account_id)


def load_series_from_db(db_path: str, symbol: str, duration_s: int) -> CandleSeries:
    import sqlite3
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT timestamp, open, high, low, close FROM candles WHERE symbol=? AND duration_s=? ORDER BY timestamp ASC",
        (symbol, duration_s),
    ).fetchall()
    conn.close()
    return CandleSeries(
        symbol=symbol,
        duration_s=duration_s,
        timestamps=[r[0] for r in rows],
        opens=[r[1] for r in rows],
        highs=[r[2] for r in rows],
        lows=[r[3] for r in rows],
        closes=[r[4] for r in rows],
    )


async def main():
    if settings.live_trading:
        print("LIVE_TRADING is true in .env - refusing to run this script. This script is DEMO-only by design.")
        sys.exit(1)

    print("Ensuring local candle history exists / is up to date...")
    inserted = await fetch_and_store_candles(DB_PATH, SYMBOL, DURATION_S, count=1000)
    print(f"  inserted {inserted} new candles")

    print("Resolving demo account WebSocket URL...")
    ws_url = await get_demo_ws_url()

    order_manager = OrderManager(
        config=OrderManagerConfig(
            currency="USD",
            risk_per_trade=settings.risk_per_trade,
            max_stake=settings.max_stake,
            min_probability_edge=settings.min_probability_edge,
            min_expected_value=settings.min_expected_value,
            max_latency_ms=settings.max_latency_ms,
        ),
        risk_governor=RiskGovernor(
            starting_balance=STARTING_BALANCE_FOR_GOVERNOR,
            max_daily_loss=settings.max_daily_loss,
            max_drawdown=settings.max_drawdown,
            max_consecutive_losses=settings.max_consecutive_losses,
        ),
        exposure_manager=ExposureManager(max_exposure_per_currency=2.0),
        duplicate_guard=DuplicateSignalGuard(),
        cooldown_tracker=CooldownTracker(cooldown_seconds=60.0),
        rate_limiter=RateLimiter(
            max_trades_per_hour=settings.max_trades_per_hour,
            max_trades_per_day=settings.max_trades_per_day,
        ),
    )

    runner = ForwardTestRunner(
        symbol=SYMBOL,
        market_type=MarketType.FOREX,
        strategy=SimpleTrendStrategy(),   # deliberately a Phase 7 baseline for the first-ever live run, not the ML model
        model_version="baseline_simple_trend_v1",
        order_manager=order_manager,
        risk_governor=order_manager.risk_governor,
        session_stats=SessionStats(),
        public_client=None,   # set fresh each loop iteration below (see note in the loop)
        auth_client=None,
        duration_candles=DURATION_CANDLES,
        duration_s=DURATION_S,
    )

    print(f"Starting forward test on {SYMBOL}. Press Ctrl+C to stop.\n")
    tick_count = 0

    while True:
        tick_count += 1
        inserted = await fetch_and_store_candles(DB_PATH, SYMBOL, DURATION_S, count=50)
        series = load_series_from_db(DB_PATH, SYMBOL, DURATION_S)

        if len(series) < 120:
            print(f"Only {len(series)} candles so far - waiting for enough warmup history...")
            await asyncio.sleep(POLL_INTERVAL_S)
            continue

        # Fresh connections per tick keep this simple and robust to a dropped
        # socket between ticks; a longer-running version would keep these
        # open and add reconnect/backoff (spec Part 30) instead.
        async with DerivPublicClient() as public_client:
            async with DerivAuthenticatedClient(ws_url) as auth_client:
                runner.public_client = public_client
                runner.auth_client = auth_client
                signal_id = f"{SYMBOL}-{series.timestamps[-1]}-{tick_count}"
                state = await runner.run_once(series, signal_id=signal_id)

        print(f"--- tick {tick_count} ---")
        print(render_dashboard_text(state))
        print()

        await asyncio.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    asyncio.run(main())
