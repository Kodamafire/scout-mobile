# STEP 1 — INSTALL, IMPORT, AND CONFIGURE

import base64
import json
import math
import os
import urllib.error
import urllib.request
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from scout_journal import record_cycle, journal_html

import numpy as np
import pandas as pd

from alpaca.data.enums import DataFeed, MostActivesBy
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.historical.screener import ScreenerClient
from alpaca.data.requests import StockBarsRequest, MostActivesRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import GetOrdersRequest, MarketOrderRequest

PACIFIC = ZoneInfo("America/Los_Angeles")


@dataclass(frozen=True)
class ScoutConfig:
    # Permanent safety lock
    paper_only: bool = True

    # Portfolio controls
    max_positions: int = 4
    max_new_entries_per_cycle: int = 2
    position_fraction: float = 0.10
    minimum_cash_reserve_fraction: float = 0.35
    minimum_order_dollars: float = 50.0
    # Portfolio upgrade / replacement engine
    # Paper replacements require confirmed market-open checks.
    upgrade_engine_enabled: bool = True
    upgrade_execute_trades: bool = True
    upgrade_candidate_score_min: int = 9
    upgrade_weak_exit_score_min: int = 6
    upgrade_min_score_advantage: int = 2
    upgrade_max_replacements_per_cycle: int = 1
    upgrade_fill_timeout_seconds: int = 45
    upgrade_fill_poll_seconds: int = 3

    # Require the same upgrade challenge across consecutive cycles.
    upgrade_confirmations_required: int = 3
    
    # Candidate scan / entry rules
    most_active_count: int = 100
    candidate_shortlist: int = 12
    entry_score_min: int = 7
    min_price: float = 2.0
    max_price: float = 200.0
    min_avg_dollar_volume: float = 20_000_000.0
    min_relative_volume: float = 1.10
    min_rs20: float = 2.0

    # Position management
    hard_stop_pct: float = -7.5
    exit_score_min: int = 10
    exit_confirmations: int = 2
    base_trailing_floor_pct: float = 1.25
    max_trailing_leash_pct: float = 6.0


    # Paper automation
    auto_buy_paper: bool = True
    auto_sell_paper: bool = True

    # Dashboard publishing
    publish_dashboard: bool = True
    github_owner: str = "Kodamafire"
    github_repo: str = "scout-mobile"
    github_branch: str = "main"
    github_file: str = "index.html"

CFG = ScoutConfig()
assert CFG.paper_only is True, "Scout stopped: paper-only safety lock changed."



# STEP 2 — SCOUT ENGINE

MEMORY_DIR = Path(".scout")
STATE_FILE = MEMORY_DIR / "scout_state_v43.json"
HISTORY_FILE = MEMORY_DIR / "scout_cycle_history_v43.csv"
DASHBOARD_FILE = Path("index.html")

def secret(name, required=True):
    value = os.environ.get(name, "").strip()
    if required and not value:
        raise RuntimeError(f"Missing environment secret: {name}")
    return value

def connect():
    key = secret("ALPACA_API_KEY")
    sec = secret("ALPACA_SECRET_KEY")
    trading = TradingClient(key, sec, paper=True)
    data = StockHistoricalDataClient(key, sec)
    screener = ScreenerClient(key, sec)
    account = trading.get_account()
    if str(getattr(account, "status", "")).upper().endswith("CLOSED"):
        raise RuntimeError("Alpaca paper account is closed.")
    return trading, data, screener, account

def mount_and_load_state():
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    default = {
        "version": 47,
        "high_water": {},
        "warning_streak": {},
        "upgrade_challenge": None,
        "last_cycle": None,
    }
    if not STATE_FILE.exists():
        return default
    try:
        loaded = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        for key, value in default.items():
            loaded.setdefault(key, value)
        return loaded
    except Exception as exc:
        print("State file unreadable; safe fresh state:", type(exc).__name__)
        return default

def save_state(state):
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temp.replace(STATE_FILE)

def bars_frame(data_client, symbols, days=420):
    symbols = sorted({str(s).upper() for s in symbols if s})
    if not symbols:
        return {}
    request = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=TimeFrame.Day,
        start=datetime.now(tz=PACIFIC) - timedelta(days=days),
        feed=DataFeed.IEX,
    )
    raw = data_client.get_stock_bars(request).df
    if raw is None or raw.empty:
        return {}
    result = {}
    if isinstance(raw.index, pd.MultiIndex):
        for symbol in symbols:
            try:
                frame = raw.xs(symbol, level=0).copy()
                result[symbol] = frame.sort_index()
            except KeyError:
                pass
    elif len(symbols) == 1:
        result[symbols[0]] = raw.copy().sort_index()
    return result

