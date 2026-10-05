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

Validation: all 212 offline tests pass (options lifecycle, dashboard, paper outbox, healthcheck and status-server tests); the 10-second
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
