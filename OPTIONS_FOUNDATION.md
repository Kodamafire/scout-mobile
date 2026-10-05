# Scout options foundation

**Status: runnable reference simulator and read-only Alpaca adapter. No brokerage
orders are submitted, no live execution switch exists, and no service is deployed.**
The existing Scout stock cycle is unchanged. This branch builds on strategy desk
PR #7, so its draft PR targets that branch rather than duplicating those changes.

## What runs

- An independent one-second management loop; scans and broker account reads run
  in worker threads so a slow request does not delay the management timer.
- Experimental bullish CALL and bearish PUT signals from 21 consecutive completed
  one-minute bars, short/long moving averages, recent momentum and volume.
- Read-only starter universe SPY, QQQ and IWM, using IEX stock bars. Scan every 60
  seconds; this is not a full-market scan and does not provide consolidated stock
  volume. At least 21 complete session minutes are required before a signal.
- Contract selection with fresh streamed OPRA quotes; 7–30 calendar days to
  expiration, standard 100-share multiplier, valid Greeks, absolute delta .35–.65,
  open interest >=100, positive IV <=200%, and bid/ask spread <=10% of midpoint.
  These are initial research settings, not performance-optimized rules.
- Streaming quote callbacks feed a SQLite simulated ledger; intents persist
  before fills, duplicate/out-of-order quotes are discarded, and fills use a
  **later** quote at ask for buys or bid for sells, subject to the order limit and
  displayed size. Partial fills retain the remaining reservation.
- Full premium budgeting, cash reserve, duplicate-underlying prevention, position
  count limit, session loss pause, cooldown, premium loss exits, underlying signal
  invalidation, profit trailing, entry timeouts and unfilled-exit repricing.
- All positions attempt to close 30 minutes before the exchange clock's close,
  including early-close sessions. No planned overnight holdings. Expiration is an
  additional exit trigger, not a promise that closure can be guaranteed.
- A writer lock prevents two services using the same ledger. Restart restores
  intents and positions, but requires fresh quotes and a fresh clock before new
  entries. It does not reconcile or recover actual brokerage orders.
- Atomic local status JSON and durable local alert/event logs. External phone
  alerts and a mobile options dashboard have **not** been connected yet.

## Run offline demo

Install the existing project requirements, then from the repository root:

```bash
python -m scout_options.service --demo --seconds 10 \
  --ledger /tmp/scout-options-demo.sqlite --status /tmp/scout-options-demo.json
```

Demo uses explicitly synthetic quotes and $10,000 virtual capital. Its resulting
P&L is a scripted software check, not historical, forward-market or broker results.
Use a fresh ledger path to repeat the same scenario. Do not commit ledgers/status.

## Read-only streaming mode

Provide existing `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` through the service's secret
store. No credentials go into source files. Set `SCOUT_OPTIONS_FEED=opra` and run:

```bash
python -m scout_options.service --capital 10000 \
  --ledger /persistent/scout-options/ledger.sqlite \
  --status /persistent/scout-options/status.json
```

Capital is an explicitly chosen **separate virtual portfolio**, not account funds.
No brokerage paper or real orders are submitted. The adapter always creates its
broker-read client with `paper=True`. OPRA access must already exist; this service
neither purchases subscriptions nor changes account permissions. Indicative mode
can be selected to inspect connectivity, but indicative quotes cannot generate
entries or fills. No automatic fallback to indicative trading is allowed.

This mode reads the stock paper account's positions, cash and open orders. Stale
account data and any unresolved broker open orders block new simulated entries.
It conservatively checks stock exposure and cash above reserve, but does not
provide an atomic shared budget with Scout's live-running paper stock process.
Do not call this integrated account execution.

The scanner reads at most one 1,000-contract metadata page per underlying within
±10% strikes and explicit expiration bounds, shortlists three per active direction,
and bounds stream subscriptions to 50 per service lifetime. Truncation is recorded
in status. A long-running service can reach this subscription cap; production
rotation/unsubscription is still needed. Metadata expires after two minutes and
quotes after three seconds. A one-second check is not a one-second fill guarantee.

