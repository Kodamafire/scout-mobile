"""Historical exit/re-entry experiment, independent of broker and account access.
Run: python backtest_profit_trial.py
Yahoo reference prices; completed daily indicators, next 15-minute close fills.
"""
import ast
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import pandas as pd
import numpy as np
from shadow_profit_trial import update_trial
from scout_shadow_v48 import directional_decision, market_regime
from scout_profit_protection import update_runner_floor

ROOT = Path(__file__).parent
DATA = ROOT / 'backtests/data'
PACIFIC = ZoneInfo('America/Los_Angeles')
SYMBOLS = ['INTC','PLTR','WBD','AAPL','NVDA','SMCI','SMR','SPY','QQQ']
# Frozen experiment cutoff: no later data can leak into this run.
CUTOFF = datetime(2026,10,1,19,20,tzinfo=timezone.utc)

def download(symbol, interval):
    path = DATA / f'{symbol}_{interval}.json'
    if path.exists(): return json.loads(path.read_text())
    start = CUTOFF - timedelta(days=59 if interval=='15m' else 800)
    url = (f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval={interval}'
           f'&period1={int(start.timestamp())}&period2={int(CUTOFF.timestamp())}')
    req = urllib.request.Request(url, headers={'User-Agent':'Mozilla/5.0'})
    raw = urllib.request.urlopen(req,timeout=35).read().decode()
    obj = json.loads(raw)
    if obj['chart'].get('error'): raise ValueError(obj['chart']['error'])
    path.write_text(raw)
    return obj

def frame(raw, interval):
    r = raw['chart']['result'][0]
    df = pd.DataFrame(r['indicators']['quote'][0],index=pd.to_datetime(r['timestamp'],unit='s',utc=True))
    df = df.dropna(subset=['close','high','low','volume'])
    # Yahoo timestamps are bar START times; only a finished bar can be observed.
    if interval=='15m':
        df.index += pd.Timedelta(minutes=15)
        df=df[df.index<=CUTOFF]
    return df

source = ast.parse((ROOT/'scout_runner.py').read_text())
func = next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='indicators')
namespace={'pd':pd,'np':np}
exec(compile(ast.Module(body=[func],type_ignores=[]),'historical indicators','exec'),namespace)
indicators = namespace['indicators']

def exit_score(m):
    return (2*(m['close']<m['ema20'])+2*(m['ema20']<m['ema50'])+
            2*(m['close']<m['sma200'])+2*(m['macd']<m['macd_signal'])+
            (not m['macd_rising'])+(m['rsi14']<45)+(m['rsi14']<40))

def observations(symbol, intra, daily, spy):
    cache={};out=[]
    for at, bar in intra.iterrows():
        day=at.tz_convert(PACIFIC).date()
        if day not in cache:
            # Never read today's final daily OHLCV while replaying an intraday bar.
            past=daily[[x.tz_convert(PACIFIC).date()<day for x in daily.index]]
            market=spy[[x.tz_convert(PACIFIC).date()<day for x in spy.index]]
            m=indicators(past,market);sp=indicators(market)
            cache[day]=(m,market_regime(sp))
        m,regime=cache[day]
        if m is None:continue
        sig=directional_decision(m,regime,'LONG')
        out.append(dict(at=at.to_pydatetime().astimezone(PACIFIC),price=float(bar['close']),
                        signal={**sig,'symbol':symbol,'price':float(bar['close'])},
                        score=exit_score(m),atr=m['atr_pct'],regime=regime['name'],metrics=m))
    return out

