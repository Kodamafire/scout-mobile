"""Read-only reporting for Scout decisions and tracked paper-order fills."""
from datetime import datetime
from html import escape
from decimal import Decimal
from scout_loss_log import loss_log_html


TERMINAL = {'filled', 'canceled', 'cancelled', 'expired', 'rejected'}


def value(item):
    return str(getattr(item, 'value', item)).lower().rsplit('.', 1)[-1]


def record_cycle(report, state, trading):
    journal = state.setdefault('activity_journal', {'events': [], 'orders': {}, 'cycles': []})
    cycle = report['cycle']
    stamp = state['last_cycle']
    if cycle not in journal['cycles']:
        journal['cycles'].append(cycle)
        journal['cycles'] = journal['cycles'][-500:]
        entries = []
        for p in report['positions']:
            status = p['order_status']
            if status.startswith('SUBMITTED '):
                reason = '; '.join(p.get('reasons', [])) or 'Exit conditions met'
                entries.append((p['symbol'], 'Sell requested', reason, status[10:]))
            else:
                reason = ('Exit conditions not met.' if not p.get('exit_confirmed')
                          else 'Exit confirmed; ' + status.lower() + '.')
                if p.get('decision') == 'DATA UNAVAILABLE':
                    reason = 'Chart data unavailable; account loss check remains active.'
                if p.get('confirmation', 0) and not p.get('exit_confirmed'):
                    reason = f"Waiting for exit confirmation ({p['confirmation']} checks)."
                entries.append((p['symbol'], 'Position checked', reason, None))
        for c in report['candidates']:
            if c['order_status'].startswith('SUBMITTED '):
                entries.append((c['symbol'], 'Buy requested',
                                f"Entry score {c['entry_score']}/9 met the entry rules.", c['order_status'][10:]))
        upgrade = report.get('upgrade')
        if upgrade:
            pair = f"{upgrade['replace_symbol']} → {upgrade['candidate']['symbol']}"
            reason = (f"{upgrade.get('upgrade_confirmation', 0)} of "
                      f"{upgrade.get('upgrade_confirmations_required', 3)} checks confirmed. "
                      + upgrade.get('order_status', 'Waiting') + '.')
            if not report['market_open']:
                reason += ' Market closed; confirmations do not advance.'
            entries.append((pair, 'Replacement check', reason, None))
            rotation = upgrade.get('rotation', {})
            for side, symbol in [('sell', upgrade['replace_symbol']), ('buy', upgrade['candidate']['symbol'])]:
                if rotation.get(side + '_order_id'):
                    entries.append((symbol, side.title() + ' requested', 'Portfolio replacement: ' + pair,
                                    rotation[side + '_order_id']))
        for symbol, action, reason, order_id in entries:
            event = dict(at=stamp, cycle=cycle, symbol=symbol, action=action, reason=reason)
            journal['events'].append(event)
            if order_id:
                journal['orders'].setdefault(order_id, dict(symbol=symbol, reason=reason,
                                                          status='submitted', requested_at=stamp))
        journal['events'] = journal['events'][-1000:]

    errors = 0
    for order_id, saved in journal['orders'].items():
        if saved['status'] in TERMINAL:
            continue
        try:
            order = trading.get_order_by_id(order_id)
            status = value(order.status)
            qty = str(getattr(order, 'filled_qty', 0) or 0)
            price = getattr(order, 'filled_avg_price', None)
            filled_at = getattr(order, 'filled_at', None)
            if (status, qty) != (saved['status'], saved.get('qty', '0')):
                journal['events'].append(dict(at=stamp, cycle=cycle, symbol=saved['symbol'],
                    action='Order ' + status.replace('_', ' '),
                    reason=f"{value(order.side).title()}: {qty} shares filled" +
                           (f" at average ${Decimal(str(price)):.4f}." if price else '.')))
            saved.update(status=status, qty=qty, price=str(price) if price else None,
                         side=value(order.side), filled_at=filled_at.isoformat() if filled_at else None)
        except Exception:
            errors += 1
    journal['events'] = journal['events'][-1000:]
    journal['sync_note'] = (f'{errors} order updates unavailable; last known status shown.' if errors
                            else 'Order status checked during the latest cycle.')
    return journal


