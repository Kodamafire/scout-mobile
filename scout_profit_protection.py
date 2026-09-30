"""Runner exits, legacy observational floors, and entry risk budgets.

These are cycle-time checks for long-only paper positions, not broker stops.
"""
from math import isfinite


def update_runner_floor(high_water, pnl_pct, atr_pct, previous_floor=None,
                        activation_pct=5.0, atr_multiple=3.0,
                        minimum_trail_pct=5.0, hard_stop_pct=-7.5):
    """Experimental runner trail, expressed as return relative to entry.

    ATR percent is measured against the current chart price. Convert both
    ATR and peak-price percentage distances into entry-return points.
    No fixed profit target or automatic break-even floor. Stored floors
    ratchet only upward, and remain enforceable during chart outages.
    """
    if not (isfinite(high_water) and isfinite(pnl_pct)):
        return previous_floor, False
    floor = previous_floor if previous_floor is not None and isfinite(previous_floor) else None
    if high_water >= activation_pct and atr_pct is not None and isfinite(atr_pct) and atr_pct > 0:
        distance = max(minimum_trail_pct * (1 + high_water / 100),
                       atr_multiple * atr_pct * (1 + pnl_pct / 100))
        candidate = max(hard_stop_pct, high_water - distance)
        floor = max(candidate, floor) if floor is not None else candidate
    return floor, floor is not None and pnl_pct <= floor


def risk_sized_budget(equity, spendable, allocation_fraction=0.10,
                      risk_fraction=0.0075, stop_pct=-7.5):
    """Cap notional by cash, allocation, and planned loss at initial stop.

    Round down to cents so rounding cannot exceed the configured budget.
    Gaps/slippage can still cause realized losses beyond planned risk.
    """
    values = (equity, spendable, allocation_fraction, risk_fraction, stop_pct)
    if not all(isfinite(v) for v in values) or stop_pct >= 0:
        return 0.0
    budget = max(0.0, min(spendable, equity * allocation_fraction,
                          equity * risk_fraction / (abs(stop_pct) / 100)))
    return int(budget * 100) / 100


def update_profit_floor(high_water, pnl_pct, leash, previous_floor=None):
    """Return a floor that can tighten but never loosen, and breach status.

    At +2%, use the volatility leash, with a minimum floor of break-even.
    At +5%, retain at least 65% of the observed peak return; at +10%, 70%.
    Stored floors and winner tiers work even when chart data is unavailable.
    """
    if not (isfinite(high_water) and isfinite(pnl_pct)):
        return previous_floor, False
    floors = []
    if previous_floor is not None and isfinite(previous_floor):
        floors.append(previous_floor)
    if high_water >= 2.0:
        if leash is not None and isfinite(leash) and leash > 0:
            floors.append(max(0.0, high_water - leash))
        if high_water >= 5.0:
            floors.append(high_water * (0.70 if high_water >= 10.0 else 0.65))
    floor = max(floors) if floors else None
    return floor, floor is not None and pnl_pct <= floor
