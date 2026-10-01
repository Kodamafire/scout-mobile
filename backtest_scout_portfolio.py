"""Matched portfolio comparison on archived reference data; no broker access."""
import json
from backtest_profit_trial import ROOT, DATA, SYMBOLS, CUTOFF, frame, observations
from scout_portfolio_replay import PortfolioReplay

VARIANTS=['baseline','no_chasing','stronger_relative_strength','stricter_mixed_market','stalled_trade_review']

def main():
    raw={(s,i):frame(json.loads((DATA/f'{s}_{i}.json').read_text()),i) for s in SYMBOLS for i in ('1d','15m')}
    timeline={}
    for symbol in SYMBOLS:
        for o in observations(symbol,raw[symbol,'15m'],raw[symbol,'1d'],raw['SPY','1d']):
            timeline.setdefault(o['at'],{})[symbol]=dict(price=o['price'],metrics=o['metrics'],regime=o['regime'])
    times=sorted(timeline);days=sorted({t.date() for t in times});split=days[len(days)*2//3]
    samples={'whole_reused':times,'earlier_reused':[t for t in times if t.date()<split],
             'later_reused':[t for t in times if t.date()>=split]}
    results=[]
    for label,ticks in samples.items():
        for cost in (.001,.0025):
            for delay in (1,2):
                for variant in VARIANTS:
                    engine=PortfolioReplay(variant,cost,delay)
                    for at in ticks:engine.step(at,timeline[at])
                    results.append({**engine.summary(),'sample':label,'start':ticks[0].isoformat(),
                                    'end':ticks[-1].isoformat(),'events':engine.events})
    report=dict(cutoff=CUTOFF.isoformat(),universe=SYMBOLS,results=results,
                limitations=['Current nine-symbol fixed universe replaces historical most-active selection; survivorship and selection bias remain.',
                             'Completed daily indicators plus 15-minute reference prices replace live forming-daily IEX inputs.',
                             'Pure Scout entry/exit/confirmation/upgrade functions and config are reused; broker execution is a simulation adapter.',
                             'Next observed prices with assumed costs; replacement sells fill before a later buy. No real fills, spreads, dividends or taxes.',
                             'Initial account is cash. Position cap 4, max 2 new entries per check, 35% cash reserve, 10% allocation cap and 0.75% planned risk.',
                             'Periods previously inspected; no untouched validation or exact historical Scout-account reconstruction.'])
    (ROOT/'backtests/scout_portfolio_results.json').write_text(json.dumps(report,indent=2))
    lines=['# Scout matched portfolio comparison','',f'{len(results)} portfolio replays. Universe: '+', '.join(SYMBOLS)+'.','',
           'Entry, exit, cash, size, confirmation and rotation are included. This is a fixed-universe reference replay, not exact historical Scout.','']
    for label in samples:
        lines += ['## '+label+' — 10 bps per side, next-observation fills','',
                  '| Version | Return | Max drawdown | Fills | Mean invested |','|---|---:|---:|---:|---:|']
        for r in results:
            if r['sample']==label and r['cost_bps']==10 and r['delay_bars']==1:
                lines.append(f'| {r["variant"]} | {r["return_pct"]:+.2f}% | {r["drawdown_pct"]:.2f}% | {r["fills"]} | {r["mean_exposure_pct"]:.1f}% |')
    lines += ['','## One change at a time','',
              '- No chasing: reject entries above EMA20 by more than max(3%, 1.5 times daily ATR%).',
              '- Stronger relative strength: require 20-session outperformance of SPY of at least 4 percentage points instead of the existing score check at 2.',
              '- Stricter mixed market: require entry score 9/9 when Shadow classifies the broad market as CHOP / MIXED.',
              '- Stalled review: exit after five observed trading sessions if return is under +1% and exit score is at least 3.',
              '- All other controls remain the same. Models reset to cash at each sample start. Higher costs and two-bar delays are included in the JSON.','',
              '## Limits','']+['- '+x for x in report['limitations']]
    (ROOT/'backtests/SCOUT_PORTFOLIO_REPORT.md').write_text('\n'.join(lines)+'\n');print('\n'.join(lines))

if __name__=='__main__':main()
