"""Paper-account reporting only. This module has no order submission capability."""
import base64
import csv
import io
import json
import os
import re
from datetime import datetime, timezone
from decimal import Decimal
from html import escape
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def fetch_orders():
    headers = {'APCA-API-KEY-ID': os.environ['ALPACA_API_KEY'],
               'APCA-API-SECRET-KEY': os.environ['ALPACA_SECRET_KEY']}
    orders, seen, cursor = [], set(), None
    for _ in range(50):
        params = dict(status='all', limit=500, direction='desc', nested='false')
        if cursor:
            params['before_order_id'] = cursor
        request = Request('https://paper-api.alpaca.markets/v2/orders?' + urlencode(params),
                          headers=headers, method='GET')
        with urlopen(request, timeout=30) as response:
            page = json.load(response)
        if not isinstance(page, list):
            raise ValueError('Unexpected order history response')
        fresh = [o for o in page if o['id'] not in seen]
        orders.extend(fresh)
        seen.update(o['id'] for o in fresh)
        if len(page) < 500:
            return orders
        if not fresh:
            raise ValueError('Order history pagination did not advance')
        cursor = page[-1]['id']
    raise ValueError('Order history exceeds reporting limit; totals withheld')


def loss_rows(orders, tracked):
    """Match long-stock fills FIFO across account history, label Scout ownership.

    Missing/partial history is visible, never silently counted as zero cost.
    """
    lots, rows, uncertain = {}, [], set()
    filled = []
    for o in orders:
        if o.get('asset_class', 'us_equity') != 'us_equity':
            continue
        qty = Decimal(str(o.get('filled_qty') or 0))
        if qty <= 0:
            continue
        if o.get('status') != 'filled' or not o.get('filled_at') or not o.get('filled_avg_price'):
            uncertain.add(o['symbol'])
            continue
        filled.append(o)
    filled.sort(key=lambda o: (datetime.fromisoformat(o['filled_at'].replace('Z', '+00:00')), o['id']))
    for o in filled:
        symbol = o['symbol']
        qty = Decimal(str(o['filled_qty']))
        price = Decimal(str(o['filled_avg_price']))
        if o['side'] == 'buy':
            lots.setdefault(symbol, []).append([qty, price, o['filled_at']])
            continue
        if o['side'] != 'sell':
            continue
        remaining, cost, entries = qty, Decimal(0), []
        available = lots.setdefault(symbol, [])
        while remaining > 0 and available:
            lot = available[0]
            matched = min(remaining, lot[0])
            cost += matched * lot[1]
            entries.append(lot[2])
            remaining -= matched
            lot[0] -= matched
            if lot[0] == 0:
                available.pop(0)
        if remaining:
            uncertain.add(symbol)
        known = not remaining and symbol not in uncertain and cost > 0
        pnl = qty * price - cost if known else None
        # Retain unresolved sales, including ones whose loss cannot yet be established.
        if pnl is not None and pnl >= 0:
            continue
        metadata = tracked.get(o['id'], {})
        owned = o['id'] in tracked or str(o.get('client_order_id', '')).lower().startswith('scout-')
        rows.append(dict(symbol=symbol, sale_id=o['id'], scout=owned,
            entry_date=entries[0] if known else None,
            last_entry_date=entries[-1] if known else None,
            exit_date=o['filled_at'], qty=str(qty),
            entry_price=str(cost / qty) if known else None, exit_price=str(price),
            loss_dollars=str(-pnl) if pnl is not None else None,
            loss_percent=str(-pnl / cost * 100) if pnl is not None else None,
            reason=metadata.get('reason', 'Historical exit; reason not recorded'),
            status='Confirmed loss' if known else 'Needs entry/fill reconciliation'))
    return rows


def refresh_loss_log(journal, fetcher=None):
    try:
        orders = (fetcher or fetch_orders)()
        rows = loss_rows(orders, journal.get('orders', {}))
        journal['loss_log'] = dict(rows=rows, checked_at=datetime.now(timezone.utc).isoformat(),
                                  status='History checked', orders_checked=len(orders))
    except Exception as exc:
        log = journal.setdefault('loss_log', {'rows': []})
        log['status'] = 'History refresh unavailable; saved results only (' + type(exc).__name__ + ').'
    return journal['loss_log']


