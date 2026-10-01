# Scout profit-protection historical experiment

Cutoff: 2026-10-01T19:20:00+00:00

Frozen parameters, completed daily signals, next completed 15-minute close fills

| Sample | Cost per side | Cases | Trial beats hold | Mean trial | Mean hold | Mean advantage |
|---|---:|---:|---:|---:|---:|---:|
| earlier | 10 bps | 9 | 1 | +3.84% | +11.57% | -7.73 points |
| earlier | 25 bps | 9 | 0 | +2.79% | +11.24% | -8.45 points |
| later_holdout | 10 bps | 9 | 5 | +4.40% | +4.70% | -0.30 points |
| later_holdout | 25 bps | 9 | 4 | +3.63% | +4.39% | -0.76 points |
| five_session | 10 bps | 72 | 16 | +1.16% | +2.16% | -1.00 points |
| five_session | 25 bps | 72 | 16 | +0.75% | +1.85% | -1.11 points |

## Limits

- Exit-management experiment with forced initial entries, not a complete Scout selection backtest.
- Yahoo consolidated prices/volume differ from Scout IEX; completed daily signals lag live forming bars.
- No broker fills, quote spreads, portfolio constraints, dividends or taxes; fixed execution costs assumed.
- Runner comparison exits to cash without new entries; holding is the matched continuous baseline.
- Later segment is a reserved chronological check, not untouched research: symbols were selected from current interests.
- Nine symbols and roughly two months do not establish durable profitability.

## Interpretation

This fixed tight-exit version reduced average peak-to-trough drawdowns but underperformed continuous holding on average in all three samples. At 10 basis points per side, five-session mean drawdown was -2.64% versus -4.57% for holding, while mean return was +1.16% versus +2.16%. The later chronological segment returned +4.40% versus +4.70%. Do not promote these thresholds to active Scout rules on this evidence.

Universe: INTC, PLTR, WBD, AAPL, NVDA, SMCI, SMR, SPY, QQQ. Parameters were fixed before the replay: +1% activation, 50% peak-gain retention, 0.75% peak-drop exit with weak momentum, two qualified checks for re-entry, maximum two re-entries each session, -7.5% hard loss. Nine symbols across earlier/later segments and eight disjoint five-session windows each, at two cost settings, produce 180 replay cases. Cases are correlated and are not 180 independent trades.

The samples contained bullish and mixed market regimes; this does not establish performance in a sustained bear market. Historical inputs are archived in data/ for reproducibility. Run python backtest_profit_trial.py from the repository to reproduce numeric results. The forward trial uses live Shadow daily observations and the actual scheduler, so results will differ from this completed-daily-signal, regular-15-minute replay.