## Research controls

Defaults: full premium per entry <=1% of virtual equity; total committed premium
<=5%; at most two underlyings; 35% cash reserve; 25% premium-loss exit; trailing
activated after +20% bid-based gain with 15% peak giveback; daily equity-loss pause
at 2%; 30-second entry timeout; 15-second exit reprice; 5-minute re-entry cooldown.
These are experimental settings. Stops are software decisions, not standing broker
orders, and cannot guarantee fill prices or cap a gap loss. Entry spread filters
never disable an existing position's loss-protection decision.

Session loss uses virtual equity, including bid-marked positions, from the first
management check of the session. No external deposits affect this isolated ledger.
No fees, queue priority, exchange routing or actual available liquidity are modeled;
displayed quote sizes may not be obtainable. Treat simulated returns accordingly.

## Validation and remaining deployment work

Validation: all 243 offline tests pass (options lifecycle, dashboard, paper outbox, healthcheck and status-server tests); the 10-second
synthetic demo completed a buy, runner trail and exit. `git diff --check` passes.

`python -m unittest discover -q` covers calls/puts, contract filters, data age,
minute-bar completeness, later-quote fills, partial fills, duplicate prevention,
restart intent recovery, one-writer ownership, stops, trailing, failed exits,
expiry/cutoffs, shared snapshot checks, pauses and management cadence.

Before unattended brokerage paper execution: validate read-only connectivity and
feed entitlement; implement idempotent broker submissions plus authoritative
order/fill/position reconciliation; test account permissions and order rejection;
coordinate one atomic stock/options budget; add external urgent alerts, service
supervision, graceful shutdown and subscription rotation; reconcile expiration
activity; and forward-test costs/fills across different market conditions.

One-second monitoring needs an always-running host with persistent storage, not
Scout's 15-minute scheduled GitHub workflow. No deployment or performance claim
is made by this implementation.

Sources checked October 5, 2026:
- https://docs.alpaca.markets/us/docs/options-trading
- https://docs.alpaca.markets/us/docs/real-time-option-data
- https://docs.alpaca.markets/us/docs/about-market-data-api

## Options dashboard

The public `options.html` page is a mobile research dashboard with a four-step
fictional demo. It polls a same-origin `options-status.json` every second and
shows disconnected/stale status when no service publishes fresh data. The page
does not place orders, expose credentials, or turn on the options service.

Local status writing now creates `options.html` beside the chosen JSON file.
Serve that directory over HTTP to view it (do not open it as a file URL):

```bash
python -m scout_options.status_server --port 8080 --directory /persistent/scout-options --bind 127.0.0.1
```

Publishing the public dashboard alone does not publish local ledgers or attach
the always-on service. Future status publishing needs an explicit public-data
allowlist, authentication choices, and hosting integration. Local phone alerts
remain unconnected. The public page clearly states it is an undeployed research
simulator. Dashboard defaults are illustrative; no returns are promised.

## Continuous hosting and staged broker recovery

See `deploy/options/README.md` for a Docker Compose package that runs the
read-only adapter with persistent storage and a local dashboard. It has not been
deployed; the container build cannot be validated here because Docker is absent.

`scout_options/paper_gateway.py` now provides a separately staged, paper-only
durable order outbox with stable client IDs, cumulative broker fill reconciliation,
restart recovery, conservative cancellation and position mismatch checks. It is
not connected to the scanner or simulator and has no activation CLI. No brokerage
orders were sent. Shared risk coordination, actual account/feed validation,
external alerts and always-on host supervision remain required.

Local credential input can be checked without displaying values using
`python -m scout_options.credentials`. Whitespace and unexpected characters
are rejected before SDK initialization; format validation is not authentication.
Stopping a service that never started its quote stream no longer calls the SDK's
uninitialized event loop. Ctrl+C exits without a traceback after cleanup.

