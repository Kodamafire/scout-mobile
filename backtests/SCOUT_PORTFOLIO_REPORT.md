# Scout matched portfolio comparison

60 portfolio replays. Universe: INTC, PLTR, WBD, AAPL, NVDA, SMCI, SMR, SPY, QQQ.

Entry, exit, cash, size, confirmation and rotation are included. This is a fixed-universe reference replay, not exact historical Scout.

## whole_reused — 10 bps per side, next-observation fills

| Version | Return | Max drawdown | Fills | Mean invested |
|---|---:|---:|---:|---:|
| baseline | +1.65% | -6.52% | 20 | 34.8% |
| no_chasing | +2.14% | -5.31% | 16 | 26.8% |
| stronger_relative_strength | +1.82% | -5.91% | 18 | 33.3% |
| stricter_mixed_market | -0.63% | -5.45% | 14 | 23.6% |
| stalled_trade_review | -1.26% | -8.29% | 26 | 32.1% |
## earlier_reused — 10 bps per side, next-observation fills

| Version | Return | Max drawdown | Fills | Mean invested |
|---|---:|---:|---:|---:|
| baseline | +0.27% | -4.69% | 14 | 33.5% |
| no_chasing | +0.45% | -4.85% | 11 | 21.9% |
| stronger_relative_strength | +0.97% | -4.17% | 12 | 32.4% |
| stricter_mixed_market | -1.36% | -5.41% | 11 | 22.7% |
| stalled_trade_review | -1.78% | -5.69% | 17 | 30.2% |
## later_reused — 10 bps per side, next-observation fills

| Version | Return | Max drawdown | Fills | Mean invested |
|---|---:|---:|---:|---:|
| baseline | +2.58% | -1.84% | 6 | 34.6% |
| no_chasing | +2.58% | -1.84% | 6 | 34.6% |
| stronger_relative_strength | +1.13% | -1.85% | 6 | 27.4% |
| stricter_mixed_market | -0.15% | -1.72% | 4 | 19.0% |
| stalled_trade_review | +1.56% | -1.85% | 8 | 33.0% |

## One change at a time

- No chasing: reject entries above EMA20 by more than max(3%, 1.5 times daily ATR%).
- Stronger relative strength: require 20-session outperformance of SPY of at least 4 percentage points instead of the existing score check at 2.
- Stricter mixed market: require entry score 9/9 when Shadow classifies the broad market as CHOP / MIXED.
- Stalled review: exit after five observed trading sessions if return is under +1% and exit score is at least 3.
- All other controls remain the same. Models reset to cash at each sample start. Higher costs and two-bar delays are included in the JSON.

## Limits

- Current nine-symbol fixed universe replaces historical most-active selection; survivorship and selection bias remain.
- Completed daily indicators plus 15-minute reference prices replace live forming-daily IEX inputs.
- Pure Scout entry/exit/confirmation/upgrade functions and config are reused; broker execution is a simulation adapter.
- Next observed prices with assumed costs; replacement sells fill before a later buy. No real fills, spreads, dividends or taxes.
- Initial account is cash. Position cap 4, max 2 new entries per check, 35% cash reserve, 10% allocation cap and 0.75% planned risk.
- Periods previously inspected; no untouched validation or exact historical Scout-account reconstruction.

## Interpretation

No-chasing is the strongest candidate in this reused reference sample: +2.14% versus baseline +1.65%, and maximum drawdown -5.31% versus -6.52%. In the later segment it produced the same orders and return as the baseline (+2.58%), so that segment provides no evidence of an improvement. Stricter mixed-market entry and stalled-trade exits hurt results here. Stronger relative strength was inconsistent across periods. These comparisons do not establish a production edge; no active trading-rule change has been made.

## Future input capture

Scout now records each cycle's actual scanner symbols, all available candidate metrics/scores, held-position inputs, account equity/cash, position quantities, decision state, configuration and code hashes. Capture has no broker mutation capabilities. Unavailable data are explicitly marked and duplicate cycles are suppressed. Credentials, account numbers and order identifiers are excluded by an allowlist. Files live under .scout/research/YYYY-MM-DD.jsonl. This supplies future point-in-time decision inputs; it does not replace bid/ask quotes or actual execution records.
