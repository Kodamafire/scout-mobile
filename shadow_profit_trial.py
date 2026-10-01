"""Forward-only reference simulation; never imports a broker or submits orders."""
from datetime import datetime
from html import escape
from math import isfinite

COST = 0.001  # Assumed 0.10% adverse execution cost per simulated side.


def update_trial(state, shadow, positions, observed_at, market_open, cost=COST):
    trial = state.setdefault('profit_trial', {'version': 1, 'rows': {}, 'last_at': None})
    if not market_open or shadow.get('status') != 'OK':
        return trial
    stamp = observed_at.isoformat()
    if trial['last_at'] and observed_at <= datetime.fromisoformat(trial['last_at']):
        return trial
    trial['cost_per_side'] = cost
    long_rows = {r['symbol']: r for r in shadow.get('evidence', []) if r['direction'] == 'LONG'}
    # Held evidence remains available even outside the top-ranked shortlist.
    long_rows.update({r['symbol']: r for r in shadow.get('held', []) if r['direction'] == 'LONG'})
    managed = {p['symbol']: p for p in positions}
    for symbol in set(trial['rows']) | set(managed):
        signal = long_rows.get(symbol)
        if not signal:
            continue
        price = float(signal['price'])
        if not isfinite(price) or price <= 0:
            continue
        row = trial['rows'].get(symbol)
        if row is None:
            trial['rows'][symbol] = dict(started=stamp, base=price, last=price,
                units=1 / (price * (1 + cost)), cash=0., entry=price, peak=price,
                pending=None, fills=0, reentries=0, session=observed_at.date().isoformat(),
                confirmations=0, confirm_at=None, status='TRACKING', last_at=stamp)
            continue
        session = observed_at.date().isoformat()
        if row['session'] != session:
            row.update(session=session, reentries=0, confirmations=0, confirm_at=None)
            if row['pending'] == 'BUY':
                row.update(pending=None, status='RE-ENTRY RESET — new session')
        row['last'] = price
        row['last_at'] = stamp
        # Signals execute at the NEXT available observation, never at their signal price.
        if row['pending'] == 'SELL':
            row['cash'] = row['units'] * price * (1 - cost)
            row.update(units=0., pending=None, fills=row['fills'] + 1,
                       confirmations=0, confirm_at=None, status='SIMULATED CASH')
            continue
        if row['pending'] == 'BUY':
            row['units'] = row['cash'] / (price * (1 + cost))
            row.update(cash=0., entry=price, peak=price, pending=None,
                       fills=row['fills'] + 1, reentries=row['reentries'] + 1,
                       confirmations=0, confirm_at=None, status='SIMULATED RE-ENTRY')
            continue
        if row['units']:
            row['peak'] = max(row['peak'], price)
            peak_gain = (row['peak'] / row['entry'] - 1) * 100
            gain = (price / row['entry'] - 1) * 100
            drop = (1 - price / row['peak']) * 100
            weak = managed.get(symbol, {}).get('exit_score', 0) >= 3 or signal['entry_score'] < 70
            protected = peak_gain >= 1 and gain <= peak_gain * .5
            momentum_exit = peak_gain >= 1 and drop >= .75 and weak
            if gain <= -7.5 or protected or momentum_exit:
                row.update(pending='SELL', status='EXIT SIGNAL — next observation')
        else:
            qualified = signal['decision'] == 'QUALIFIED'
            if not qualified:
                row.update(confirmations=0, confirm_at=None)
            elif row['reentries'] < 2:
                previous = row['confirm_at']
                if previous is None or (observed_at - datetime.fromisoformat(previous)).total_seconds() >= 900:
                    row['confirmations'] += 1
                    row['confirm_at'] = stamp
                if row['confirmations'] >= 2:
                    row.update(pending='BUY', status='RE-ENTRY SIGNAL — next observation')
    trial['last_at'] = stamp
    return trial


def trial_html(trial):
    rows = []
    cost = trial.get('cost_per_side', COST)
    for symbol, r in sorted(trial.get('rows', {}).items()):
        # Compare both alternatives after the same assumed initial buy and final sale cost.
        hold = r['last'] / (r['base'] * (1 + cost)) * (1 - cost)
        value = r['cash'] + r['units'] * r['last'] * (1 - cost)
        rows.append(f'<li><b>{escape(symbol)}</b> · Hold {100*(hold-1):+.2f}% · '
                    f'Protect/re-enter {100*(value-1):+.2f}%<br>'
                    f'{escape(r["status"])} · {r["fills"]} simulated fills<br>'
                    f'<small>Started {escape(r["started"][:16])}; last observation '
                    f'{escape(r["last_at"][:16])} PT</small></li>')
    return ('<section class="panel"><h2>Shadow profit-protection trial</h2>'
            '<p>Forward simulation only — no orders. Compared from the same starting price.</p>'
            '<details><summary>See profit protection versus holding</summary><ul>'
            + (''.join(rows) or '<li>Waiting for a market-open observation.</li>') + '</ul>'
            '<p class="sub">Experimental settings: protection after +1%; retain half the peak gain; '
            'also exit after a 0.75% peak drop with weak momentum. Re-entry needs two qualified '
            'checks at least 15 minutes apart; maximum two re-entries per session. '
            'Signals fill at the next observed price with an assumed 0.10% cost per side. '
            'Returns include assumed liquidation costs. Reference prices are not executable quotes. '
            'Missing observations pause tracking; gaps can exceed the protective floor. '
            'Baseline is holding, not Scout’s actual trades.</p></details></section>')