For easier local setup, `python -m scout_options.credentials --connect` prompts
privately for the existing paper key and secret, strips surrounding whitespace
and terminal paste delimiters, validates format, then starts the read-only
indicative service with the existing $10,000 research ledger. It does not save
credentials, change subscriptions or submit broker orders.

`python -m scout_options.diagnostics --running` checks paper account, clock and
position reads using the same user's single running options service credentials.
It reads that process environment locally and prints only safe status labels and
numeric HTTP codes. It never outputs credential values, remote error bodies,
account details, or makes brokerage changes.

## Windows local supervision

After verifying the running reference service, use
`python -m scout_options.local_runner --install` inside the same checkout. It
rechecks read-only paper access, saves existing credentials inside Ubuntu at
`~/.config/scout-options/credentials.json` with directory mode 700 and file mode
600, and creates a current-user Windows Startup shortcut. This file is not
encrypted; the Ubuntu user and administrators can read it. No credentials enter
GitHub, the shortcut, console output or the dashboard.

The installer hands off the known local worker and dashboard to a single-writer
background supervisor. It restarts exited children and a worker whose heartbeat
has stalled, always in read-only indicative mode with the existing research
ledger. The dashboard stays bound to localhost. Startup is after Windows sign-in,
not before login; do not enable automatic Windows login. Windows startup execution
must be verified on the user's machine. No cloud service or purchase is involved.

For an existing installation, `python -m scout_options.local_runner --update`
rechecks paper access, refreshes the shortcut and dashboard, and restarts using
locally saved credentials. No key entry is needed. The dashboard labels saved
alerts as history, since an earlier error can remain after recovery.

`python -m scout_options.local_runner --status` reports current readiness and
launch origin without credentials. `startup_verified: true` requires a fresh,
ready supervisor from the current WSL boot with `launch_source: windows_signin`.
An installer/update launch has source `setup`; a direct `--run` has source
`manual`. These do not establish Windows startup success. Check after the next
Windows sign-in. This is launch evidence, not proof of market-data readiness or
profitable trading.
`--stop` stops the background instance but leaves sign-in startup enabled. To
disable sign-in startup, remove `ScoutOptionsResearch.lnk` from `shell:startup`.
The two original console windows become unnecessary only after the background
service and dashboard are verified. Losing power or rebooting still interrupts
monitoring until Windows sign-in; phone alerts are not connected.

Validation: private file permissions, symlink rejection, shortcut construction,
worker restart and stale heartbeat readiness are tested offline. Actual Windows
COM shortcut creation, WSL console independence and sign-in behavior require
local verification. Child console output is discarded to avoid credential/error
payload leakage; lifecycle and heartbeat state are retained in supervisor status.


## Historical collection and chronological signal replay

No historical market dataset is bundled or downloaded by this PR. The default
collector defaults to six years ending before today. To probe further back, use
an explicit start date with saved local credentials:

```bash
python -m scout_options.history --collect --start 2016-01-01
python -m scout_options.history --replay --start 2016-01-01
```

The dataset is SPY/QQQ/IWM stock minute bars from the free IEX feed with raw
prices, stored at `.scout-options/stock-history.sqlite`. It is not a consolidated
stock feed or historical option premiums. Access and actual coverage must be
verified from the returned data. Alpaca documents option history only since
February 2024; this collector does not request options history.

Collection uses the exchange calendar, including early closes and daylight-saving
time. SDK pagination has no total-result limit. Each complete network response
is saved in an atomic session transaction. Interrupted sessions are requested
again; saved sessions resume automatically. Missing or invalid bars are reported,
not invented. `--retry-incomplete` re-fetches sessions with missing/rejected bars.
`--start YYYY-MM-DD --end YYYY-MM-DD` selects an inclusive start and exclusive end.
`--max-sessions 5` permits a small initial download; stopping with Ctrl+C is safe.
The foreground downloader does not change the existing background supervisor.

