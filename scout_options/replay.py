"""Chronological scanner replay on stock bars, not an options P&L backtest."""
from datetime import datetime, timedelta
from .core import directional_signal
from .history import SYMBOLS


def evaluate_session(symbol, bars, horizon=15):
    """Signal uses closed bars; entry/exit use later minute opens in one session."""
    results = []
    for index in range(20,len(bars)-horizon-1):
        window = bars[index-20:index+horizon+2]
        if any((b[0]-a[0]).total_seconds() != 60 for a,b in zip(window,window[1:])):
            continue
        past = [(at,close,volume) for at,opened,close,volume in bars[index-20:index+1]]
        signal = directional_signal(symbol,past,bars[index][0]+timedelta(minutes=1))
        if signal is None:
            continue
        entry = bars[index+1][1]
        exit_price = bars[index+1+horizon][1]
        move = (exit_price/entry-1)*10000
        signed = move if signal.direction == 'CALL' else -move
        results.append(dict(direction=signal.direction,at=signal.at.isoformat(),
                            entry_at=bars[index+1][0].isoformat(),signed_move_bps=signed))
    return results


def metrics(samples):
    moves = [s['signed_move_bps'] for s in samples]
    return dict(observations=len(moves),positive_direction_fraction=
                sum(m>0 for m in moves)/len(moves) if moves else None,
                average_signed_stock_move_bps=sum(moves)/len(moves) if moves else None)


def replay(db,start,end,horizon=15):
    days = [r[0] for r in db.execute('SELECT DISTINCT session FROM bars WHERE session>=? AND session<? ORDER BY session',
                                    (start.isoformat(),end.isoformat()))]
    if len(days) < 2:
        return dict(status='Need at least two saved exchange sessions',sessions_available=len(days))
    split = min(len(days)-1,max(1,int(len(days)*.7)))
    samples = {'earlier':[],'held_out':[]}
    for day in days:
        group = 'earlier' if day < days[split] else 'held_out'
        for symbol in SYMBOLS:
            bars = [(datetime.fromisoformat(at),o,c,v) for at,o,c,v in db.execute(
                'SELECT at,open,close,volume FROM bars WHERE session=? AND symbol=? ORDER BY at',(day,symbol))]
            samples[group].extend(dict(s,underlying=symbol) for s in evaluate_session(symbol,bars,horizon))
    return dict(status='Stock-signal replay only — no option P&L or fills',holding_minutes=horizon,
        entry='Next minute open after completed signal; exit at a later minute open',
        held_out_start=days[split],sessions_available=len(days),
        limitations=['IEX is one exchange; missing minute windows are excluded.',
                     'Repeated signals overlap; observations are not independent trades.',
                     'No option premiums, spreads, fees, slippage, or portfolio sizing modeled.',
                     'Earlier/held-out split is chronological; do not tune on held-out results.'],
        results={group:dict(overall=metrics(rows),
                    by_direction={d:metrics([s for s in rows if s['direction']==d]) for d in ('CALL','PUT')},
                    by_underlying={u:metrics([s for s in rows if s['underlying']==u]) for u in SYMBOLS})
                 for group,rows in samples.items()})