def matched_sales(journal):
    """FIFO matching of fully filled tracked orders; never invent opening cost."""
    lots, sales = {}, []
    uncertain = {o['symbol'] for o in journal.get('orders', {}).values()
                 if o.get('status') != 'filled' and Decimal(o.get('qty', '0')) > 0}
    orders = sorted((o for o in journal.get('orders', {}).values()
                     if o.get('status') == 'filled' and o.get('filled_at') and o.get('price')),
                    key=lambda o: (datetime.fromisoformat(o['filled_at']), 0 if o.get('side') == 'buy' else 1))
    for o in orders:
        symbol = o['symbol']
        qty, price = Decimal(o['qty']), Decimal(o['price'])
        if qty <= 0:
            continue
        if o['side'] == 'buy':
            lots.setdefault(symbol, []).append([qty, price, o['filled_at']])
            continue
        if o['side'] != 'sell':
            continue
        remaining, cost, weighted_days = qty, Decimal(0), Decimal(0)
        available = lots.setdefault(symbol, [])
        while remaining > 0 and available:
            lot = available[0]
            matched = min(remaining, lot[0])
            cost += matched * lot[1]
            days = (datetime.fromisoformat(o['filled_at']) - datetime.fromisoformat(lot[2])).total_seconds() / 86400
            weighted_days += matched * Decimal(str(days))
            remaining -= matched
            lot[0] -= matched
            if lot[0] == 0:
                available.pop(0)
        sales.append(dict(symbol=symbol, at=o['filled_at'], qty=str(qty),
                          pnl=float(qty * price - cost) if not remaining and symbol not in uncertain else None,
                          days=float(weighted_days / qty) if not remaining and symbol not in uncertain else None,
                          reason=o.get('reason', 'Reason not recorded')))
    return sales


def journal_html(journal, report):
    esc = lambda x: escape(str(x))
    events = journal.get('events', [])
    latest_date = report.get('journal_date', '')
    today = [e for e in events if e['at'][:10] == latest_date]
    requests = sum(e['action'] in ('Buy requested', 'Sell requested') for e in today)
    cycles = len({e['cycle'] for e in today})
    summary = (f"{cycles} recorded checks; {requests} order requests. "
               + ('Market open at the latest check.' if report['market_open'] else 'Market closed at the latest check.'))
    if not events:
        summary = 'The journal is ready. Activity will appear after Scout’s next scheduled cycle.'
    activity = ''.join(f"<li><b>{esc(e['symbol'])} · {esc(e['action'])}</b><br>{esc(e['reason'])}"
                       f"<br><small>{esc(e['at'])}</small></li>" for e in reversed(events[-40:]))
    sales = []
    for sale in reversed(matched_sales(journal)[-20:]):
        outcome = ('Result unavailable—opening fills are incomplete or a partial order needs reconciliation.' if sale['pnl'] is None
                   else f"Gross result ${sale['pnl']:+,.2f}; held {sale['days']:.1f} calendar days (quantity-weighted).")
        for loss in journal.get('loss_log', {}).get('rows', []):
            if (loss['symbol'] == sale['symbol'] and loss.get('loss_dollars') is not None
                    and datetime.fromisoformat(loss['exit_date'].replace('Z', '+00:00')) == datetime.fromisoformat(sale['at'])):
                outcome = f"Gross loss ${Decimal(loss['loss_dollars']):,.2f}; entry and exit details are in the loss log above."
                break
        sales.append(f"<li><b>{esc(sale['symbol'])}</b> · {esc(sale['qty'])} shares sold<br>"
                     f"{esc(outcome)}<br>{esc(sale['reason'])}<br><small>{esc(sale['at'])}</small></li>")
    return (loss_log_html(journal) + f'<section class="panel"><h2>Today’s summary</h2><p>{esc(summary)}</p>'
            f'<p class="sub">As of the saved cycle: {esc(report["updated"])}. This is not a live account feed.</p></section>'
            '<section class="panel"><h2>Scout’s Activity</h2>'
            f'<p>{esc(journal.get("sync_note", "Waiting for the first journal cycle."))}</p>'
            f'<details open><summary>Recent decisions and order updates</summary><ul>{activity or "<li>No activity recorded yet.</li>"}</ul></details></section>'
            '<section class="panel"><h2>Completed sales</h2>'
            f'<ul>{"".join(sales) or "<li>No fully filled sales recorded yet.</li>"}</ul>'
            '<p class="sub">Tracked paper orders only, starting when this journal was installed. '
            'Results match fully filled buy and sell orders in order of purchase, use actual average fill prices, '
            'and exclude fees. Existing holdings without recorded opening fills show no result. '
            'Partial orders are shown in activity but excluded from these results. '
            'This is not total account performance.</p></section>')

