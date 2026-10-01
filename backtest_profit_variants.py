"""Replay fixed exit/re-entry hypotheses against archived historical data."""
import json
from pathlib import Path
from backtest_profit_trial import ROOT, DATA, SYMBOLS, CUTOFF, frame, observations, replay
from profit_strategy_variants import VARIANTS, ReferenceStrategy, fast_features


def replay_variant(symbol, obs, config, cost, delay=1):
    engine=ReferenceStrategy(config,cost,delay)
    peak=1.;dd=0.;hp=1.;hd=0.
    for o in obs:
        value=engine.step(o)
        hold=o['price']/(obs[0]['price']*(1+cost))*(1-cost)
        peak=max(peak,value);dd=min(dd,value/peak-1)
        hp=max(hp,hold);hd=min(hd,hold/hp-1)
    return dict(symbol=symbol,variant=config.name,start=obs[0]['at'].isoformat(),end=obs[-1]['at'].isoformat(),
                observations=len(obs),cost_bps=round(cost*10000),delay_bars=delay,
                trial_pct=round((value-1)*100,3),hold_pct=round((hold-1)*100,3),
                advantage_pct=round((value-hold)*100,3),trial_drawdown_pct=round(dd*100,3),
                hold_drawdown_pct=round(hd*100,3),trial_fills=engine.state['fills'])


def main():
    raw={(s,i):frame(json.loads((DATA/f'{s}_{i}.json').read_text()),i) for s in SYMBOLS for i in ('1d','15m')}
    results=[]
    for symbol in SYMBOLS:
        intra=raw[symbol,'15m'];obs=observations(symbol,intra,raw[symbol,'1d'],raw['SPY','1d'])
        features=dict(zip(intra.index,fast_features(intra)))
        for o in obs:o['fast']=features.get(o['at'])
        days=sorted({o['at'].date() for o in obs});split=days[len(days)*2//3]
        cohorts=[('earlier',[o for o in obs if o['at'].date()<split]),
                 ('later_reused',[o for o in obs if o['at'].date()>=split])]
        cohorts += [('five_session',[o for o in obs if o['at'].date() in set(days[i:i+5])])
                    for i in range(0,len(days)-4,5)]
        for cost in (.001,.0025,.005):
            for label,selected in cohorts:
                # Original tight rule is the matched control, not a new tuned version.
                base=replay(symbol,selected,cost)
                results.append({**base,'variant':'original_tight','delay_bars':1,'sample':label})
                for config in VARIANTS:
                    for delay in (1,2):
                        r=replay_variant(symbol,selected,config,cost,delay)
                        results.append({**r,'sample':label})
    def summarize(sample,bps,delay):
        out=[]
        for name in ['original_tight']+[x.name for x in VARIANTS]:
            group=[r for r in results if r['sample']==sample and r['cost_bps']==bps and r['variant']==name and r['delay_bars']==delay]
            if not group:continue
            avg=lambda key:round(sum(r[key] for r in group)/len(group),3)
            out.append(dict(variant=name,cases=len(group),cost_bps=bps,delay_bars=delay,sample=sample,
                            wins=sum(r['advantage_pct']>0 for r in group),return_pct=avg('trial_pct'),
                            hold_pct=avg('hold_pct'),advantage_pct=avg('advantage_pct'),
                            drawdown_pct=avg('trial_drawdown_pct'),hold_drawdown_pct=avg('hold_drawdown_pct'),
                            fills=sum(r['trial_fills'] for r in group)))
        return out
    summary=[x for sample in ('earlier','later_reused','five_session') for cost in (10,25,50) for delay in (1,2) for x in summarize(sample,cost,delay)]
    report=dict(cutoff=CUTOFF.isoformat(),results=results,summary=summary,
                limitations=['Exit-management trials start invested; this is not a complete Scout portfolio or entry-selection backtest.',
                             'Previously inspected historical periods are reused comparisons, not fresh out-of-sample validation.',
                             'About two months, nine selected symbols; mostly bullish/mixed market. Correlated cases, not independent trades.',
                             'Yahoo consolidated prices, completed daily signals, causal intraday signals; data differ from live IEX.',
                             'Next completed-bar prices are fill proxies, not guaranteed fills. Costs, execution delays are assumptions.',
                             'No dividends, taxes, portfolio cash/slot constraints. Fixed rules, no parameter optimization.'])
    (ROOT/'backtests/profit_variants_results.json').write_text(json.dumps(report,indent=2))
    lines=['# Scout profit strategy variants','',f'Data cutoff: {CUTOFF.isoformat()}. {len(results)} replay cases.','',
           'Each variant changes a specific part of the original rule; thresholds were fixed before this comparison. The later period was already inspected and is not a fresh holdout.','',
           '## Five-session cases, 10 basis points cost per side, next-observation fills','',
           '| Variant | Mean return | Holding | Difference | Mean drawdown | Beats holding |',
           '|---|---:|---:|---:|---:|---:|']
    for x in summarize('five_session',10,1):
        lines.append(f'| {x["variant"]} | {x["return_pct"]:+.2f}% | {x["hold_pct"]:+.2f}% | {x["advantage_pct"]:+.2f} points | {x["drawdown_pct"]:.2f}% | {x["wins"]}/{x["cases"]} |')
    lines+=['','## Later reused segment, same costs and delay','', '| Variant | Mean return | Holding | Difference | Mean drawdown |','|---|---:|---:|---:|---:|']
    for x in summarize('later_reused',10,1):
        lines.append(f'| {x["variant"]} | {x["return_pct"]:+.2f}% | {x["hold_pct"]:+.2f}% | {x["advantage_pct"]:+.2f} points | {x["drawdown_pct"]:.2f}% |')
    lines+=['','## Stress results: five-session cases','', '| Variant | Cost per side | Delay | Mean return | Difference vs holding |','|---|---:|---:|---:|---:|']
    for x in summary:
        if x['sample']=='five_session' and (x['cost_bps'],x['delay_bars']) in [(25,1),(50,1),(25,2)]:
            lines.append(f'| {x["variant"]} | {x["cost_bps"]} bps | {x["delay_bars"]} bars | {x["return_pct"]:+.2f}% | {x["advantage_pct"]:+.2f} points |')
    lines+=['','## Rules','',
            '- Volatility: activation at max(1%, 0.75 times daily ATR%); trail 1.5 times daily ATR% while strong, 0.75 times while weak, with a 0.75% minimum. Floors tighten only upward. Hard loss -7.5%.',
            '- Faster timing: 15-minute EMA9/EMA21, MACD and prior 20-bar volume. Re-entry needs two rising-price qualifying observations at least 15 minutes apart, reclaiming the last sale price. Daily bearish market regime blocks re-entry.',
            '- Partial: sell half once on a weaker volatility warning, retain the remainder until its protective floor or hard loss fires; cash can refill the position only after re-entry confirmation.',
            '- At most two re-entries per session. New sessions reset confirmations and cancel stale pending buys. Signals fill on the next observation, or the second observation in delay stress tests.',
            '- All costs are charged per side and final positions are valued after assumed liquidation costs. Each replay begins from the same unit capital as holding.','', '## Limits','']+['- '+x for x in report['limitations']]
    (ROOT/'backtests/PROFIT_VARIANTS_REPORT.md').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))

if __name__=='__main__':main()
