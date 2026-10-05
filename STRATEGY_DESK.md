# Scout strategy desk: first implementation

The two reference videos inspired separate strategy visibility, explicit
decision stages, shared budgets, and pausing new entries while managing winners.
They do not disclose a reproducible strategy or verify profitability.

## Included

- A compact dashboard desk for the existing Scout stock engine and Shadow's
  long/short observation channels. These are existing channels, not three newly
  built independent trading engines. Options remain excluded.
- Decision reasons, order-request status, gross held exposure, largest position,
  cash above the configured reserve, and available position slots.
- Tracked FIFO matched-sale paper P&L from filled journal orders. Unmatched
  opening costs are unknown; submitted orders never count as profits. This is
  incomplete tracked realized P&L before fees, not daily or total account return.
- Desk snapshots and module hashes recorded with existing research inputs.
- Pure research functions for sequential shared-budget checks and session pause
  policies. They have no broker access and are not connected to order execution.

## Research controls

`evaluate_entry_risk` applies the existing configuration's position slots,
allocation cap, modeled per-trade stop risk, minimum order and cash reserve to
a sequence of proposed entries. Each accepted proposal reserves a slot and cash
for later proposals. Duplicate symbols share the same limit. Any pending orders
block research eligibility until their committed amounts can be reconciled.
This does not model sector/factor correlation or total account gap losses.

`evaluate_pause` accepts externally calculated session P&L, including unrealized
changes and adjusted for deposits/withdrawals. Do not pass lifetime journal P&L
or an unadjusted equity difference. All thresholds default to disabled and are
research dollar amounts, not adopted recommendations. Optional loss limits and
peak-profit giveback limits latch until the next Pacific trading session.
Missing P&L blocks research entry permission; duplicate, stale and closed-market
checks do not advance state. Existing positions always continue to be managed.
The caller must persist returned state. This experiment has not been wired to a
forward P&L stream and has not been backtested for profitability.

## Scope and next validation

Scout's execution gates, runner exits, paper-only lock and options hold remain
unchanged. The new desk fails independently so a reporting problem cannot stop
the existing cycle. Account/position snapshots precede that cycle's orders;
displayed exposure excludes pending fills and does not certify entry safety.

Before activating any new controls: reconcile order reservations and cash flows,
wire separate forward paper portfolios for each strategy, then compare no pause,
fixed-target pause, and peak-giveback pause with identical entry rules and
realistic delayed fills/costs. Evaluate fresh data, losing sessions, profits
missed by pausing, drawdown and turnover. Test correlated simultaneous signals.

Validation: `python -m unittest discover -q`.