def indicators(frame, spy_frame=None):
    df = frame.copy()
    df.columns = [str(c).lower() for c in df.columns]
    if len(df) < 210:
        return None
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    macd_signal = macd.ewm(span=9, adjust=False).mean()
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    prev_close = close.shift(1)
    tr = pd.concat([(high-low), (high-prev_close).abs(), (low-prev_close).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean()
    avg_vol20 = volume.shift(1).rolling(20).mean()
    avg_dollar_vol20 = (close * volume).shift(1).rolling(20).mean()
    ret20 = close.pct_change(20) * 100
    spy_ret20 = 0.0
    if spy_frame is not None and len(spy_frame) >= 21:
        spy_close = spy_frame["close"].astype(float)
        spy_ret20 = float(spy_close.pct_change(20).iloc[-1] * 100)
    latest = {
        "close": float(close.iloc[-1]),
        "ema20": float(close.ewm(span=20, adjust=False).mean().iloc[-1]),
        "ema50": float(close.ewm(span=50, adjust=False).mean().iloc[-1]),
        "sma200": float(close.rolling(200).mean().iloc[-1]),
        "rsi14": float(rsi.iloc[-1]),
        "macd": float(macd.iloc[-1]),
        "macd_signal": float(macd_signal.iloc[-1]),
        "macd_rising": bool(macd.iloc[-1] > macd.iloc[-2]),
        "atr_pct": float(atr.iloc[-1] / close.iloc[-1] * 100),
        "relative_volume": float(volume.iloc[-1] / avg_vol20.iloc[-1]),
        "avg_dollar_volume": float(avg_dollar_vol20.iloc[-1]),
        "return20": float(ret20.iloc[-1]),
        "rs20": float(ret20.iloc[-1] - spy_ret20),
    }
    if not all(np.isfinite(v) for v in latest.values() if isinstance(v, (int, float))):
        return None
    return latest

def entry_score(m):
    checks = {
        "price_above_ema20": m["close"] > m["ema20"],
        "ema20_above_ema50": m["ema20"] > m["ema50"],
        "price_above_sma200": m["close"] > m["sma200"],
        "macd_bullish": m["macd"] > m["macd_signal"],
        "macd_rising": m["macd_rising"],
        "rsi_healthy": 45 <= m["rsi14"] <= 72,
        "relative_volume": m["relative_volume"] >= CFG.min_relative_volume,
        "relative_strength": m["rs20"] >= CFG.min_rs20,
        "liquid": m["avg_dollar_volume"] >= CFG.min_avg_dollar_volume,
    }
    return sum(checks.values()), checks

def prepare_confirmation_cycle(state, market_open, observed_at):
    """Count checks only during an open session, at least 15 minutes apart.

    Old state without a timestamp cannot supply trusted confirmations.
    """
    previous = state.get("last_confirmation_check")
    now = observed_at.timestamp()
    session = observed_at.date().isoformat()
    if not market_open or not previous or previous.get("session") != session:
        state["warning_streak"] = {}
        state["upgrade_challenge"] = None
        state["last_confirmation_check"] = None
    if not market_open:
        return False
    previous = state.get("last_confirmation_check")
    if previous and now - float(previous["timestamp"]) < 900:
        return False
    state["last_confirmation_check"] = {"session": session, "timestamp": now}
    return True


def analyze_position(position, m, state, advance_confirmation=True):
    symbol = str(position.symbol).upper()
    pnl_pct = float(position.unrealized_plpc) * 100
    previous_high = float(state["high_water"].get(symbol, pnl_pct))
    high_water = max(previous_high, pnl_pct)
    state["high_water"][symbol] = round(high_water, 4)
    giveback = max(0.0, high_water - pnl_pct)
    if m is None:
        # The account loss check must not depend on historical chart data.
        hard_exit = pnl_pct <= CFG.hard_stop_pct
        state["warning_streak"][symbol] = 0
        return {
            "symbol": symbol, "qty": float(position.qty),
            "market_value": float(position.market_value), "pnl_pct": pnl_pct,
            "high_water": high_water, "giveback": giveback, "leash": 0.0,
            "exit_score": 10 if hard_exit else 0, "confirmation": 0,
            "decision": "EXIT CONFIRMED" if hard_exit else "DATA UNAVAILABLE",
            "exit_confirmed": hard_exit, "rsi14": None,
            "reasons": (["Hard loss limit reached"] if hard_exit else [])
                       + ["Chart data unavailable; account loss check still active"],
        }
    leash = min(CFG.max_trailing_leash_pct, max(CFG.base_trailing_floor_pct, m["atr_pct"] * 1.35))

    score = 0
    reasons = []
    def add(points, reason):
        nonlocal score
        score += points
        reasons.append(reason)

    if m["close"] < m["ema20"]: add(2, "Price below EMA20")
    if m["ema20"] < m["ema50"]: add(2, "EMA20 below EMA50")
    if m["close"] < m["sma200"]: add(2, "Price below SMA200")
    if m["macd"] < m["macd_signal"]: add(2, "MACD bearish")
    if not m["macd_rising"]: add(1, "MACD falling")
    if m["rsi14"] < 45: add(1, "RSI weakening")
    if m["rsi14"] < 40: add(1, "RSI weak")
    if high_water >= 2 and giveback >= leash: add(3, "Adaptive profit leash breached")
    if high_water >= 5 and giveback >= high_water * 0.35: add(3, "Strong winner reversing")
    if high_water >= 10 and giveback >= high_water * 0.30: add(3, "Nuclear winner reversing")
    if pnl_pct <= CFG.hard_stop_pct: add(10, "Hard loss limit reached")

    warning = score >= CFG.exit_score_min
    streak = int(state["warning_streak"].get(symbol, 0))
    streak = (streak + int(advance_confirmation)) if warning else 0
    state["warning_streak"][symbol] = streak
    hard_exit = pnl_pct <= CFG.hard_stop_pct
    exit_confirmed = hard_exit or (warning and streak >= CFG.exit_confirmations)
    if exit_confirmed:
        decision = "EXIT CONFIRMED"
    elif warning:
        decision = "EXIT WARNING"
    elif high_water > 0 and (score >= 6 or (high_water >= 2 and giveback >= leash * .70)):
        decision = "PROTECT PROFIT"
    elif score >= 6:
        decision = "RISK WARNING"
    elif score >= 3:
        decision = "WATCH CLOSELY"
    elif pnl_pct > 0 and m["close"] > m["ema20"] and m["macd"] > m["macd_signal"]:
        decision = "LET RUN"
    else:
        decision = "HOLD"

    return {
        "symbol": symbol, "qty": float(position.qty), "market_value": float(position.market_value),
        "pnl_pct": pnl_pct, "high_water": high_water, "giveback": giveback,
        "leash": leash, "exit_score": score, "confirmation": streak,
        "decision": decision, "exit_confirmed": exit_confirmed,
        "rsi14": m["rsi14"], "reasons": reasons,
    }

def open_orders_by_symbol(trading):
    request = GetOrdersRequest(status=QueryOrderStatus.OPEN)
    orders = trading.get_orders(filter=request)
    return {str(o.symbol).upper(): o for o in orders}

def submit_sell_if_allowed(trading, row, market_open, open_orders):
    symbol = row["symbol"]
    if not (CFG.auto_sell_paper and market_open and row["exit_confirmed"]):
        if not row["exit_confirmed"]:
            return "NO ACTION"
        return "WAITING — MARKET CLOSED" if not market_open else "SELLING DISABLED"
    if symbol in open_orders:
        return "SKIPPED — OPEN ORDER EXISTS"
    qty = abs(float(row["qty"]))
    if qty <= 0:
        return "SKIPPED — NO QUANTITY"
    order = trading.submit_order(order_data=MarketOrderRequest(
        symbol=symbol, qty=qty, side=OrderSide.SELL, time_in_force=TimeInForce.DAY
    ))
    return f"SUBMITTED {getattr(order, 'id', '')}"

def scan_candidates(screener, data_client, held_symbols):
    active = screener.get_most_actives(MostActivesRequest(top=CFG.most_active_count, by=MostActivesBy.VOLUME))
    rows = getattr(active, "most_actives", active)
    symbols = []
    for row in rows:
        symbol = str(getattr(row, "symbol", "")).upper()
        if symbol and symbol not in held_symbols:
            symbols.append(symbol)
    requested = list(dict.fromkeys(["SPY"] + symbols))
    frames = bars_frame(data_client, requested)
    spy = frames.get("SPY")
    scored = []
    for symbol in symbols:
        m = indicators(frames[symbol], spy) if symbol in frames else None
        if not m or not (CFG.min_price <= m["close"] <= CFG.max_price):
            continue
        score, checks = entry_score(m)
        if score >= CFG.entry_score_min:
            scored.append({"symbol": symbol, "entry_score": score, **m, "checks": checks})
    scored.sort(key=lambda x: (x["entry_score"], x["relative_volume"], x["rs20"]), reverse=True)
    return scored[:CFG.candidate_shortlist]

def choose_upgrade(managed, candidates, held_symbols, open_orders):
    """Identify a superior candidate when the portfolio is already full."""
    if not CFG.upgrade_engine_enabled:
        print("UPGRADE CHECK: disabled")
        return None
    if len(held_symbols) < CFG.max_positions:
        print(f"UPGRADE CHECK: portfolio not full ({len(held_symbols)}/{CFG.max_positions})")
        return None
    if not candidates:
        print("UPGRADE CHECK: no qualified candidates")
        return None
    if not managed:
        print("UPGRADE CHECK: no managed positions")
        return None

    elite = candidates[0]
    if elite["entry_score"] < CFG.upgrade_candidate_score_min:
        print(f"UPGRADE CHECK: best candidate {elite['symbol']} scored {elite['entry_score']}/{CFG.upgrade_candidate_score_min} required")
        return None

    eligible = [
        p for p in managed
        if p.get("exit_score", 0) >= CFG.upgrade_weak_exit_score_min
        and p.get("symbol") not in open_orders
        and not p.get("exit_confirmed", False)
    ]
    if not eligible:
        scores = ", ".join(f"{p.get('symbol')}={p.get('exit_score', 0)}" for p in managed)
        print(f"UPGRADE CHECK: no weak holding at exit score >= {CFG.upgrade_weak_exit_score_min}; scores: {scores}")
        return None

    weakest = max(
        eligible,
        key=lambda p: (p.get("exit_score", 0), -p.get("pnl_pct", 0))
    )
    holding_quality = max(0, 9 - int(weakest.get("exit_score", 0)))
    advantage = int(elite["entry_score"]) - holding_quality
    if advantage < CFG.upgrade_min_score_advantage:
        print(
            f"UPGRADE CHECK: {elite['symbol']} advantage {advantage} is below "
            f"required {CFG.upgrade_min_score_advantage} over {weakest['symbol']}"
        )
        return None

    print(
        f"UPGRADE CHECK: MATCH {weakest['symbol']} -> {elite['symbol']} | "
        f"candidate={elite['entry_score']} holding_quality={holding_quality} advantage={advantage}"
    )
    return {
        "candidate": elite,
        "replace_symbol": weakest["symbol"],
        "replace_exit_score": weakest["exit_score"],
        "replace_pnl_pct": weakest["pnl_pct"],
        "holding_quality": holding_quality,
        "score_advantage": advantage,
        "replace_qty": weakest["qty"],
    }



def confirm_upgrade_persistence(upgrade, state, advance_confirmation=True):
    """Require the same candidate -> holding challenge across consecutive cycles."""
    previous = state.get("upgrade_challenge")

    if not upgrade:
        if previous:
            print(
                f"UPGRADE CONFIRM: reset {previous.get('replace_symbol')} -> "
                f"{previous.get('candidate_symbol')} (challenge disappeared)"
            )
        state["upgrade_challenge"] = None
        return None

    candidate_symbol = str(upgrade["candidate"]["symbol"]).upper()
    replace_symbol = str(upgrade["replace_symbol"]).upper()
    same_challenge = (
        isinstance(previous, dict)
        and previous.get("candidate_symbol") == candidate_symbol
        and previous.get("replace_symbol") == replace_symbol
    )
    confirmations = (int(previous.get("confirmations", 0)) if same_challenge else 0)
    confirmations += int(advance_confirmation)

    state["upgrade_challenge"] = {
        "candidate_symbol": candidate_symbol,
        "replace_symbol": replace_symbol,
        "confirmations": confirmations,
        "entry_score": int(upgrade["candidate"]["entry_score"]),
        "score_advantage": int(upgrade["score_advantage"]),
    }
    upgrade["upgrade_confirmation"] = confirmations
    upgrade["upgrade_confirmations_required"] = CFG.upgrade_confirmations_required
    upgrade["upgrade_confirmed"] = confirmations >= CFG.upgrade_confirmations_required

    print(
        f"UPGRADE CONFIRM: {replace_symbol} -> {candidate_symbol} | "
        f"{confirmations}/{CFG.upgrade_confirmations_required} consecutive cycles | "
        f"{'CONFIRMED' if upgrade['upgrade_confirmed'] else 'WAITING'}"
    )
    return upgrade

def _order_status_text(order):
    status = getattr(order, "status", "")
    return str(getattr(status, "value", status)).upper().rsplit(".", 1)[-1]


def _wait_for_terminal_order(trading, order_id, timeout_seconds=None):
    """Poll an Alpaca paper order until filled or terminal; never assume a fill."""
    timeout_seconds = timeout_seconds or CFG.upgrade_fill_timeout_seconds
    deadline = time.time() + timeout_seconds
    last = None
    while time.time() < deadline:
        last = trading.get_order_by_id(order_id)
        status = _order_status_text(last)
        if status == "FILLED":
            return True, last
        if status in ("CANCELED", "CANCELLED", "REJECTED", "EXPIRED"):
            return False, last
        time.sleep(CFG.upgrade_fill_poll_seconds)
    return False, last


def execute_upgrade_rotation_if_allowed(trading, upgrade, market_open, open_orders):
    """Safely rotate one confirmed weak holding into one elite candidate on paper."""
    result = {
        "status": None, "sell_order_id": None, "buy_order_id": None,
        "sell_filled": False, "buy_filled": False,
    }
    if not upgrade:
        return result

    confirmations = int(upgrade.get("upgrade_confirmation", 0))
    if not upgrade.get("upgrade_confirmed", False):
        result["status"] = (
            f"WATCH ONLY — CONFIRMING {confirmations}/"
            f"{CFG.upgrade_confirmations_required}"
        )
        return result
    if not CFG.upgrade_execute_trades:
        result["status"] = "WATCH ONLY — UPGRADE CONFIRMED"
        return result
    if not CFG.paper_only:
        result["status"] = "BLOCKED — PAPER SAFETY LOCK"
        return result
    if not market_open:
        result["status"] = "WATCH ONLY — MARKET CLOSED"
        return result

    sell_symbol = str(upgrade["replace_symbol"]).upper()
    buy_symbol = str(upgrade["candidate"]["symbol"]).upper()

    if sell_symbol in open_orders or buy_symbol in open_orders:
        result["status"] = "SKIPPED — OPEN ORDER EXISTS"
        return result

    fresh_positions = {
        str(p.symbol).upper(): p for p in trading.get_all_positions()
    }
    sell_position = fresh_positions.get(sell_symbol)
    if sell_position is None:
        result["status"] = "SKIPPED — SELL POSITION NO LONGER HELD"
        return result
    if buy_symbol in fresh_positions:
        result["status"] = "SKIPPED — REPLACEMENT ALREADY HELD"
        return result

    qty = abs(float(sell_position.qty))
    if qty <= 0:
        result["status"] = "SKIPPED — NO SELL QUANTITY"
        return result

    sell_order = trading.submit_order(order_data=MarketOrderRequest(
        symbol=sell_symbol, qty=qty, side=OrderSide.SELL,
        time_in_force=TimeInForce.DAY
    ))
    sell_id = getattr(sell_order, "id", None)
    result["sell_order_id"] = str(sell_id or "")
    if not sell_id:
        result["status"] = "FAILED — SELL ORDER HAS NO ID"
        return result

    sell_filled, sell_final = _wait_for_terminal_order(trading, sell_id)
    result["sell_filled"] = bool(sell_filled)
    if not sell_filled:
        result["status"] = (
            f"SELL NOT FILLED — {_order_status_text(sell_final) or 'TIMEOUT'}"
        )
        return result

    account = trading.get_account()
    positions_after_sell = {
        str(p.symbol).upper(): p for p in trading.get_all_positions()
    }
    if sell_symbol in positions_after_sell:
        result["status"] = "STOPPED — SELL FILLED BUT POSITION STILL PRESENT"
        return result
    if buy_symbol in positions_after_sell:
        result["status"] = "STOPPED — REPLACEMENT ALREADY PRESENT AFTER SELL"
        return result

    equity = float(account.equity)
    cash = float(account.cash)
    reserve = equity * CFG.minimum_cash_reserve_fraction
    spendable = max(0.0, cash - reserve)
    budget = min(equity * CFG.position_fraction, spendable)
    if budget < CFG.minimum_order_dollars:
        result["status"] = "SELL FILLED — BUY BLOCKED BY CASH RESERVE"
        return result

    fresh_open_orders = open_orders_by_symbol(trading)
    if buy_symbol in fresh_open_orders:
        result["status"] = "SELL FILLED — BUY SKIPPED, OPEN ORDER EXISTS"
        return result

    buy_order = trading.submit_order(order_data=MarketOrderRequest(
        symbol=buy_symbol, notional=round(budget, 2),
        side=OrderSide.BUY, time_in_force=TimeInForce.DAY
    ))
    buy_id = getattr(buy_order, "id", None)
    result["buy_order_id"] = str(buy_id or "")
    if not buy_id:
        result["status"] = "SELL FILLED — BUY ORDER HAS NO ID"
        return result

    buy_filled, buy_final = _wait_for_terminal_order(trading, buy_id)
    result["buy_filled"] = bool(buy_filled)
    if not buy_filled:
        result["status"] = (
            f"SELL FILLED — BUY NOT FILLED — "
            f"{_order_status_text(buy_final) or 'TIMEOUT'}"
        )
        return result

    result["status"] = f"ROTATION COMPLETE — {sell_symbol} -> {buy_symbol}"
    return result


def submit_buys_if_allowed(trading, candidates, account, market_open, held_symbols, open_orders):
    # Keep every qualified candidate visible even when all slots are full.
    results = [{**row, "order_status": "WATCHLIST — PORTFOLIO FULL"} for row in candidates]
    available_slots = max(0, CFG.max_positions - len(held_symbols))
    limit = min(CFG.max_new_entries_per_cycle, available_slots)
    if limit <= 0:
        return results

    equity = float(account.equity)
    cash = float(account.cash)
    reserve = equity * CFG.minimum_cash_reserve_fraction
    spendable = max(0.0, cash - reserve)
    budget_each = min(equity * CFG.position_fraction, spendable / max(1, limit))

    for i, row in enumerate(candidates[:limit]):
        symbol = row["symbol"]
        status = "QUALIFIED"
        if symbol in open_orders or symbol in held_symbols:
            status = "SKIPPED — DUPLICATE"
        elif budget_each < CFG.minimum_order_dollars:
            status = "SKIPPED — CASH RESERVE"
        elif not (CFG.auto_buy_paper and market_open):
            status = "DRY RUN"
        else:
            order = trading.submit_order(order_data=MarketOrderRequest(
                symbol=symbol, notional=round(budget_each, 2), side=OrderSide.BUY,
                time_in_force=TimeInForce.DAY
            ))
            status = f"SUBMITTED {getattr(order, 'id', '')}"
            spendable -= budget_each
        results[i] = {**row, "order_status": status}
    return results

def dashboard_html(report):
    def esc(value):
        import html
        return html.escape(str(value))
    cards = []
    for p in report["positions"]:
        pnl_class = "good" if p["pnl_pct"] >= 0 else "bad"
        reasons = "; ".join(p.get("reasons", [])) or "No exit warning signals"
        cards.append(f'''<section class="card"><div class="top"><b>{esc(p['symbol'])}</b><span class="pill">{esc(p['decision'])}</span></div>
        <div class="pnl {pnl_class}">{p['pnl_pct']:+.2f}%</div>
        <div class="grid"><span>Exit score (trigger: {CFG.exit_score_min})</span><b>{p['exit_score']}</b><span>Confirmed checks</span><b>{p.get('confirmation', 0)}/{CFG.exit_confirmations}</b><span>High water</span><b>{p['high_water']:+.2f}%</b><span>Giveback</span><b>{p['giveback']:.2f}%</b><span>Action</span><b>{esc(p['order_status'])}</b></div><p>{esc(reasons)}</p></section>''')
    candidates = "".join(f"<li><b>{esc(c['symbol'])}</b> — score {c['entry_score']}/9 — {esc(c['order_status'])}</li>" for c in report["candidates"])
    upgrade = report.get("upgrade")
    if upgrade:
        replacement = (
            f"Considering {esc(upgrade['replace_symbol'])} → {esc(upgrade['candidate']['symbol'])}. "
            f"Holding exit score: {upgrade['replace_exit_score']}; "
            f"candidate entry score: {upgrade['candidate']['entry_score']}/9. "
            f"Confirmed checks: {upgrade.get('upgrade_confirmation', 0)}/{CFG.upgrade_confirmations_required}. "
            f"Status: {esc(upgrade.get('order_status', 'WAITING'))}."
        )
    else:
        replacement = "No replacement currently qualifies."
    replacement_panel = (
        f'<div class="panel"><h2>Position replacement</h2><p>{replacement}</p>'
        '<p>Confirmations count only while the market is open, at least 15 minutes apart. '
        'They restart each trading session. Closed-market checks do not count.</p></div>'
    )
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="cache-control" content="no-cache"><title>Scout Trader</title>
    <style>*{{box-sizing:border-box}}body{{margin:0;background:#07111f;color:#ecf3ff;font-family:Arial,sans-serif}}main{{max-width:760px;margin:auto;padding:18px}}h1{{margin:0}}.sub{{color:#8ca3bf;margin:6px 0 18px}}.summary,.card,.panel{{background:#101f33;border:1px solid #223955;border-radius:16px;padding:16px;margin:12px 0}}.summary{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}.label{{color:#8ca3bf;font-size:12px}}.value{{font-size:22px;font-weight:bold}}.top{{display:flex;justify-content:space-between;gap:10px}}.pill{{background:#1c3552;padding:5px 9px;border-radius:99px;font-size:12px}}.pnl{{font-size:32px;font-weight:bold;margin:12px 0}}.good{{color:#31d18b}}.bad{{color:#ff6677}}.grid{{display:grid;grid-template-columns:1fr auto;gap:7px;color:#a8bad0}}.grid b{{color:#fff;text-align:right}}li{{margin:9px 0}}</style></head>
    <body><main><h1>🤖 Scout Trader</h1><div class="sub">Paper account • Updated {esc(report['updated'])}</div>
    <div class="summary"><div><div class="label">EQUITY</div><div class="value">${report['equity']:,.2f}</div></div><div><div class="label">MARKET</div><div class="value">{'OPEN' if report['market_open'] else 'CLOSED'}</div></div><div><div class="label">POSITIONS</div><div class="value">{len(report['positions'])}</div></div><div><div class="label">CANDIDATES</div><div class="value">{len(report['candidates'])}</div></div></div>
    {journal_html(report.get('journal', {}), report)}{replacement_panel}{''.join(cards) or '<div class="panel">No open positions.</div>'}<div class="panel"><h2>Qualified candidates</h2><ul>{candidates or '<li>None this cycle</li>'}</ul></div></main></body></html>'''

def publish_dashboard(html_text):
    DASHBOARD_FILE.write_text(html_text, encoding="utf-8")
    return "GENERATED FOR GITHUB PAGES"

def append_history(report):
    rows = []
    for p in report["positions"]:
        rows.append({"cycle": report["cycle"], "updated": report["updated"], "symbol": p["symbol"], "pnl_pct": p["pnl_pct"], "high_water": p["high_water"], "exit_score": p["exit_score"], "decision": p["decision"], "order_status": p["order_status"]})
    if not rows:
        rows.append({"cycle": report["cycle"], "updated": report["updated"], "symbol": "", "pnl_pct": "", "high_water": "", "exit_score": "", "decision": "NO POSITIONS", "order_status": ""})
    new = pd.DataFrame(rows)
    if HISTORY_FILE.exists():
        old = pd.read_csv(HISTORY_FILE)
        new = pd.concat([old, new], ignore_index=True)
    new.drop_duplicates(subset=["cycle", "symbol"], keep="last").to_csv(HISTORY_FILE, index=False)


# SCOUT AUTOMATIC PAPER CYCLE

def run_scout_cycle():
    started = datetime.now(PACIFIC)
    cycle = started.strftime("%Y%m%d_%H%M%S")
    print("=" * 70)
    print("SCOUT TRADER V47 AUTOMATIC — PAPER CONTROL CENTER")
    print("Cycle:", cycle)
    print("=" * 70)

    trading, data_client, screener, account = connect()
    state = mount_and_load_state()
    clock = trading.get_clock()
    market_open = bool(clock.is_open)
    advance_confirmation = prepare_confirmation_cycle(state, market_open, clock.timestamp)
    positions = list(trading.get_all_positions())
    held = {str(p.symbol).upper() for p in positions}
    open_orders = open_orders_by_symbol(trading)

    analysis_symbols = sorted(held | {"SPY"})
    try:
        frames = bars_frame(data_client, analysis_symbols)
    except Exception as exc:
        print("Position chart data unavailable; checking account losses:", type(exc).__name__)
        frames = {}
    spy = frames.get("SPY")
    managed = []
    for position in positions:
        symbol = str(position.symbol).upper()
        try:
            m = indicators(frames[symbol], spy) if symbol in frames else None
        except Exception as exc:
            print(f"Chart analysis unavailable for {symbol}:", type(exc).__name__)
            m = None
        row = analyze_position(position, m, state, advance_confirmation)
        row["order_status"] = submit_sell_if_allowed(trading, row, market_open, open_orders)
        managed.append(row)

    try:
        candidates = scan_candidates(screener, data_client, held)
    except Exception as exc:
        print("Candidate scan unavailable:", type(exc).__name__)
        candidates = []

    # Upgrade intelligence: at 4/4, elite candidates can still challenge weak holdings.
    # Execution is gated by confirmed checks and the paper-trading setting.
    print(
        f"UPGRADE DEBUG | managed={len(managed)} | held={len(held)} | "
        f"max_positions={CFG.max_positions} | candidates={len(candidates)} | "
        f"best_candidate={candidates[0]['symbol'] if candidates else 'NONE'} | "
        f"best_score={candidates[0]['entry_score'] if candidates else 'NONE'}"
    )
    raw_upgrade = choose_upgrade(managed, candidates, held, open_orders)
    upgrade = confirm_upgrade_persistence(raw_upgrade, state, advance_confirmation)
    if upgrade:
        rotation = execute_upgrade_rotation_if_allowed(
            trading, upgrade, market_open, open_orders
        )
        upgrade["order_status"] = rotation["status"]
        upgrade["rotation"] = rotation
        if rotation.get("buy_filled"):
            state["upgrade_challenge"] = None
            print(
                f"UPGRADE EXECUTE: {upgrade['replace_symbol']} -> "
                f"{upgrade['candidate']['symbol']} | ROTATION COMPLETE"
            )

    rotation_completed = bool(
        upgrade and upgrade.get("rotation", {}).get("buy_filled", False)
    )
    if rotation_completed:
        buy_results = [
            {**row, "order_status": "WATCHLIST — ROTATION COMPLETED THIS CYCLE"}
            for row in candidates
        ]
    else:
        buy_results = submit_buys_if_allowed(
            trading, candidates, account, market_open, held, open_orders
        )
    active_symbols = {p["symbol"] for p in managed}
    state["high_water"] = {k: v for k, v in state["high_water"].items() if k in active_symbols}
    state["warning_streak"] = {k: v for k, v in state["warning_streak"].items() if k in active_symbols}
    state["last_cycle"] = started.isoformat()

    report = {
        "version": 47, "cycle": cycle, "updated": started.strftime("%b %d, %Y %I:%M:%S %p PT"),
        "market_open": market_open, "equity": float(account.equity), "cash": float(account.cash),
        "positions": managed, "candidates": buy_results, "upgrade": upgrade,
    }
    report["journal_date"] = started.date().isoformat()
    try:
        report["journal"] = record_cycle(report, state, trading)
    except Exception as exc:
        print("Activity journal unavailable:", type(exc).__name__)
        report["journal"] = state.get("activity_journal", {})
        report["journal"]["sync_note"] = "Journal update unavailable; showing saved activity."
    save_state(state)
    append_history(report)
    publish_status = publish_dashboard(dashboard_html(report))

    print(f"Market: {'OPEN' if market_open else 'CLOSED'} | Equity: ${report['equity']:,.2f} | Cash: ${report['cash']:,.2f}")
    print(f"Positions analyzed: {len(managed)} | Qualified candidates: {len(candidates)} | Dashboard: {publish_status}")
    if upgrade:
        print(
            f"UPGRADE WATCH: {upgrade['replace_symbol']} -> {upgrade['candidate']['symbol']} | "
            f"candidate {upgrade['candidate']['entry_score']}/9 | holding quality "
            f"{upgrade['holding_quality']}/9 | advantage +{upgrade['score_advantage']} | "
            f"weak exit score {upgrade['replace_exit_score']} | "
            f"confirmation {upgrade.get('upgrade_confirmation', 0)}/"
            f"{CFG.upgrade_confirmations_required} | {upgrade['order_status']}"
        )
    if managed:
        print(pd.DataFrame(managed)[["symbol", "pnl_pct", "high_water", "giveback", "exit_score", "confirmation", "decision", "order_status"]].to_string(index=False))
    if buy_results:
        print(pd.DataFrame(buy_results)[["symbol", "entry_score", "close", "relative_volume", "rs20", "order_status"]].to_string(index=False))
    
    return report

if __name__ == "__main__":
    scout_report = run_scout_cycle()

