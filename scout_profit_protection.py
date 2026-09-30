"""Profit floors for Scout's long-only paper positions, in entry-return points.

These are cycle-time exit triggers, not standing broker stop orders.
The thresholds reuse Scout's existing 2/5/10% winner tiers.
"""
from math import isfinite


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
