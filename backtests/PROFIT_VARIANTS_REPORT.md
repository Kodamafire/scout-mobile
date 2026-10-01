# Scout profit strategy variants

Data cutoff: 2026-10-01T19:20:00+00:00. 2430 replay cases.

Each variant changes a specific part of the original rule; thresholds were fixed before this comparison. The later period was already inspected and is not a fresh holdout.

## Five-session cases, 10 basis points cost per side, next-observation fills

| Variant | Mean return | Holding | Difference | Mean drawdown | Beats holding |
|---|---:|---:|---:|---:|---:|
| original_tight | +1.17% | +2.16% | -1.00 points | -2.64% | 16/72 |
| volatility_only | +1.86% | +2.16% | -0.30 points | -3.90% | 11/72 |
| fast_timing_only | +1.47% | +2.16% | -0.70 points | -2.62% | 16/72 |
| volatility_and_fast | +1.75% | +2.16% | -0.41 points | -4.06% | 12/72 |
| partial_volatility_and_fast | +1.75% | +2.16% | -0.41 points | -3.92% | 16/72 |

## Later reused segment, same costs and delay

| Variant | Mean return | Holding | Difference | Mean drawdown |
|---|---:|---:|---:|---:|
| original_tight | +4.40% | +4.70% | -0.30 points | -3.79% |
| volatility_only | +1.84% | +4.70% | -2.87 points | -5.58% |
| fast_timing_only | +2.88% | +4.70% | -1.82 points | -4.48% |
| volatility_and_fast | +0.51% | +4.70% | -4.19 points | -7.02% |
| partial_volatility_and_fast | +1.12% | +4.70% | -3.58 points | -6.67% |

## Stress results: five-session cases

| Variant | Cost per side | Delay | Mean return | Difference vs holding |
|---|---:|---:|---:|---:|
| original_tight | 25 bps | 1 bars | +0.75% | -1.11 points |
| volatility_only | 25 bps | 1 bars | +1.53% | -0.32 points |
| fast_timing_only | 25 bps | 1 bars | +1.01% | -0.84 points |
| volatility_and_fast | 25 bps | 1 bars | +1.41% | -0.45 points |
| partial_volatility_and_fast | 25 bps | 1 bars | +1.40% | -0.45 points |
| volatility_only | 25 bps | 2 bars | +1.55% | -0.30 points |
| fast_timing_only | 25 bps | 2 bars | +0.88% | -0.98 points |
| volatility_and_fast | 25 bps | 2 bars | +1.35% | -0.51 points |
| partial_volatility_and_fast | 25 bps | 2 bars | +1.27% | -0.59 points |
| original_tight | 50 bps | 1 bars | +0.06% | -1.29 points |
| volatility_only | 50 bps | 1 bars | +1.00% | -0.35 points |
| fast_timing_only | 50 bps | 1 bars | +0.27% | -1.08 points |
| volatility_and_fast | 50 bps | 1 bars | +0.85% | -0.50 points |
| partial_volatility_and_fast | 50 bps | 1 bars | +0.82% | -0.53 points |

## Rules

- Volatility: activation at max(1%, 0.75 times daily ATR%); trail 1.5 times daily ATR% while strong, 0.75 times while weak, with a 0.75% minimum. Floors tighten only upward. Hard loss -7.5%.
- Faster timing: 15-minute EMA9/EMA21, MACD and prior 20-bar volume. Re-entry needs two rising-price qualifying observations at least 15 minutes apart, reclaiming the last sale price. Daily bearish market regime blocks re-entry.
- Partial: sell half once on a weaker volatility warning, retain the remainder until its protective floor or hard loss fires; cash can refill the position only after re-entry confirmation.
- At most two re-entries per session. New sessions reset confirmations and cancel stale pending buys. Signals fill on the next observation, or the second observation in delay stress tests.
- All costs are charged per side and final positions are valued after assumed liquidation costs. Each replay begins from the same unit capital as holding.

## Limits

- Exit-management trials start invested; this is not a complete Scout portfolio or entry-selection backtest.
- Previously inspected historical periods are reused comparisons, not fresh out-of-sample validation.
- About two months, nine selected symbols; mostly bullish/mixed market. Correlated cases, not independent trades.
- Yahoo consolidated prices, completed daily signals, causal intraday signals; data differ from live IEX.
- Next completed-bar prices are fill proxies, not guaranteed fills. Costs, execution delays are assumptions.
- No dividends, taxes, portfolio cash/slot constraints. Fixed rules, no parameter optimization.
