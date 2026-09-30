"""Offline what-if review of logged paper-position givebacks.

This reads Scout's existing cycle history. The observed P&L at a trigger is
not an executable fill and this module never connects to a broker.
"""
import csv
from pathlib import Path


def audit_giveback(rows, symbol, minimum_peak=5.0, giveback=3.0):
    observations = [r for r in rows if r.get("symbol") == symbol and r.get("pnl_pct") not in (None, "")]
    if not observations:
        return None
    observations.sort(key=lambda r: r["cycle"])
    latest = observations[-1]
    first = next((r for r in observations
                  if float(r["high_water"]) >= minimum_peak
                  and float(r["high_water"]) - float(r["pnl_pct"]) >= giveback), None)
    if first is None:
        return {"symbol": symbol, "minimum_peak": minimum_peak, "giveback": giveback,
                "triggered": False, "latest_pct": float(latest["pnl_pct"])}
    return {"symbol": symbol, "minimum_peak": minimum_peak, "giveback": giveback,
            "triggered": True, "first_cycle": first["cycle"],
            "observed_pct": round(float(first["pnl_pct"]), 3),
            "latest_pct": round(float(latest["pnl_pct"]), 3)}


if __name__ == "__main__":
    path = Path(__file__).parent / ".scout/scout_cycle_history_v43.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        history = list(csv.DictReader(handle))
    for peak, leash in ((5, 3), (5, 5), (5, 7)):
        print(audit_giveback(history, "INTC", peak, leash))
