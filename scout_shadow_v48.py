"""Scout V48 shadow intelligence.

Read-only decision layer. It NEVER submits orders.
It classifies market regime and separates setup quality from entry timing so
V47 can be benchmarked before any new logic is allowed to control paper trades.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class ShadowThresholds:
    setup_min: int = 70
    entry_min: int = 70
    hostile_entry_min: int = 85


def clamp(value, low=0, high=100):
    return max(low, min(high, int(round(value))))


def market_regime(spy):
    """Classify broad market conditions from the same daily indicators Scout uses."""
    if not spy:
        return {"name": "UNKNOWN", "risk": "HIGH", "score": 0, "trade_gate": False}

    close = spy["close"]
    ema20 = spy["ema20"]
    ema50 = spy["ema50"]
    sma200 = spy["sma200"]
    rsi = spy["rsi14"]
    atr = spy["atr_pct"]
    macd_bull = spy["macd"] > spy["macd_signal"]

    trend = 0
    trend += 25 if close > ema20 else -25
    trend += 20 if ema20 > ema50 else -20
    trend += 20 if close > sma200 else -20
    trend += 15 if macd_bull else -15
    trend += 10 if 48 <= rsi <= 72 else (-10 if rsi < 42 else 0)
    trend = clamp(50 + trend / 2)

    if atr >= 3.0:
        risk = "HIGH"
    elif atr >= 2.0:
        risk = "ELEVATED"
    else:
        risk = "NORMAL"

    if close > ema20 > ema50 and close > sma200 and macd_bull:
        name = "BULLISH TREND"
    elif close < ema20 < ema50 and close < sma200 and not macd_bull:
        name = "BEARISH TREND"
    elif atr >= 3.0:
        name = "HIGH VOLATILITY"
    else:
        name = "CHOP / MIXED"

    return {
        "name": name,
        "risk": risk,
        "score": trend,
        "trade_gate": name != "BEARISH TREND" and not (name == "HIGH VOLATILITY" and trend < 55),
    }


def setup_score(m):
    """Score durable swing quality; intentionally avoids double-counting near-identical signals."""
    if not m:
        return 0, {}

    groups = {
        "trend": 30 if (m["close"] > m["ema20"] > m["ema50"] and m["close"] > m["sma200"]) else
                 20 if (m["close"] > m["ema20"] and m["close"] > m["sma200"]) else
                 10 if m["close"] > m["sma200"] else 0,
        "momentum": 25 if (m["macd"] > m["macd_signal"] and m["macd_rising"] and 48 <= m["rsi14"] <= 70) else
                    15 if (m["macd"] > m["macd_signal"] and 45 <= m["rsi14"] <= 74) else 5,
        "relative_strength": 20 if m["rs20"] >= 8 else 15 if m["rs20"] >= 4 else 10 if m["rs20"] >= 2 else 0,
        "participation": 15 if m["relative_volume"] >= 1.5 else 10 if m["relative_volume"] >= 1.1 else 5,
        "liquidity": 10 if m["avg_dollar_volume"] >= 50_000_000 else 7 if m["avg_dollar_volume"] >= 20_000_000 else 0,
    }
    return clamp(sum(groups.values())), groups


def entry_timing_score(m, regime):
    """Score whether NOW is a sensible entry, separately from setup quality."""
    if not m:
        return 0, {}

    distance_ema20 = (m["close"] / m["ema20"] - 1) * 100
    not_extended = -1.0 <= distance_ema20 <= max(3.0, m["atr_pct"] * 1.5)
    momentum_live = m["macd"] > m["macd_signal"] and m["macd_rising"]
    rsi_window = 48 <= m["rsi14"] <= 68
    volume_confirm = m["relative_volume"] >= 1.10
    rs_confirm = m["rs20"] >= 2.0

    parts = {
        "not_extended": 30 if not_extended else 5,
        "momentum_live": 25 if momentum_live else 5,
        "rsi_window": 20 if rsi_window else 5,
        "volume_confirm": 15 if volume_confirm else 5,
        "relative_strength": 10 if rs_confirm else 0,
    }
    score = clamp(sum(parts.values()))
    if regime["name"] == "CHOP / MIXED":
        score = clamp(score - 10)
    elif regime["name"] in ("BEARISH TREND", "HIGH VOLATILITY"):
        score = clamp(score - 20)
    return score, parts


def shadow_decision(m, regime, thresholds=None):
    thresholds = thresholds or ShadowThresholds()
    setup, setup_parts = setup_score(m)
    entry, entry_parts = entry_timing_score(m, regime)
    required_entry = thresholds.hostile_entry_min if regime["risk"] == "HIGH" else thresholds.entry_min

    if not regime["trade_gate"]:
        decision = "NO TRADE — MARKET REGIME"
    elif setup < thresholds.setup_min:
        decision = "WATCH — SETUP TOO WEAK"
    elif entry < required_entry:
        decision = "WAIT — ENTRY NOT READY"
    else:
        decision = "QUALIFIED"

    return {
        "setup_score": setup,
        "entry_score": entry,
        "decision": decision,
        "setup_parts": setup_parts,
        "entry_parts": entry_parts,
        "required_setup": thresholds.setup_min,
        "required_entry": required_entry,
    }


def enrich_candidates(candidates, regime):
    """Attach V48 shadow scores without changing V47 order eligibility."""
    out = []
    for row in candidates:
        shadow = shadow_decision(row, regime)
        out.append({**row, "shadow": shadow})
    return out