Replay evaluates the current completed-minute trend/volume signal and measures
signed underlying-stock movement from the next minute open to a minute open 15
minutes later. It skips gapped windows and never crosses a session boundary.
The earlier 70% of available sessions and later 30% are reported separately.
Repeated signals overlap and are not independent trades. Outputs do not model
option premiums, fill quality, fees, spreads, position sizing, stops or trailing
exits, and are not a full options-strategy backtest. No profitability evidence is
claimed until real historical data has been collected and evaluated.

Offline validation covers interruption/resume, invalid and missing data, atomic
checkpoints, free-feed selection, early close, no future inputs to signals,
gap rejection and chronological holdout. Actual API coverage remains unverified.

### Matched comparisons

```bash
python -m scout_options.history --replay --start 2016-01-01 --report .scout-options/comparison.json
```

This reuses saved data without another download. Progress appears every 25
sessions. The JSON report contains coverage, both chronological samples,
overlapping results, matched baselines and nonoverlapping comparisons. The
terminal shows a compact table. `always_up` and `always_down` measure signed
stock movement on exactly the bot's signal windows, using the same entry, exit,
symbol and gap filters. They test directional choice conditional on a signal,
not timing against all possible entries or buy-and-hold returns.

Nonoverlapping selection takes the first eligible signal for each underlying
and waits until its fixed exit timestamp before accepting the next. Different
symbols can overlap. Reports include calls/puts, symbols and predeclared entry
time buckets in Eastern time (09:30–10:30, 10:30–14:00, 14:00–16:00), handling
daylight-saving time. These are descriptive comparisons, not optimized filters.
Session means give each day with signals equal weight; the JSON includes those
daily means for inspection. Neither view makes observations independent or
provides a statistical confidence claim. Days without signals do not contribute.

The later sample has already been viewed. Keep the legacy `held_out` field for
compatibility, but treat it as historical validation for any subsequent change.
Do not select the best segment and claim a fresh out-of-sample result. A revised
strategy requires a frozen rule and new future observations. Missing future
windows also affect eligibility and can introduce selection bias. One-minute
stock bars cannot test five-second exits, option-profit targets, Greeks or
executable option quotes. These comparisons never place brokerage orders.

### Matched 1-, 5-, and 15-minute exits

```bash
python -m scout_options.history --compare-exits --start 2016-01-01 --report .scout-options/exit-comparison.json
```

This is one pass over the saved database, without credentials, downloads or
orders. Each completed signal is computed once. All horizons use the same
symbols and entry timestamps, requiring consecutive minute bars through the
longest (15-minute) exit. The nonoverlapping selection reserves each underlying
until that longest exit, even in the 1-minute and 5-minute versions. This holds
the entry sample fixed to isolate exit timing; it does not model immediate
re-entry after a shorter exit. Shorter exits near gaps or session close that
lack the full longest window are excluded from all three variants.

Reports keep both earlier and already-viewed later samples. Each horizon has
matched upward/downward baselines, calls/puts, symbols and Eastern time buckets.
Daily observations and average stock moves are now retained for every
nonoverlapping subgroup, allowing checks of day weighting, unusual days and
cross-day consistency. The terminal displays equal-weight session means for
all signals and opening-hour signals, rather than selecting a winning version.
Days without eligible signals remain excluded. The tests cover exact future
exit opens, unchanged earlier exits after changing a later price, common gap
exclusions, fixed nonoverlap selection and a credential-free CLI report.

These are fixed exits, not profit targets, trailing stops or position management.
Do not interpret the output as five-second option trades, executable prices,
portfolio P&L or a fresh holdout. Option quotes and a new future evaluation are
still required before drawing conclusions about the proposed options strategy.

### Frozen future-session study

