"""Offline, read-only option exit comparison. Never places or changes orders."""
import argparse, hashlib, json, math, sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TIERS = ((.20,.15),(.50,.10),(1.,.025),(2.,.02),(9.,.01))
PLAN = dict(activation=.20,initial_stop=.25,baseline_trail=.15,
            progressive_tiers=TIERS,fee_per_contract_per_side=.65,
            slippage_per_share=.01,max_quote_age_seconds=3)

def stamp(value):
    d=datetime.fromisoformat(value.replace('Z','+00:00'))
    if d.tzinfo is None:raise ValueError('Timezone required')
    return d

def valid(q):
    try:
        b,a,bs,asz=(q[k] for k in ('bid','ask','bid_size','ask_size'))
        return (all(isinstance(x,(float,int)) and not isinstance(x,bool) and math.isfinite(x) for x in (b,a,bs,asz))
                and 0<b<=a and bs>=1 and asz>=1 and bs==int(bs) and asz==int(asz)
                and q['feed']=='opra' and 0<=(stamp(q['received_at'])-stamp(q['market_at'])).total_seconds()<=3)
    except (ValueError,KeyError,TypeError):return False

def floor_for(entry,peak,previous,progressive):
    floor=entry*(1-PLAN['initial_stop'])
    gain=peak/entry-1
    if gain+1e-10>=PLAN['activation']:
        fraction=PLAN['baseline_trail']
        if progressive:
            for threshold,width in TIERS:
                if gain+1e-10>=threshold:fraction=width
        floor=max(floor,peak*(1-fraction))
    return max(floor,previous) if previous is not None else floor

def replay(trade,progressive=False,cost_multiplier=1):
    entry=float(trade['entry_price']); qty=trade['qty']
    if not math.isfinite(entry) or entry<=0 or type(qty)!=int or qty<1:raise ValueError('Invalid entry')
    entered=stamp(trade['entry_at']);cutoff=stamp(trade['cutoff_at'])
    if cutoff<=entered:raise ValueError('Invalid cutoff')
    peak=entry;floor=None;prior=None;pending=None;remaining=qty;proceeds=0.;fills=[];discarded=0;gaps=0
    for q in sorted(trade['quotes'],key=lambda x:stamp(x['received_at'])):
        market=stamp(q['market_at']);received=stamp(q['received_at'])
        if received<=entered or market<=entered:continue
        if not valid(q) or prior is not None and market<=prior:discarded+=1;continue
        if prior is not None and (market-prior).total_seconds()>3:gaps+=1
        prior=market
        # A trigger is not a fill. Use only subsequent market and receipt timestamps.
        if pending is not None and market>pending['market_at'] and received>pending['received_at']:
            filled=min(remaining,int(q['bid_size']));price=max(0,q['bid']-PLAN['slippage_per_share']*cost_multiplier)
            proceeds+=filled*price*100;remaining-=filled
            fills.append(dict(at=q['received_at'],price=price,qty=filled))
            if remaining==0:break
        elif pending is None:
            peak=max(peak,q['bid']);floor=floor_for(entry,peak,floor,progressive)
            common=trade.get('common_exit_at') and received>=stamp(trade['common_exit_at'])
            if common or received>=cutoff or q['bid']<=floor:
                reason='shared non-trail exit' if common else 'session cutoff' if received>=cutoff else 'stop / trail'
                pending=dict(market_at=market,received_at=received,floor=floor,reason=reason)
    completed=remaining==0
    # Same recorded entry for both arms. Entry fee/slippage is added conservatively.
    costs=2*qty*PLAN['fee_per_contract_per_side']*cost_multiplier+qty*100*PLAN['slippage_per_share']*cost_multiplier
    net=proceeds-entry*qty*100-costs if completed else None
    return dict(status='complete modeled exit' if completed else 'incomplete / censored',net_pnl=round(net,4) if net is not None else None,
        peak_return_pct=round((peak/entry-1)*100,4),floor=floor,trigger_at=pending['received_at'].isoformat() if pending else None,
        exit_reason=pending['reason'] if pending else None,fills=fills,unfilled_contracts=remaining,
        discarded_quotes=discarded,quote_gaps_over_3s=gaps,
        giveback_dollars=round((peak*qty*100-proceeds),4) if completed else None)

