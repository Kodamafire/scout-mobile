# Scout progressive trailing-stop trial v1

A read-only, one-time offline exit comparison. It does not install a service, change the dashboard, restart Scout, place orders, or change its current rules.

Frozen candidate: activate at +20%; trail 15% below peak, tighten to 10% at +50%, 2.5% at +100%, 2% at +200%, 1% at +900% (10 times entry value). Stored sell line never decreases. Baseline: activate +20%, fixed 15% trail; both have initial -25% premium stop.

Compare the same recorded entries and quote paths, retaining recorded common non-trailing exit triggers. Use a later fresh OPRA quote to model selling; never fill on the triggering quote. Displayed bid sizes limit partial fills. Stale, crossed, zero-size, indicative and out-of-order quotes cannot be modeled fills. Missing exits are censored, not scored as profits.

Default research assumptions: $0.65 per contract per side and $0.01 per share slippage per side, also tested at double costs. These are assumptions, not verified broker fees. Entry fills remain frozen; additional entry slippage is a conservative stress cost.

## Run on your computer

Download the ZIP to Downloads, Extract All, then in Ubuntu:

```bash
bash /mnt/c/Users/Omen/Downloads/Scout_Progressive_Test/Scout_Progressive_Test/run.sh
```

This takes a consistent read-only snapshot of compatible local SQLite ledgers and quote tapes under ~/scout-options/.scout-options. It writes inputs.json, results.json and REPORT.txt into ~/scout-options/.scout-options/progressive-test-results. No credentials or raw account state are exported. Inputs contain only selected simulated entries and observed quotes.

Send REPORT.txt back first. If the report says no eligible trades, missing quotes, or baseline unverified, there is no real option-performance result yet. Enhanced engine rules trigger withheld scoring until we build a matching baseline adapter. Partial/multiple entry fills are excluded and listed. A missing status file also withholds scoring.

A single run does not schedule future tests. Run the command again after more sessions are recorded. This comparison does not change subscriptions: tape after Scout's original exit may be missing. New forward collection may be needed to resolve that censoring.

## Mechanics tests already run

```bash
python3 -m unittest -v test_progressive_trial.py
python3 progressive_trial.py --synthetic --out synthetic_results
```

Six invented paths check a sustained winner, early stop before a rebound, loss, 10x winner, gap and unfinished trade. They are not backtest performance. The rebound example intentionally shows the progressive trail losing to the baseline. Tests cover tier math, ratcheting, later-quote fills, partial liquidity, stale/indicative exclusions, doubled costs, gaps, shared exits, read-only WAL snapshots and unsupported-baseline scoring refusal.

This isolates exit policy, not an executable full strategy: no counterfactual sizing, reentries or session account-risk recalculation. Other exits are mirrored from historical intents. Early closes depend on recorded session-cutoff intents. Do not promote it based on synthetic paths or a small, selected sample; freeze this version for fresh-session evaluation.
