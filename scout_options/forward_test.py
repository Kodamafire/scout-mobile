"""Frozen future-session stock study. No brokerage orders or option fills."""
import hashlib
import fcntl
import inspect
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from .core import directional_signal, finite
from .history import HistoryStore, SYMBOLS, collect
from .replay import evaluate_session_horizons, report_group, nonoverlapping, time_bucket, comparison, metrics

EASTERN = ZoneInfo('America/New_York')


def rules():
    evaluated=(directional_signal,finite,evaluate_session_horizons,nonoverlapping,time_bucket,
               comparison,metrics,report_group,HistoryStore.save_session,collect)
    return dict(version=1,evaluator_sha256=hashlib.sha256(
        '\n'.join(inspect.getsource(f) for f in evaluated).encode()).hexdigest(),
        universe=list(SYMBOLS),feed='iex',adjustment='raw',
        horizons_minutes=[5,15],selection_spacing_minutes=15,
        candidate='Opening-hour signals, five-minute fixed exit',
        opening_entry_minutes_et=[570,630],
        comparisons=['Opening 5 vs opening 15 on identical entries',
                     'Opening 5 vs matched always-up and always-down',
                     'All-day 15 vs matched always-up and always-down'],
        evaluation='Equal-weight session means; daily details retained',
        mode='Finished-session stock research; no brokerage orders or option fills')


def load_or_freeze(directory,now):
    """A restart retains the original date and refuses silent signal changes."""
    if now.tzinfo is None:
        raise ValueError('Timezone-aware freeze time required')
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    path=directory/'forward-plan.json'
    definition=rules()
    if not path.exists():
        plan=dict(rules=definition,frozen_at=now.isoformat(),
                  start_session=(now.astimezone(EASTERN).date()+timedelta(days=1)).isoformat())
        # Single worker owns the research ledger; exclusive creation prevents replacement.
        with path.open('x') as output:
            json.dump(plan,output,indent=2);output.flush();os.fsync(output.fileno())
    plan=json.loads(path.read_text())
    if plan['rules'] != definition:
        raise ValueError('Frozen study rules changed; refuse to mix versions')
    frozen=datetime.fromisoformat(plan['frozen_at'])
    start=datetime.fromisoformat(plan['start_session']).date()
    if frozen.tzinfo is None or start <= frozen.astimezone(EASTERN).date():
        raise ValueError('Study must begin after its freeze date')
    return plan


def summarize(db,plan,end):
    start=plan['start_session']
    days=[r[0] for r in db.execute(
        'SELECT DISTINCT session FROM bars WHERE session>=? AND session<? ORDER BY session',
        (start,end.isoformat()))]
    samples={5:[],15:[]}
    for day in days:
        for symbol in SYMBOLS:
            bars=[(datetime.fromisoformat(at),o,c,v) for at,o,c,v in db.execute(
                'SELECT at,open,close,volume FROM bars WHERE session=? AND symbol=? ORDER BY at',
                (day,symbol))]
            for horizon,rows in evaluate_session_horizons(symbol,bars,(5,15)).items():
                samples[horizon].extend(dict(row,underlying=symbol,session=day) for row in rows)
    reports={str(h):report_group(rows) for h,rows in samples.items()}
    candidate=reports['5']['nonoverlapping']['by_entry_time']['09:30-10:30 ET']
    reference=reports['15']['nonoverlapping']['by_entry_time']['09:30-10:30 ET']
    return dict(plan=plan,sessions_available=len(days),last_session=days[-1] if days else None,
        candidate=candidate,opening_15_reference=reference,
        all_day_15_reference=reports['15']['nonoverlapping']['overall'],
        horizons=reports,
        limitations=['Stock bars only; no option premiums, Greeks, fills, fees or account returns.',
                     'Gapped windows excluded; eligibility includes future data availability.',
                     'Spacing stays 15 minutes; shorter exits do not create extra entries.',
                     'Different symbols share exposure; session means are not independent trades.',
                     'This is future-session replay after close, not a live execution test.',
                     'A favorable sample does not automatically enable brokerage orders.'])


def cycle(directory,stocks,trading,now):
    """Called in a background thread; only completed future sessions are collected."""
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    with (directory/'forward-study.lock').open('a') as writer:
        fcntl.flock(writer,fcntl.LOCK_EX | fcntl.LOCK_NB)
        return _cycle(directory,stocks,trading,now)


def _cycle(directory,stocks,trading,now):
    from datetime import date
    directory=Path(directory)
    plan=load_or_freeze(directory,now)
    start=date.fromisoformat(plan['start_session'])
    today=now.astimezone(EASTERN).date()
    end=today+timedelta(days=1)
    store=HistoryStore(directory/'forward-history.sqlite')
    try:
        identity=json.dumps(plan,sort_keys=True)
        with store.db:
            store.db.execute('CREATE TABLE IF NOT EXISTS study_identity (id INTEGER PRIMARY KEY CHECK(id=1), plan TEXT)')
            saved=store.db.execute('SELECT plan FROM study_identity WHERE id=1').fetchone()
            if saved and saved[0] != identity:
                raise ValueError('Frozen study identity differs from saved dataset')
            if not saved:
                store.db.execute('INSERT INTO study_identity VALUES (1,?)',(identity,))
        downloaded=collect(store,stocks,trading,start,end,now) if start <= today else 0
        path=directory/'forward-results.json'
        if downloaded or not path.exists():
            result=summarize(store.db,plan,end)
            result['coverage']=store.summary(start,max(start,end))
            temp=path.with_suffix('.tmp')
            temp.write_text(json.dumps(result,indent=2,allow_nan=False))
            temp.replace(path)
        else:
            result=json.loads(path.read_text())
            if result['plan'] != plan:
                raise ValueError('Saved result belongs to a different study')
        stats=result['candidate']['equal_weight_session_means']
        return dict(status='Waiting for completed future sessions' if not result['sessions_available'] else 'Future-session results available',
                    checked_at=now.isoformat(),start_session=plan['start_session'],
                    frozen_at=plan['frozen_at'],sessions_available=result['sessions_available'],
                    last_session=result['last_session'],candidate_windows=result['candidate']['bot']['observations'],
                    candidate_bot_bps=stats['bot_bps'],candidate_always_up_bps=stats['always_up_bps'],
                    opening_15_bps=result['opening_15_reference']['equal_weight_session_means']['bot_bps'],
                    mode=plan['rules']['mode'])
    finally:
        store.close()