def loss_csv(log):
    output = io.StringIO(newline='')
    fields = ['symbol', 'scout', 'status', 'entry_date', 'last_entry_date', 'exit_date',
              'qty', 'entry_price', 'exit_price', 'loss_dollars', 'loss_percent', 'reason']
    writer = csv.DictWriter(output, fieldnames=fields, extrasaction='ignore')
    writer.writeheader()
    for row in log.get('rows', []):
        safe = dict(row)
        for key, val in safe.items():
            if isinstance(val, str) and val.startswith(('=', '+', '-', '@', '\t', '\r')):
                safe[key] = "'" + val
        writer.writerow(safe)
    return output.getvalue()


def loss_log_html(journal):
    log = journal.get('loss_log', {})
    rows = log.get('rows', [])
    scout = [r for r in rows if r['scout']]
    other = [r for r in rows if not r['scout']]
    confirmed = [r for r in scout if r['loss_dollars'] is not None]
    pending = sum(r['loss_dollars'] is None for r in scout)
    total = sum((Decimal(r['loss_dollars']) for r in confirmed), Decimal(0))
    def cards(items):
        out = []
        for r in reversed(items):
            entry = f"${Decimal(r['entry_price']):,.4f}" if r['entry_price'] else 'Not verified'
            result = (f"${Decimal(r['loss_dollars']):,.2f} loss ({Decimal(r['loss_percent']):.2f}%)"
                      if r['loss_dollars'] is not None else 'Loss amount not yet verified')
            out.append(f'<article class="card"><b>{escape(r["symbol"])}</b>'
                       f'<p class="bad">{result}</p><p>Entry: {entry} → Exit: ${Decimal(r["exit_price"]):,.4f}'
                       f'<br>Shares sold: {escape(r["qty"])}</p>'
                       f'<p>Entry date: {escape(r["entry_date"] or "Not verified")}'
                       f'<br>Exit date: {escape(r["exit_date"])}</p>'
                       f'<p>{escape(r["reason"])}</p></article>')
        return ''.join(out)
    download = base64.b64encode(loss_csv(log).encode('utf-8-sig')).decode('ascii')
    return ('<section class="panel" id="loss-log"><h2>Scout’s loss log</h2>'
            f'<p><b>{len(confirmed)} confirmed losing sales · ${total:,.2f} total gross loss</b>'
            f'<br>{pending} Scout sales awaiting entry/fill verification.</p>'
            f'<p>{escape(log.get("status", "Waiting for paper-account history."))}'
            f'<br><small>{escape(log.get("checked_at", ""))}</small></p>'
            f'{cards(scout) or "<p>No verified Scout losses recorded yet.</p>"}'
            f'<details><summary>Other paper-account sales: {len(other)} — Scout ownership unverified</summary>{cards(other)}</details>'
            f'<p><a style="color:#8dc5ff" download="scout-loss-log.csv" href="data:text/csv;base64,{download}">Download loss log</a></p>'
            '<p class="sub">Closed long-stock sales only. Losses are positive amounts lost, before fees; '
            'open-position declines are excluded. Entry price is the weighted cost of matched purchases '
            '(oldest purchases first). Multiple entries use the earliest matched entry date. '
            'Unresolved sales are excluded from totals. Older sales without a Scout order tag are listed separately. '
            'Stock splits, transfers or incomplete fills may require broker reconciliation.</p></section>')


def main():
    state_path = Path('.scout/scout_state_v43.json')
    state = json.loads(state_path.read_text(encoding='utf-8'))
    journal = state.setdefault('activity_journal', {'orders': {}})
    log = refresh_loss_log(journal)
    if log['status'] != 'History checked':
        raise RuntimeError(log['status'])
    page = Path('index.html')
    html = page.read_text(encoding='utf-8')
    from scout_journal import journal_html
    stamp = datetime.fromisoformat(state['last_cycle'])
    report = dict(updated=stamp.strftime('%b %d, %Y %I:%M:%S %p PT'),
                  journal_date=stamp.date().isoformat(), market_open='>OPEN</div>' in html)
    start = html.find('<section class="panel" id="loss-log">')
    if start < 0:
        start = html.find('<section class="panel"><h2>Today’s summary')
    end = html.find('<div class="panel"><h2>Position replacement', start)
    if start < 0 or end < 0:
        raise ValueError('Dashboard journal section not found; leaving saved files unchanged')
    html = html[:start] + journal_html(journal, report) + html[end:]
    page.write_text(html, encoding='utf-8')
    temp = state_path.with_suffix('.tmp')
    temp.write_text(json.dumps(state, indent=2), encoding='utf-8')
    temp.replace(state_path)
    Path('scout-loss-log.csv').write_text(loss_csv(log), encoding='utf-8-sig')
    print(f"Read-only loss report: {len(log['rows'])} losing or unresolved sales. No orders submitted.")


if __name__ == '__main__':
    main()