def compare(dataset):
    rows=[]
    for t in ([] if dataset.get('blocked') else dataset['trades']):
        b=replay(t);p=replay(t,True);b2=replay(t,False,2);p2=replay(t,True,2)
        rows.append(dict(id=t['id'],symbol=t['symbol'],baseline=b,progressive=p,
                         doubled_costs=dict(baseline=b2,progressive=p2)))
    paired=[r for r in rows if r['baseline']['net_pnl'] is not None and r['progressive']['net_pnl'] is not None]
    return dict(experiment='Frozen progressive trail v1',provenance=dataset['provenance'],plan=PLAN,
        status='Descriptive matched exit replay; not a portfolio backtest or actual fills',
        collected_trades=len(dataset['trades']),scored_trades=len(rows),blocked=dataset.get('blocked',False),completed_pairs=len(paired),incomplete_pairs=len(dataset['trades'])-len(paired),
        totals=dict(baseline=sum(r['baseline']['net_pnl'] for r in paired),progressive=sum(r['progressive']['net_pnl'] for r in paired),
                    progressive_minus_baseline=sum(r['progressive']['net_pnl']-r['baseline']['net_pnl'] for r in paired)),
        rows=rows,collection_notes=dataset.get('notes',[]),limitations=[
            'Costs are research assumptions, not verified broker charges. Doubled costs are also reported.',
            'Next valid quote after trigger models a marketable exit; displayed sizes are not guaranteed liquidity.',
            'Partial fills use later quotes; same quote cannot trigger and fill an exit.',
            'Entry set is conditioned on recorded Scout trades, not independent new entry selection.',
            'Other exits are mirrored from recorded intents, not recomputed counterfactual account state.',
            'No reentry or sizing feedback; session-risk effects may differ between arms.',
            'Sparse quotes, missing tape after the original exit and dropped events can bias results.',
            'Synthetic scenarios validate mechanics only; neither they nor a small sample establish an edge.'])

def read_db(path):
    # SQLite backup via a read-only connection captures a consistent view including WAL.
    source=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=3)
    dest=sqlite3.connect(':memory:');source.backup(dest);source.close();return dest

