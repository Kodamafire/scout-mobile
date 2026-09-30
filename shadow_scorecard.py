"""Read-only, forward-only scorecard for Scout and Shadow observations.

Prices are reference daily-bar closes, not executable fills or trade results.
No broker client or order functions are imported here.
"""
import json
from datetime import date
from pathlib import Path

HORIZONS = (1, 5, 10)


def load_scorecard(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("signals"), list):
            return data
    except FileNotFoundError:
        pass
    return {"version": 1, "signals": []}


def record_signals(scorecard, shadow, observed_at, market_open):
    """Keep the first occurrence of each decision per stock, direction and session."""
    if not market_open or shadow.get("status") != "OK":
        return 0
    session = observed_at.date().isoformat()
    existing = {s["id"] for s in scorecard["signals"]}
    added = 0
    for row in shadow.get("evidence", []):
        if row["decision"] != "QUALIFIED" and not (
            row["direction"] == "LONG" and row.get("main_score") is not None
        ):
            continue
        signal_id = f'{session}|{row["symbol"]}|{row["direction"]}|{row["decision"]}'
        if signal_id in existing:
            continue
        scorecard["signals"].append({
            "id": signal_id, "session": session, "at": observed_at.isoformat(),
            "symbol": row["symbol"], "direction": row["direction"],
            "shadow_decision": row["decision"], "main_score": row.get("main_score"),
            "setup_score": row["setup_score"], "entry_score": row["entry_score"],
            "reference_price": row["price"],
            "outcomes": {str(h): None for h in HORIZONS},
        })
        existing.add(signal_id)
        added += 1
    return added


def pending_symbols(scorecard):
    return sorted({s["symbol"] for s in scorecard["signals"]
                   if any(s["outcomes"][str(h)] is None for h in HORIZONS)})


def settle_outcomes(scorecard, closes_by_symbol, today):
    """Use only completed sessions before today, never the forming daily bar."""
    today = date.fromisoformat(str(today))
    settled = 0
    for signal in scorecard["signals"]:
        if all(signal["outcomes"][str(h)] is not None for h in HORIZONS):
            continue
        closes = sorted((date.fromisoformat(str(day)), float(price))
                        for day, price in closes_by_symbol.get(signal["symbol"], [])
                        if date.fromisoformat(str(day)) < today
                        and date.fromisoformat(str(day)) > date.fromisoformat(signal["session"]))
        for horizon in HORIZONS:
            key = str(horizon)
            if signal["outcomes"][key] is not None or len(closes) < horizon:
                continue
            day, close = closes[horizon - 1]
            move = (close / float(signal["reference_price"]) - 1) * 100
            if signal["direction"] == "SHORT":
                move = -move
            signal["outcomes"][key] = {"session": day.isoformat(), "close": round(close, 4),
                                       "directional_move_pct": round(move, 3)}
            settled += 1
    return settled


def scorecard_summary(scorecard):
    signals = scorecard["signals"]
    comparison = {}
    for label, predicate in (
        ("agree", lambda s: s["direction"] == "LONG" and s["main_score"] is not None
         and s["shadow_decision"] == "QUALIFIED"),
        ("wait", lambda s: s["direction"] == "LONG" and s["main_score"] is not None
         and s["shadow_decision"] != "QUALIFIED"),
    ):
        group = [s for s in signals if predicate(s)]
        completed = [s["outcomes"]["1"]["directional_move_pct"] for s in group
                     if s["outcomes"]["1"] is not None]
        comparison[label] = {"signals": len(group), "one_day_complete": len(completed),
                             "average_one_day_pct": round(sum(completed) / len(completed), 3)
                             if completed else None}
    return {"count": len(signals), "one_day_complete": sum(s["outcomes"]["1"] is not None for s in signals),
            "five_day_complete": sum(s["outcomes"]["5"] is not None for s in signals),
            "comparison": comparison, "recent": list(reversed(signals[-4:]))}


def save_scorecard(path, scorecard):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(scorecard, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(path)
