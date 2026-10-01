# Scout exit and re-entry timing audit

1440 matched historical replay cases; 307 fill events audited.

## Five-session comparison at 10 bps per side, next-observation fills

| Variant | Mean return | Hold | Mean drawdown |
|---|---:|---:|---:|
| fast_timing_only | +1.46% | +2.16% | -2.62% |
| volatility_and_fast | +1.75% | +2.16% | -4.06% |
| fast_timing_only_no_reclaim | +1.41% | +2.16% | -2.68% |
| volatility_and_fast_no_reclaim | +1.75% | +2.16% | -4.06% |

## Fill diagnostics, five-session windows

| Variant | Complete exits | Rebound ≥1% after exit | Decline ≥1% after exit | Missed ≥1% while out | Complete buys | Buyback fell ≥1% | Pending outcomes |
|---|---:|---:|---:|---:|---:|---:|---:|
| fast_timing_only | 66 | 33 | 29 | 33 | 33 | 9 | 12 |
| volatility_and_fast | 25 | 13 | 14 | 13 | 7 | 3 | 6 |
| fast_timing_only_no_reclaim | 69 | 34 | 31 | 34 | 37 | 11 | 14 |
| volatility_and_fast_no_reclaim | 25 | 13 | 14 | 13 | 7 | 3 | 6 |

## Later reused period, same assumptions

| Variant | Mean return | Hold |
|---|---:|---:|
| fast_timing_only | +2.88% | +4.70% |
| volatility_and_fast | +0.51% | +4.70% |
| fast_timing_only_no_reclaim | +3.59% | +4.70% |
| volatility_and_fast_no_reclaim | +0.54% | +4.70% |

## Limits

- Post-exit rebounds and subsequent declines can both occur; categories overlap and are diagnostic, not proof an exit was wrong.
- 20 bars are five hours of observed trading, potentially crossing sessions; no future prices enter strategy decisions.
- Short windows censor many outcomes; incomplete horizons are excluded and reported as pending.
- Reused, selected history; no new holdout or full historical Scout portfolio/selection simulation.
- Forced initial positions, Yahoo reference bars, assumed per-side costs and next-bar fills, no dividends/taxes.
