"""Observational strategy desk and opt-in research guards. No broker access.

The desk consumes cycle snapshots; it never changes order eligibility. The
pause experiment requires caller-supplied, cash-flow-adjusted session P&L.
"""
from dataclasses import dataclass
from datetime import datetime
from html import escape
from math import isfinite
from zoneinfo import ZoneInfo

from scout_journal import matched_sales


def number(value):
    try:
        result = float(value)
        return result if isfinite(result) else None
    except (TypeError, ValueError):
        return None


def build_desk(report, config):
    """Summarize actual Scout decisions separately from Shadow observations."""
    positions = report.get('positions', [])
    candidates = report.get('candidates', [])
    decisions = [dict(symbol=p['symbol'], stage='MANAGING',
                      action=p.get('order_status', 'Unknown'),
                      reason='; '.join(p.get('reasons', [])) or p.get('decision', 'HOLD'))
                 for p in positions]
    decisions.extend(dict(symbol=c['symbol'], stage='ENTRY CHECK',
                          action=c.get('order_status', 'Unknown'),
                          reason=f"Entry score {c['entry_score']}/9; order status is not a fill.")
                     for c in candidates)
    sales = matched_sales(report.get('journal', {}))
    known = [s['pnl'] for s in sales if s['pnl'] is not None]
    results = dict(matched_sales=len(known), unknown_sales=len(sales)-len(known),
                   realized_pnl=sum(known) if known else None,
                   wins=sum(p > 0 for p in known), losses=sum(p < 0 for p in known))
    equity, cash = number(report.get('equity')), number(report.get('cash'))
    values = [number(p.get('market_value')) for p in positions]
    risk = dict(status='UNAVAILABLE')
    if equity is not None and equity > 0 and cash is not None and all(v is not None for v in values):
        gross = sum(abs(v) for v in values)
        reserve = equity * config.minimum_cash_reserve_fraction
        risk = dict(status='SNAPSHOT', gross_exposure_pct=100*gross/equity,
                    largest_position_pct=100*max([abs(v) for v in values], default=0)/equity,
                    cash_reserve=reserve, cash_headroom=max(0, cash-reserve),
                    slots=max(0, config.max_positions-len(positions)))
    shadow = report.get('shadow', {})
    workers = []
    for direction in ('LONG', 'SHORT'):
        rows = shadow.get(direction.lower(), [])
        workers.append(dict(name='Shadow '+direction.lower(), mode='OBSERVATION ONLY',
                            status=shadow.get('status', 'UNAVAILABLE'),
                            qualified=shadow.get('qualified_'+direction.lower(), 0),
                            decisions=[dict(symbol=r['symbol'], stage=r['decision'],
                                            action='No orders', reason=timing_reason(r)) for r in rows]))
    return dict(version=1, updated=report.get('updated'), market_open=report.get('market_open', False),
                workers=[dict(name='Scout stock engine', mode='PAPER', status='CURRENT SNAPSHOT',
                              decisions=decisions, results=results), *workers], risk=risk)


def timing_reason(row):
    labels = {'not_extended': (30, 'price extension'), 'momentum_live': (25, 'momentum'),
              'rsi_window': (20, 'RSI window'), 'volume_confirm': (15, 'volume'),
              'relative_strength': (10, 'relative strength'),
              'relative_weakness': (10, 'relative weakness')}
    weak = [label for key, (maximum, label) in labels.items()
            if key in row.get('entry_parts', {}) and row['entry_parts'][key] < maximum]
    text = f"Setup {row['setup_score']}/100; timing {row['entry_score']}/{row['required_entry']} required."
    if weak:
        text += ' Below full points: '+', '.join(weak)+'.'
    return text


def evaluate_entry_risk(equity, cash, holdings, proposals, pending_symbols, config):
    """Sequential shared-budget research check, never an execution gate.

    Unknown pending-order amounts block this experiment rather than pretending
    their cash is free. Stop-loss estimates are sizing assumptions, not limits
    on gap losses. Each accepted proposal reserves cash and a position slot.
    """
    equity, cash = number(equity), number(cash)
    held = {str(s).upper() for s in holdings}
    if equity is None or equity <= 0 or cash is None or cash < 0:
        return [dict(symbol=p.get('symbol'), allowed=False, reason='ACCOUNT DATA UNAVAILABLE') for p in proposals]
    budget = max(0., cash-equity*config.minimum_cash_reserve_fraction)
    results = []
    for proposal in proposals:
        symbol = str(proposal.get('symbol', '')).upper()
        amount = number(proposal.get('notional'))
        reason = None
        if pending_symbols:
            reason = 'PENDING ORDER AMOUNTS UNKNOWN'
        elif not symbol or amount is None or amount < config.minimum_order_dollars:
            reason = 'INVALID OR BELOW MINIMUM NOTIONAL'
        elif symbol in held:
            reason = 'DUPLICATE EXPOSURE'
        elif len(held) >= config.max_positions:
            reason = 'POSITION LIMIT'
        elif amount > equity*config.position_fraction:
            reason = 'POSITION ALLOCATION LIMIT'
        elif amount*abs(config.hard_stop_pct)/100 > equity*config.risk_fraction_per_trade:
            reason = 'MODELED TRADE RISK LIMIT'
        elif amount > budget:
            reason = 'SHARED CASH RESERVE'
        if reason is None:
            budget -= amount
            held.add(symbol)
        results.append(dict(symbol=symbol, allowed=reason is None,
                            reason=reason or 'RESEARCH BUDGET RESERVED', remaining_budget=budget))
    return results