def replay(symbol, obs, cost):
    state={};curve=[];peak=1.;drawdown=0.;hold_peak=1.;hold_dd=0.
    runner_units=1/(obs[0]['price']*(1+cost));runner_cash=0.;runner_entry=obs[0]['price']
    runner_peak_gain=0.;floor=None;pending=False;streak=0;runner_fills=0;session=None
    for o in obs:
        at=o['at'];price=o['price']
        shadow={'status':'OK','evidence':[o['signal']], 'held':[]}
        update_trial(state,shadow,[{'symbol':symbol,'exit_score':o['score']}],at,True,cost)
        r=state['profit_trial']['rows'][symbol]
        v=r['cash']+r['units']*price*(1-cost)
        hold=price/(obs[0]['price']*(1+cost))*(1-cost)
        peak=max(peak,v);drawdown=min(drawdown,v/peak-1)
        hold_peak=max(hold_peak,hold);hold_dd=min(hold_dd,hold/hold_peak-1)
        if pending:
            runner_cash=runner_units*price*(1-cost);runner_units=0.;pending=False;runner_fills+=1
        elif runner_units:
            gain=(price/runner_entry-1)*100;runner_peak_gain=max(runner_peak_gain,gain)
            floor,breach=update_runner_floor(runner_peak_gain,gain,o['atr'],floor)
            if session!=at.date():streak=0;session=at.date()
            streak=streak+1 if o['score']>=10 else 0
            pending=gain<=-7.5 or breach or streak>=2
        curve.append(v)
    return {'symbol':symbol,'start':obs[0]['at'].isoformat(),'end':obs[-1]['at'].isoformat(),
            'observations':len(obs),'cost_bps':round(cost*10000),'hold_pct':round((hold-1)*100,3),
            'trial_pct':round((v-1)*100,3),'advantage_pct':round((v-hold)*100,3),
            'runner_exit_only_pct':round((runner_cash+runner_units*price*(1-cost)-1)*100,3),
            'trial_drawdown_pct':round(drawdown*100,3),'hold_drawdown_pct':round(hold_dd*100,3),
            'trial_fills':r['fills'],'runner_fills':runner_fills,
            'regimes':sorted(set(o['regime'] for o in obs))}

def main():
    DATA.mkdir(parents=True,exist_ok=True)
    jobs=[(s,i) for s in SYMBOLS for i in ('15m','1d')]
    raw={};errors=[]
    def get(job):return job,download(*job)
    with ThreadPoolExecutor(max_workers=6) as pool:
        for job,result in pool.map(get,jobs):raw[job]=frame(result,job[1])
    results=[]
    for symbol in SYMBOLS:
        obs=observations(symbol,raw[symbol,'15m'],raw[symbol,'1d'],raw['SPY','1d'])
        days=sorted({o['at'].date() for o in obs})
        split=days[len(days)*2//3]
        for cost in (.001,.0025):
            for label,predicate in [('earlier',lambda d:d<split),('later_holdout',lambda d:d>=split)]:
                selected=[o for o in obs if predicate(o['at'].date())]
                if selected:results.append({**replay(symbol,selected,cost),'sample':label})
            # Disjoint five-session windows avoid multiplying overlapping results.
            for i in range(0,len(days)-4,5):
                chosen=set(days[i:i+5]);selected=[o for o in obs if o['at'].date() in chosen]
                results.append({**replay(symbol,selected,cost),'sample':'five_session'})
    report={'cutoff':CUTOFF.isoformat(),'method':'Frozen parameters, completed daily signals, next completed 15-minute close fills',
            'limitations':['Exit-management experiment with forced initial entries, not a complete Scout selection backtest.',
                           'Yahoo consolidated prices/volume differ from Scout IEX; completed daily signals lag live forming bars.',
                           'No broker fills, quote spreads, portfolio constraints, dividends or taxes; fixed execution costs assumed.',
                           'Runner comparison exits to cash without new entries; holding is the matched continuous baseline.',
                           'Later segment is a reserved chronological check, not untouched research: symbols were selected from current interests.',
                           'Nine symbols and roughly two months do not establish durable profitability.'], 'results':results}
    (ROOT/'backtests/profit_trial_results.json').write_text(json.dumps(report,indent=2))
    lines=['# Scout profit-protection historical experiment','',f'Cutoff: {CUTOFF.isoformat()}','',report['method'], '',
           '| Sample | Cost per side | Cases | Trial beats hold | Mean trial | Mean hold | Mean advantage |',
           '|---|---:|---:|---:|---:|---:|---:|']
    for sample in ('earlier','later_holdout','five_session'):
        for bps in (10,25):
            group=[r for r in results if r['sample']==sample and r['cost_bps']==bps]
            avg=lambda key:sum(r[key] for r in group)/len(group)
            lines.append(f'| {sample} | {bps} bps | {len(group)} | {sum(r["advantage_pct"]>0 for r in group)} | {avg("trial_pct"):+.2f}% | {avg("hold_pct"):+.2f}% | {avg("advantage_pct"):+.2f} points |')
    lines+=['','## Limits','']+['- '+x for x in report['limitations']]
    (ROOT/'backtests/PROFIT_TRIAL_REPORT.md').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))

if __name__=='__main__':main()
