# Runner exits and planned account risk

Scout's long-only paper strategy now separates initial loss protection from
winner management. These settings are an experimental starting point, not
optimized thresholds or a claim of improved returns.

- Initial loss trigger remains -7.5% relative to entry and bypasses confirmation.
- No fixed take-profit target and no tightening at +10%.
- After a sampled peak of +5%, activate a trail using the greater of 3 ATR
  or 5% of peak price. No automatic break-even floor. The trail never exceeds
  the initial 7.5% loss allowance and ratchets upward once established.
- Indicator exits remain available at score 10 after two eligible checks.
  Old percentage-giveback score points are removed, so they cannot force an
  early runner exit indirectly. Confirmed weak-holding rotation remains active.
- Normal and rotation entries both cap planned initial loss at 0.75% of equity,
  allocation at 10%, and maintain the existing 35% cash reserve.
  With a 7.5% stop, 10% allocation already equals 0.75% planned account risk.
  Budget amounts round down to cents; smaller available cash reduces size.

ATR percent uses the latest chart close, while returns use the account mark.
Trail distance in entry-return percentage points is the greater of:
`5 * (1 + peak_return/100)` and `3 * ATR_pct * (1 + current_return/100)`.
For example, at +10% with ATR 2%, the floor is +3.4%, not the old +7% floor.
When chart and account prices differ, this conversion is approximate.

## Migration and comparison

The first runner-policy check preserves old tight floors under
`legacy_profit_floor`, resets executable floors and warning/rotation
confirmations once, and preserves sampled high-water observations.
This intentional policy change allows current winners more room. Later checks
never loosen the new floor. A missing chart retains an established floor; if
there is no established runner floor, only the account loss trigger can act.
Closed-position cleanup removes both executable and legacy floors.

The old floor remains observation-only. Up to 2,000 symbol/check comparisons
are retained under `exit_comparison` in Scout state, including both floors,
breach signals, and the observed return. This is a signal comparison, not two
independent portfolios or a fill backtest. Once Scout sells a position it no
longer follows that holding's counterfactual return. The historical CSV lacks
ATR and precise market-clock data; it cannot establish which policy earns more.

## Execution and validation

These are scheduled software checks, not standing broker stop orders. Orders
require the paper account, open market, and no duplicate open order. Gaps,
slippage, outages, and delayed checks can exceed planned risk. Peaks are sampled
account returns, not every intraday high. External changes to cost basis require
resetting the corresponding saved peaks and floors.

Offline tests cover runner pullbacks, trail conversion and ratcheting, policy
migration, chart outages, loss boundaries, entry and rotation sizing, cash
reserves, and order gates. CI runs the suite before connecting to the paper broker.