The read-only service now runs a separate background study every five minutes.
It freezes its rules and start date at first launch in `forward-plan.json`,
beginning on the next Eastern calendar date (non-exchange days are skipped).
It collects only completed exchange sessions on or after that date into a
separate `forward-history.sqlite`, never importing the viewed historical sample.
The frozen candidate is the same completed-minute trend/volume signal on
SPY/QQQ/IWM, entries from 09:30 inclusive to 10:30 exclusive Eastern, and a
five-minute fixed exit. References are opening-hour 15-minute exits and matched
always-up/down movements; the all-day 15-minute reference is also retained.
All horizons keep identical entry windows and 15-minute selection spacing.

Results are future-session stock replays performed after close, not real-time
signals, option fills or brokerage paper orders. Requests use separate read-only
clients in a background thread so they do not block one-second management.
Unfinished sessions are not downloaded/checkpointed. Missing windows remain
excluded. Durable session transactions permit restart/resume. A study lock
prevents competing writers; the database pins the study identity. A source hash
covers the signal and evaluation functions. Changed rules, edited study dates
or mismatched saved results refuse reuse rather than silently mixing versions.

`forward-results.json` retains full coverage and daily subgroup results.
The local dashboard's **Fresh-session test** panel shows readiness, frozen start,
completed sessions, candidate windows and equal-weight daily means. A failed
study check is displayed independently of the options worker. No sample size or
favorable result automatically enables trading. Fees, option prices, execution,
immediate re-entry and five-second behavior remain untested.

To install the new background task and refresh the local dashboard:

```bash
git pull --ff-only
python -m scout_options.local_runner --update
```

This reuses the user's saved credentials and current free feed. No paid data or
hosting is activated. Windows sign-in startup still requires local verification.
Offline tests cover frozen-date persistence, code changes, edited study identity,
pre-start/no-network behavior, unfinished sessions, one-day reporting, resume,
old-data exclusion, missing windows, network failure and competing writers.

### Timestamped option observations

The existing shortlisted option subscriptions now enqueue quote observations to
`option-quotes.sqlite` through a separate SQLite writer thread. Each row retains
the SDK quote timestamp, desktop receipt timestamp, bid/ask, sizes, exchange
codes, conditions, feed and available contract metadata. Signal observations are
also recorded before contract discovery. This is a tape of received updates,
not a full option-chain archive or execution simulator. No extra subscriptions,
paid data or broker orders are activated. The supervisor still forces the free
indicative feed; those quotes are modified and are not executable OPRA prices.

The SDK exposes Python datetimes: original feed nanoseconds are not preserved.
Arrival delays can be negative if clocks differ. Crossed/zero-price/zero-size
quotes are retained with quality labels; invalid provenance and nonfinite
values are dropped and counted. A bounded 10,000-event queue avoids database
writes in the stream callback. The writer pauses once database plus WAL storage
reaches the default 256 MiB threshold, with at most one batch of overshoot. It
does not delete existing observations. Each process run has a durable start/end
marker and drop counters; an unclosed run or any drops prevents assuming a
continuous quote path. Disconnects, subscription coverage and source quality
still need consideration even in runs with no recorded drops.

The dashboard **Option quote recorder** panel shows subscriptions, saved quotes
this run, drops, timestamps and queued events. An empty tape after market close
is expected. Quotes are recorded only for contracts the scanner has subscribed
to; missing Greeks or failed contract discovery can leave no subscriptions.
There is no option-return/target-hit evaluator yet.

```bash
python -m scout_options.quote_recorder
```

This optional access probe reuses privately saved credentials. It chooses one
standard active QQQ contract from a bounded metadata request and separately
requests the latest indicative and OPRA quotes. It reports request acceptance,
quote timestamp or sanitized HTTP status, never secret values or remote error
bodies. An accepted REST request does not establish stream entitlement,
freshness or executable fills; an empty contract response is inconclusive.
The probe cannot buy data or change an account's subscriptions. Actual user
entitlement and a market-open stream test remain pending desktop verification.