@dataclass(frozen=True)
class PausePolicy:
    loss_limit: float | None = None
    profit_activation: float | None = None
    max_giveback: float | None = None

    def __post_init__(self):
        for value in (self.loss_limit, self.profit_activation, self.max_giveback):
            if value is not None and (number(value) is None or value <= 0):
                raise ValueError('Research thresholds must be finite positive dollar amounts.')
        if (self.profit_activation is None) != (self.max_giveback is None):
            raise ValueError('Profit activation and giveback must be configured together.')


def evaluate_pause(saved, observed_at, market_open, session_pnl, policy):
    """Pure research evaluation; never stops management or submits orders.

    Caller provides session P&L including unrealized changes, adjusted for
    deposits/withdrawals. A pause latches through the session. Closed-market,
    duplicate and out-of-order observations do not advance research state.
    """
    if observed_at.tzinfo is None:
        raise ValueError('Research observations require a timezone-aware timestamp.')
    observed_at = observed_at.astimezone(ZoneInfo('America/Los_Angeles'))
    state = dict(saved)
    last = state.get('last_at')
    if not market_open or (last and observed_at <= datetime.fromisoformat(last)):
        return state, dict(status='NOT ADVANCED', allow_new_entries=False, manage_positions=True)
    pnl = number(session_pnl)
    if pnl is None:
        return state, dict(status='DATA UNAVAILABLE', allow_new_entries=False, manage_positions=True)
    session = observed_at.date().isoformat()
    if state.get('session') != session:
        state = dict(session=session, peak=0., paused=False, reason=None)
    state['peak'] = max(state['peak'], pnl)
    if not state['paused']:
        if policy.loss_limit is not None and pnl <= -policy.loss_limit:
            state.update(paused=True, reason='SESSION LOSS LIMIT')
        elif (policy.profit_activation is not None and state['peak'] >= policy.profit_activation
              and state['peak']-pnl >= policy.max_giveback):
            state.update(paused=True, reason='PROFIT GIVEBACK LIMIT')
    state['last_at'] = observed_at.isoformat()
    return state, dict(status=state['reason'] or 'RESEARCH OPEN',
                       allow_new_entries=not state['paused'], manage_positions=True)


def desk_html(desk):
    if not desk:
        return ''
    esc = lambda x: escape(str(x))
    chunks = ['<section class="panel"><h2>Strategy desk</h2>',
              '<p>Scout executes paper stock rules. Shadow long and short observe independently.</p>']
    risk = desk['risk']
    if risk['status'] == 'SNAPSHOT':
        chunks.append(f'<p>Gross held exposure: {risk["gross_exposure_pct"]:.1f}% · '
                      f'Largest holding: {risk["largest_position_pct"]:.1f}% · '
                      f'Cash above reserve: ${risk["cash_headroom"]:,.2f} · Open slots: {risk["slots"]}</p>')
    else:
        chunks.append('<p>Risk snapshot unavailable.</p>')
    for worker in desk['workers']:
        chunks.append(f'<details><summary>{esc(worker["name"])} · {esc(worker["mode"])}</summary>')
        if 'results' in worker:
            r = worker['results']
            pnl = f'${r["realized_pnl"]:+,.2f}' if r['realized_pnl'] is not None else 'Unavailable'
            chunks.append(f'<p>Tracked matched-sale P&amp;L: {pnl} · {r["matched_sales"]} matched sales · '
                          f'{r["unknown_sales"]} sales with unknown cost. {r["wins"]} wins / {r["losses"]} losses.</p>')
        else:
            chunks.append(f'<p>{esc(worker["status"])} · {worker["qualified"]} qualified signals. No trade P&amp;L.</p>')
        for d in worker['decisions'][:6]:
            chunks.append(f'<div class="activity-row"><b>{esc(d["symbol"])} · {esc(d["stage"])}</b>'
                          f'<p>{esc(d["action"])}<br>{esc(d["reason"])}</p></div>')
        if not worker['decisions']:
            chunks.append('<p>No decisions available this cycle.</p>')
        chunks.append('</details>')
    chunks.append('<p class="sub">Snapshot taken before this cycle’s orders; pending orders and correlations '
                  'are not included. Matched sales cover tracked filled paper orders only, before fees, '
                  'not whole-account or daily returns. Profit-pause experiment is built but inactive; '
                  'existing position management continues. No new execution rules enabled.</p></section>')
    return ''.join(chunks)
