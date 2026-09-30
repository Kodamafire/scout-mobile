# Independent profit exits

Scout now evaluates a return floor separately from its indicator exit score.
For long paper holdings, reaching +2% activates the existing ATR-based leash
(1.25 to 6 percentage points), with a floor no lower than break-even. At +5%,
the floor also retains at least 65% of the observed peak return; at +10%, 70%.
The tightest applicable floor wins. Persisted floors never move downward.

A return at or below the floor marks an exit immediately, without requiring
an indicator score of 10 or two confirmation checks. Actual order submission
still requires the paper account, an open market, and no existing open order.
Stored floors remain usable during chart-data failures. Floors are discarded
when their symbol is no longer held. Existing saved high-water values seed
the rule, so current holdings may qualify for exit on the next open check.

The dashboard shows the floor, the exit trigger, and the order action separately.
These are software checks, not standing broker stop orders. Scheduled checks
can be delayed; gaps and slippage can cause fills below the floor. Peaks remain
sampled returns, not every intraday high. Adding to a holding or externally
changing its cost basis can require resetting its saved high-water/floor.

## Offline observations, September 30, 2026

39 regression checks pass, including independent profit exits, monotonic floors,
missing charts, threshold boundaries, market-closed and open-order gates, and
cleanup for closed holdings. CI runs the suite before a paper cycle.

The saved CSV lacks historical ATR values and broker market-clock flags, so
exact volatility-floor replay and executable fill backtesting are unavailable.
An illustrative replay used logged checks on weekdays from 06:30 to 13:00 PT,
with the retained sampled high-water values (including out-of-session peaks).

| Variant | INTC first observed trigger | Later best logged session return |
| --- | --- | --- |
| Winner tiers alone | Sep 25 06:45, +3.721% | +3.721% |
| Constant 1.25-point leash | Sep 24 08:44, +0.744% | +4.608% |
| Constant 6-point leash plus winner tiers | Sep 25 06:45, +3.721% | +3.721% |

Winner tiers alone also flagged PLTR at +8.917% on September 29; subsequent
logged returns reached +11.860%. A fixed tight leash flagged it still earlier,
at +7.035%, before a later +13.795% observation. Those recoveries demonstrate
the cost of tighter protection. These examples are observations, not fills,
strategy returns, or evidence that the new rule improves profitability. No
threshold was optimized to maximize this small sample's result.
