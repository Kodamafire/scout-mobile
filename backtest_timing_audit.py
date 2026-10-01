"""Post-trade hindsight diagnostics, strictly separate from causal decisions.

Reuses archived data. Tests one ablation: omit the sale-price reclaim gate.
"""
import json
from dataclasses import replace
from backtest_profit_trial import ROOT, DATA, SYMBOLS, CUTOFF, frame, observations
from profit_strategy_variants import VARIANTS, ReferenceStrategy, fast_features
from backtest_profit_variants import replay_variant


def future_metrics(obs, index, horizon=20):
    """Future prices are for evaluating an already recorded event, never for signals."""
    future=obs[index+1:index+1+horizon]
    if len(future)<horizon:
        return None  # Incomplete outcome is pending, never counted as a success/failure.
    reference=obs[index]['price']
    return dict(max_gain_pct=100*(max(o['price'] for o in future)/reference-1),
                max_loss_pct=100*(min(o['price'] for o in future)/reference-1),
                final_move_pct=100*(future[-1]['price']/reference-1),
                recovered_above_sale=any(o['price']>=reference for o in future),
                end=future[-1]['at'].isoformat())


def audit_events(symbol, obs, config, cost):
    e=ReferenceStrategy(config,cost);events=[]
    for i,o in enumerate(obs):
        prior=e.state['fills'] if e.state else 0
        e.step(o)
        if e.state['fills']>prior:
            events.append(dict(symbol=symbol,variant=config.name,side=e.state['last_action'],
                               at=o['at'].isoformat(),index=i,price=o['price'],
                               future_5=future_metrics(obs,i,5),future_20=future_metrics(obs,i,20)))
    for event in events:
        if event['side'] not in ('SELL','HALF'):continue
        rebuy=next((x for x in events if x['index']>event['index'] and x['side']=='BUY'),None)
        # Evaluate only the cash interval, bounded at the diagnostic horizon.
        end=min(event['index']+20,rebuy['index'] if rebuy else len(obs)-1)
        cash_interval=obs[event['index']+1:end+1]
        event['rebuy_bars']=rebuy['index']-event['index'] if rebuy else None
        event['best_move_while_out_pct']=100*(max((x['price'] for x in cash_interval),default=event['price'])/event['price']-1)
        event['rebuy_price_pct']=100*(rebuy['price']/event['price']-1) if rebuy else None
    return events


def classify(events):
    exits=[e for e in events if e['side'] in ('SELL','HALF') and e['future_20'] is not None]
    buys=[e for e in events if e['side']=='BUY' and e['future_20'] is not None]
    return dict(exits=len(exits),buys=len(buys),
                rebound_1pct_after_exit=sum(e['future_20']['max_gain_pct']>=1 for e in exits),
                decline_1pct_after_exit=sum(e['future_20']['max_loss_pct']<=-1 for e in exits),
                missed_1pct_while_out=sum(e['best_move_while_out_pct']>=1 for e in exits),
                buyback_fell_1pct=sum(e['future_20']['max_loss_pct']<=-1 for e in buys),
                pending=sum(e['future_20'] is None for e in events))


