"""Chronological scanner replay on stock bars, not an options P&L backtest."""
from datetime import datetime, timedelta
from collections import defaultdict
from zoneinfo import ZoneInfo
from .core import directional_signal
from .history import SYMBOLS


def evaluate_session(symbol, bars, horizon=15):
    """Signal uses closed bars; entry/exit use later minute opens in one session."""
    if not isinstance(horizon, int) or horizon < 1:
        raise ValueError('Positive whole-minute horizon required')
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
                            entry_at=bars[index+1][0].isoformat(),
                            exit_at=bars[index+1+horizon][0].isoformat(),
                            stock_move_bps=move,signed_move_bps=signed))
    return results


def metrics(samples):
    moves = [s['signed_move_bps'] for s in samples]
    return dict(observations=len(moves),positive_direction_fraction=
                sum(m>0 for m in moves)/len(moves) if moves else None,
                average_signed_stock_move_bps=sum(moves)/len(moves) if moves else None)


def nonoverlapping(samples):
    """First eligible signal wins; no simultaneous windows within one underlying.

    This is a descriptive stock-window comparison, not portfolio execution.
    Different underlyings may still overlap and share market exposure.
    """
    available = {}
    selected = []
    for row in sorted(samples, key=lambda s: (s['entry_at'], s['underlying'])):
        key = row['underlying']
        entry = datetime.fromisoformat(row['entry_at'])
        if key in available and entry < available[key]:
            continue
        selected.append(row)
        available[key] = datetime.fromisoformat(row['exit_at'])
    return selected


def time_bucket(row):
    entry = datetime.fromisoformat(row['entry_at']).astimezone(ZoneInfo('America/New_York'))
    minute = entry.hour*60+entry.minute
    if minute < 10*60+30: return '09:30-10:30 ET'
    if minute < 14*60: return '10:30-14:00 ET'
    return '14:00-16:00 ET'


def comparison(samples):
    """Baselines use exactly the same symbols, timestamps and missing-data filter."""
    def baseline(sign):
        return metrics([dict(s,signed_move_bps=sign*s['stock_move_bps']) for s in samples])
    days = defaultdict(list)
    for row in samples:
        days[row['session']].append(row)
    daily = [dict(session=day,observations=len(rows),
                  bot_bps=sum(s['signed_move_bps'] for s in rows)/len(rows),
                  always_up_bps=sum(s['stock_move_bps'] for s in rows)/len(rows))
             for day,rows in sorted(days.items())]
    def average(field):
        return sum(d[field] for d in daily)/len(daily) if daily else None
    return dict(bot=metrics(samples),always_up=baseline(1),always_down=baseline(-1),
                equal_weight_session_means=dict(sessions_with_signals=len(daily),
                    bot_bps=average('bot_bps'),always_up_bps=average('always_up_bps'),
                    bot_minus_always_up_bps=average('bot_bps')-average('always_up_bps') if daily else None),
                daily=daily)


def report_group(rows):
    selected = nonoverlapping(rows)
    def compact_comparison(samples):
        result = comparison(samples)
        result.pop('daily')
        return result
    return dict(overall=metrics(rows),
                by_direction={d:metrics([s for s in rows if s['direction']==d]) for d in ('CALL','PUT')},
                by_underlying={u:metrics([s for s in rows if s['underlying']==u]) for u in SYMBOLS},
                matched_baselines=compact_comparison(rows),
                nonoverlapping=dict(overall=comparison(selected),
                    by_direction={d:compact_comparison([s for s in selected if s['direction']==d]) for d in ('CALL','PUT')},
                    by_underlying={u:compact_comparison([s for s in selected if s['underlying']==u]) for u in SYMBOLS},
                    by_entry_time={b:compact_comparison([s for s in selected if time_bucket(s)==b])
                                   for b in ('09:30-10:30 ET','10:30-14:00 ET','14:00-16:00 ET')}))


def replay(db,start,end,horizon=15,progress=None):
    days = [r[0] for r in db.execute('SELECT DISTINCT session FROM bars WHERE session>=? AND session<? ORDER BY session',
                                    (start.isoformat(),end.isoformat()))]
    if len(days) < 2:
        return dict(status='Need at least two saved exchange sessions',sessions_available=len(days))
    split = min(len(days)-1,max(1,int(len(days)*.7)))
    samples = {'earlier':[],'held_out':[]}
    for number,day in enumerate(days,1):
        group = 'earlier' if day < days[split] else 'held_out'
        for symbol in SYMBOLS:
            bars = [(datetime.fromisoformat(at),o,c,v) for at,o,c,v in db.execute(
                'SELECT at,open,close,volume FROM bars WHERE session=? AND symbol=? ORDER BY at',(day,symbol))]
            samples[group].extend(dict(s,underlying=symbol,session=day) for s in evaluate_session(symbol,bars,horizon))
        if progress and (number == 1 or number % 25 == 0 or number == len(days)):
            progress(number,len(days),day)
    return dict(status='Stock-signal replay only — no option P&L or fills',holding_minutes=horizon,
        entry='Next minute open after completed signal; exit at a later minute open',
        held_out_start=days[split],sessions_available=len(days),
        limitations=['IEX is one exchange; missing minute windows are excluded.',
                     'Repeated signals overlap; observations are not independent trades.',
                     'Nonoverlapping windows are per underlying; cross-symbol and daily dependence remain.',
                     'Baselines are matched signal windows, not buy-and-hold or executable options trades.',
                     'Missing future windows are excluded too; availability can introduce selection bias.',
                     'No option premiums, spreads, fees, slippage, or portfolio sizing modeled.',
                     'Later sample already viewed: historical validation, not fresh holdout for new changes.',
                     'Time buckets are descriptive comparisons, not recommended trading filters.'],
        results={group:report_group(rows) for group,rows in samples.items()})


def summary_text(result):
    lines = [result['status']]
    if 'results' not in result: return '\n'.join(lines)
    lines.append(f"{result['sessions_available']} sessions; later sample begins {result['held_out_start']}; {result['holding_minutes']}-minute stock windows.")
    def number(value): return 'n/a' if value is None else f'{value:+.3f}'
    for group,data in result['results'].items():
        lines.append(f'\n{group.upper()} — nonoverlapping windows, per underlying')
        lines.append('Segment | windows | bot bps | always up bps | always down bps')
        subsets = [('All',data['nonoverlapping']['overall'])]
        for key in ('by_direction','by_underlying','by_entry_time'):
            subsets.extend(data['nonoverlapping'][key].items())
        for name,stats in subsets:
            lines.append(f"{name} | {stats['bot']['observations']} | " + ' | '.join(
                number(stats[k]['average_signed_stock_move_bps']) for k in ('bot','always_up','always_down')))
        daily = data['nonoverlapping']['overall']['equal_weight_session_means']
        lines.append(f"Equal-weight session mean: bot {number(daily['bot_bps'])} bps; "
                     f"bot minus always up {number(daily['bot_minus_always_up_bps'])} bps over {daily['sessions_with_signals']} sessions.")
    lines.append('\n100 bps = 1% underlying stock move. These are not option profits or account returns.')
    lines.append('Later results were already viewed. Any new strategy requires fresh future validation.')
    return '\n'.join(lines)