def collect(home):
    folder=home/'.scout-options';trades=[];notes=[];tapes=[];ledgers=[];blocked=False
    status_path=folder/'options-status.json'
    if status_path.exists():
        status=json.loads(status_path.read_text());settings=status.get('research_settings',{})
        if status.get('trading_rules'):
            blocked=True;notes.append('Enhanced trading rules detected: scoring withheld until the installed engine baseline is verified. Inputs are exported for an adapter; no claim about current-engine performance.')
        for key,value in [('trail_activation',.20),('trail_fraction',.15),('stop_fraction',.25)]:
            if key in settings and abs(settings[key]-value)>1e-9:
                raise ValueError('Saved '+key+' differs from frozen baseline. Do not compare mismatched rules.')
    else:
        blocked=True;notes.append('Current status file not found: baseline rules unverified; scoring withheld.')
    for path in sorted(folder.iterdir()) if folder.exists() else []:
        if path.suffix not in ('.sqlite','.sqlite3','.db'):continue
        db=None
        try:
            db=read_db(path);columns={r[1] for r in db.execute('PRAGMA table_info(events)')}
            if {'market_at','received_at','bid','ask','bid_size','ask_size','feed','symbol'}<=columns:
                rows=db.execute("SELECT symbol,market_at,received_at,bid,ask,bid_size,ask_size,feed FROM events WHERE kind='quote' ORDER BY received_at,id").fetchall()
                tapes.extend(dict(zip(('symbol','market_at','received_at','bid','ask','bid_size','ask_size','feed'),r)) for r in rows)
                if 'runs' in {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
                    drops=db.execute('SELECT SUM(dropped) FROM runs').fetchone()[0]
                    if drops:notes.append(path.name+': '+str(drops)+' dropped recorder events')
            elif {'at','data'}<=columns:
                row=db.execute('SELECT data FROM ledger WHERE id=1').fetchone()
                state=json.loads(row[0]) if row else {}
                events=[(stamp(at),json.loads(data)) for at,data in db.execute('SELECT at,data FROM events ORDER BY at,id')]
                ledgers.append((state,events))
        except (sqlite3.Error,ValueError,KeyError) as exc:notes.append(path.name+': unsupported or unreadable schema ('+type(exc).__name__+')')
        finally:
            if db:db.close()
    by_symbol={}
    for q in tapes:by_symbol.setdefault(q['symbol'],[]).append(q)
    for state,events in ledgers:
        orders=state.get('orders',{})
        for oid,o in orders.items():
            if o.get('side')!='BUY':continue
            fills=[(at,e) for at,e in events if e.get('kind')=='SIMULATED FILL' and e.get('order_id')==oid]
            if len(fills)!=1 or fills[0][1].get('qty')!=o.get('qty'):
                if fills:notes.append(oid+': partial or multiple entry fills excluded')
                continue
            at,e=fills[0];symbol=o['symbol'];quotes=by_symbol.get(symbol,[])
            if not quotes:notes.append(oid+': no option quote tape');continue
            # First common risk/setup exit associated with this position; trail exits differ by arm.
            next_buys=[stamp(x['created']) for k,x in orders.items() if k!=oid and x.get('side')=='BUY' and x.get('symbol')==symbol and stamp(x['created'])>at]
            stop_before=min(next_buys) if next_buys else None
            common=[stamp(x['created']) for x in orders.values() if x.get('side')=='SELL' and x.get('symbol')==symbol and stamp(x['created'])>=at
                    and (stop_before is None or stamp(x['created'])<stop_before) and x.get('reason') not in ('RUNNER TRAIL','REPRICE UNFILLED EXIT','PREMIUM LOSS')]
            # Freeze original core's 30-minute pre-close cutoff. Early closes need actual SESSION CUTOFF intent.
            local=at.astimezone(ZoneInfo('America/New_York'));cutoff=local.replace(hour=15,minute=30,second=0,microsecond=0)
            end=min(cutoff,stop_before) if stop_before else cutoff
            relevant=[q for q in quotes if at<stamp(q['received_at'])<=end+timedelta(minutes=2) and (stop_before is None or stamp(q['received_at'])<stop_before)]
            trades.append(dict(id=oid,symbol=symbol,entry_at=at.isoformat(),entry_price=e['price'],qty=e['qty'],cutoff_at=cutoff.isoformat(),
                               common_exit_at=min(common).isoformat() if common else None,quotes=relevant))
    return dict(provenance='Recorded local Scout simulated entries and observed quotes; OPRA only is eligible',trades=trades,notes=notes,blocked=blocked)

def synthetic():
    # Diverse fabricated paths; fixed before inspecting real results.
    paths={'runner_then_pullback':[.98,1.20,1.50,1.80,2.,1.94,1.90,1.70,1.68],
           'tight_stop_then_rebound':[.98,1.20,1.50,1.34,1.33,2.,1.80,1.70,1.68],
           'straight_loss':[.98,.90,.74,.73],
           'tenfold_runner':[.98,1.20,1.50,2.,3.,6.,10.,9.89,9.88,8.50,8.49],
           'gap_down':[.98,1.20,2.,1.20,1.15],
           'no_exit_yet':[.98,1.20,1.50,2.]}
    start=stamp('2026-10-07T14:00:00+00:00');trades=[]
    for name,bids in paths.items():
        quotes=[]
        for i,b in enumerate(bids):
            at=start+timedelta(seconds=i+1);quotes.append(dict(market_at=at.isoformat(),received_at=at.isoformat(),bid=b,ask=b+.02,bid_size=1,ask_size=1,feed='opra'))
        trades.append(dict(id=name,symbol='SYNTHETIC',entry_at=start.isoformat(),entry_price=1.,qty=1,cutoff_at=(start+timedelta(hours=1)).isoformat(),quotes=quotes))
    return dict(provenance='SYNTHETIC MECHANICS CHECK ONLY — invented prices, no market-performance evidence',trades=trades)

def main():
    ap=argparse.ArgumentParser(description=__doc__);ap.add_argument('--home',type=Path,default=Path.home()/'scout-options');ap.add_argument('--synthetic',action='store_true');ap.add_argument('--out',type=Path,default=Path('progressive-test-results'));args=ap.parse_args()
    data=synthetic() if args.synthetic else collect(args.home);result=compare(data)
    args.out.mkdir(parents=True,exist_ok=True)
    (args.out/'inputs.json').write_text(json.dumps(data,indent=2,allow_nan=False));result['input_sha256']=hashlib.sha256((args.out/'inputs.json').read_bytes()).hexdigest()
    (args.out/'results.json').write_text(json.dumps(result,indent=2,allow_nan=False))
    lines=[result['provenance'],result['status'],f"Completed pairs: {result['completed_pairs']}; incomplete: {result['incomplete_pairs']}",
           'Sample | Fixed trail net | Progressive net | Difference']
    for r in result['rows']:
        b=r['baseline']['net_pnl'];p=r['progressive']['net_pnl']
        lines.append(f"{r['id']} | {b} | {p} | {round(p-b,2) if b is not None and p is not None else 'incomplete'}")
    lines+=['Totals (complete pairs only): '+str(result['totals']),*result['collection_notes'],*result['limitations']]
    if not result['collected_trades']:lines.append('NO ELIGIBLE RECORDED TRADES. No performance conclusion can be drawn.')
    (args.out/'REPORT.txt').write_text('\n'.join(lines)+'\n');print('\n'.join(lines));print('Report saved in '+str(args.out.resolve()))

if __name__=='__main__':main()