def main():
    raw={(s,i):frame(json.loads((DATA/f'{s}_{i}.json').read_text()),i) for s in SYMBOLS for i in ('1d','15m')}
    events=[];results=[]
    base=[v for v in VARIANTS if v.fast and not v.partial]
    configs=base+[replace(v,name=v.name+'_no_reclaim',reclaim_sale=False) for v in base]
    for symbol in SYMBOLS:
        intra=raw[symbol,'15m'];obs=observations(symbol,intra,raw[symbol,'1d'],raw['SPY','1d'])
        features=dict(zip(intra.index,fast_features(intra)))
        for o in obs:o['fast']=features.get(o['at'])
        days=sorted({o['at'].date() for o in obs});split=days[len(days)*2//3]
        cohorts=[('earlier',[o for o in obs if o['at'].date()<split]),
                 ('later_reused',[o for o in obs if o['at'].date()>=split])]
        cohorts += [('five_session',[o for o in obs if o['at'].date() in set(days[i:i+5])])
                    for i in range(0,len(days)-4,5)]
        for label,selected in cohorts:
            for config in configs:
                for cost in (.001,.0025):
                    for delay in (1,2):
                        results.append({**replay_variant(symbol,selected,config,cost,delay),'sample':label})
                # Diagnostics only on non-overlapping five-session windows, one cost setting.
                if label=='five_session':
                    events.extend(audit_events(symbol,selected,config,.001))
    summaries=[]
    for label in ('earlier','later_reused','five_session'):
        for config in configs:
            for bps in (10,25):
                for delay in (1,2):
                    group=[r for r in results if r['sample']==label and r['variant']==config.name and r['cost_bps']==bps and r['delay_bars']==delay]
                    avg=lambda key:sum(r[key] for r in group)/len(group)
                    summaries.append(dict(sample=label,variant=config.name,cost_bps=bps,delay_bars=delay,cases=len(group),
                                          return_pct=avg('trial_pct'),hold_pct=avg('hold_pct'),drawdown_pct=avg('trial_drawdown_pct'),
                                          wins=sum(r['advantage_pct']>0 for r in group)))
    diagnostics={c.name:classify([e for e in events if e['variant']==c.name]) for c in configs}
    report=dict(cutoff=CUTOFF.isoformat(),diagnostics=diagnostics,summary=summaries,events=events,results=results,
                limitations=['Post-exit rebounds and subsequent declines can both occur; categories overlap and are diagnostic, not proof an exit was wrong.',
                             '20 bars are five hours of observed trading, potentially crossing sessions; no future prices enter strategy decisions.',
                             'Short windows censor many outcomes; incomplete horizons are excluded and reported as pending.',
                             'Reused, selected history; no new holdout or full historical Scout portfolio/selection simulation.',
                             'Forced initial positions, Yahoo reference bars, assumed per-side costs and next-bar fills, no dividends/taxes.'])
    (ROOT/'backtests/timing_audit_results.json').write_text(json.dumps(report,indent=2))
    lines=['# Scout exit and re-entry timing audit','',f'{len(results)} matched historical replay cases; {len(events)} fill events audited.','',
           '## Five-session comparison at 10 bps per side, next-observation fills','',
           '| Variant | Mean return | Hold | Mean drawdown |','|---|---:|---:|---:|']
    for r in summaries:
        if r['sample']=='five_session' and r['cost_bps']==10 and r['delay_bars']==1:
            lines.append(f'| {r["variant"]} | {r["return_pct"]:+.2f}% | {r["hold_pct"]:+.2f}% | {r["drawdown_pct"]:.2f}% |')
    lines+=['','## Fill diagnostics, five-session windows','',
            '| Variant | Complete exits | Rebound ≥1% after exit | Decline ≥1% after exit | Missed ≥1% while out | Complete buys | Buyback fell ≥1% | Pending outcomes |',
            '|---|---:|---:|---:|---:|---:|---:|---:|']
    for name,d in diagnostics.items():
        lines.append('| '+name+' | '+' | '.join(str(d[k]) for k in ['exits','rebound_1pct_after_exit','decline_1pct_after_exit','missed_1pct_while_out','buys','buyback_fell_1pct','pending'])+' |')
    lines+=['','## Later reused period, same assumptions','', '| Variant | Mean return | Hold |','|---|---:|---:|']
    for r in summaries:
        if r['sample']=='later_reused' and r['cost_bps']==10 and r['delay_bars']==1:
            lines.append(f'| {r["variant"]} | {r["return_pct"]:+.2f}% | {r["hold_pct"]:+.2f}% |')
    lines+=['','## Limits','']+['- '+x for x in report['limitations']]
    (ROOT/'backtests/TIMING_AUDIT_REPORT.md').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines))

if __name__=='__main__':main()
