# Always-on Scout options research service

This package runs the existing **read-only Alpaca adapter and local quote
simulator**, not brokerage execution. No service has been deployed. The new
`paper_gateway.py` is separately staged and is not imported by this worker.

Use one Docker-capable always-on host and persistent disk. Put the paper API key
and secret in that host's secret store as `ALPACA_API_KEY` and
`ALPACA_SECRET_KEY`; set `SCOUT_OPTIONS_CAPITAL` to an explicit virtual research
amount. Never paste secrets into ChatGPT or commit them. Feed access must include
OPRA; this package does not purchase a subscription or enable account permissions.

From the checkout root, with those variables supplied securely:

```bash
docker compose -f deploy/options/compose.yaml up --build -d
docker compose -f deploy/options/compose.yaml ps
docker compose -f deploy/options/compose.yaml logs --tail=50 worker
```

The dashboard is available on the **host itself** at
`http://127.0.0.1:8080/options.html`. For remote use, connect through an SSH tunnel
or an authenticated reverse proxy. No public port or public status upload is
configured. The status server serves only the HTML and status JSON, never the
SQLite ledger. The existing GitHub Pages dashboard remains disconnected.

Worker uses a non-root account, read-only container filesystem, persistent
`options-data` volume, bounded logs and automatic restart on process exit. Its
healthcheck checks the one-second heartbeat; it does **not** assert OPRA access,
broker reconciliation, alert delivery or trading readiness. Docker reports an
unhealthy process but does not itself restart it solely for unhealthy status.
Host supervision must alert/restart a stalled worker. Do not delete the volume
when restarting; it holds the ledger and durable intents. A database snapshot
must be consistent with SQLite WAL, not an isolated copy of the main file.

Validation here: Python service, healthcheck and HTTP allowlist tested offline.
Docker is unavailable in the development environment, so the image build and
host operation are unverified. OPRA subscription rotation and external urgent
alerts remain to be implemented before unattended use.

## Staged paper-order gateway

`PaperOutbox` commits a stable client-order ID before submission, reconciles by
that ID after a timeout/restart, and never automatically resends an ambiguous
request. The transport hardcodes Alpaca paper mode. It requests DAY limit orders
with buy-to-open/sell-to-close; sells cannot exceed broker-confirmed bot holdings.
Cumulative partial fills are counted once; cancellation remains pending until the
broker confirms it. Unknown positions, fill regressions and identity mismatches
block readiness rather than adopting or liquidating holdings.

It is **not execution-ready**: no scanner/risk coordinator arms it, no CLI
activates it, and no automatic paper orders have been sent. Connecting it needs
an authoritative fresh account/order/position snapshot, a shared atomic
stock/options budget, account/contract permissions, observed live feed, expiry
reconciliation and urgent alert delivery. Do not copy simulated fills into the
broker ledger or arm this component directly. After uncertain sends that remain
not-found, an operator must resolve the original client ID before any new intent.

The stock runner and its schedule are unchanged.
