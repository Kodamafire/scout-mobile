"""Broker-free, 15-minute portfolio replay using Scout's pure decision functions.

The historical universe and daily inputs are approximations. It is NOT a
reconstruction of Scout's historical most-active scanner or actual account.
"""
import ast
import contextlib
import io
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace as NS
from scout_profit_protection import update_profit_floor, update_runner_floor, risk_sized_budget


def load_rules(path=None):
    source=Path(path or Path(__file__).with_name('scout_runner.py')).read_text()
    names={'ScoutConfig','entry_score','prepare_confirmation_cycle','analyze_position',
           'choose_upgrade','confirm_upgrade_persistence'}
    tree=ast.parse(source)
    namespace=dict(dataclass=dataclass,update_profit_floor=update_profit_floor,
                   update_runner_floor=update_runner_floor,risk_sized_budget=risk_sized_budget)
    exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.ClassDef)) and n.name in names],type_ignores=[]),'<scout replay rules>','exec'),namespace)
    namespace['CFG']=namespace['ScoutConfig']()
    return namespace


def entry_allowed(variant,m,score,regime):
    if variant=='no_chasing':
        return (m['close']/m['ema20']-1)*100 <= max(3.,1.5*m['atr_pct'])
    if variant=='stronger_relative_strength':return m['rs20']>=4
    if variant=='stricter_mixed_market':return regime!='CHOP / MIXED' or score>=9
    return True


class PortfolioReplay:
    def __init__(self,variant='baseline',cost=.001,delay=1,capital=100000):
        if cost<0 or cost>=1 or delay<1:raise ValueError('Invalid execution assumptions')
        self.variant,self.cost,self.delay,self.initial=variant,cost,delay,capital
        self.rules=load_rules();self.cfg=self.rules['CFG']
        self.cash=float(capital);self.positions={};self.pending=[];self.prices={}
        self.state={'high_water':{},'warning_streak':{},'exit_policy':'runner_v1','profit_floor':{},'legacy_profit_floor':{}}
        self.events=[];self.curve=[];self.rotation=None;self.session_number=0;self.session=None
        self.filters=0;self.stale_fills=0
    def equity(self):return self.cash+sum(p['qty']*self.prices[s] for s,p in self.positions.items())
    def queue(self,side,symbol,at,notional=None,rotation=None):
        if any(o['symbol']==symbol for o in self.pending):return
        self.pending.append(dict(side=side,symbol=symbol,signal_at=at,notional=notional,
                                 remaining=self.delay,rotation=rotation))
    def fill(self,o,price,at):
        symbol=o['symbol']
        if o['side']=='SELL':
            p=self.positions.pop(symbol,None)
            if not p:return
            received=p['qty']*price*(1-self.cost);self.cash+=received
            self.events.append(dict(at=at.isoformat(),symbol=symbol,side='SELL',price=price,qty=p['qty'],
                                    pnl=received-p['spent'],signal_at=o['signal_at'].isoformat()))
            for key in ('high_water','warning_streak','profit_floor','legacy_profit_floor'):
                self.state[key].pop(symbol,None)
            if o['rotation']:self.rotation={'candidate':o['rotation'],'stage':'NEEDS_BUY'}
        else:
            if symbol in self.positions or len(self.positions)>=self.cfg.max_positions:return
            reserve=self.equity()*self.cfg.minimum_cash_reserve_fraction
            spendable=max(0.,self.cash-reserve)
            budget=min(o['notional'],spendable)
            if budget<self.cfg.minimum_order_dollars:return
            qty=budget/(price*(1+self.cost));self.cash-=budget
            self.positions[symbol]=dict(qty=qty,entry=price,spent=budget,session_entered=self.session_number)
            self.events.append(dict(at=at.isoformat(),symbol=symbol,side='BUY',price=price,qty=qty,
                                    spent=budget,signal_at=o['signal_at'].isoformat()))
    def step(self,at,snapshot):
        if self.curve and at.isoformat()<=self.curve[-1]['at']:return
        if at.date()!=self.session:
            self.session=at.date();self.session_number+=1
            # DAY buys cannot silently fill on a later trading session.
            self.pending=[o for o in self.pending if o['side']=='SELL']
        for s,x in snapshot.items():self.prices[s]=x['price']
        for o in list(self.pending):
            if o['symbol'] not in snapshot:self.stale_fills+=1;continue
            o['remaining']-=1
            if o['remaining']<=0:
                self.pending.remove(o);self.fill(o,snapshot[o['symbol']]['price'],at)
        open_orders={o['symbol']:o for o in self.pending}
        advance=self.rules['prepare_confirmation_cycle'](self.state,True,at)
        managed=[]
        for s,p in self.positions.items():
            price=self.prices[s]
            m=snapshot.get(s,{}).get('metrics')
            row=self.rules['analyze_position'](NS(symbol=s,unrealized_plpc=price/p['entry']-1,
                  qty=p['qty'],market_value=p['qty']*price),m,self.state,advance)
            managed.append(row)
            stalled=(self.variant=='stalled_trade_review' and self.session_number-p['session_entered']>=5
                     and row['pnl_pct']<1 and row['exit_score']>=3)
            if (row['exit_confirmed'] or stalled) and s not in open_orders:
                self.queue('SELL',s,at)
        candidates=[]
        for s,x in snapshot.items():
            if s in self.positions or x.get('metrics') is None:continue
            m=x['metrics']
            if not self.cfg.min_price<=m['close']<=self.cfg.max_price:continue
            score,checks=self.rules['entry_score'](m)
            if score<self.cfg.entry_score_min:continue
            if not entry_allowed(self.variant,m,score,x['regime']):self.filters+=1;continue
            candidates.append(dict(symbol=s,entry_score=score,**m))
        candidates.sort(key=lambda x:(x['entry_score'],x['relative_volume'],x['rs20']),reverse=True)
        candidates=candidates[:self.cfg.candidate_shortlist]
        open_orders={o['symbol']:o for o in self.pending}
        with contextlib.redirect_stdout(io.StringIO()):
            upgrade=self.rules['choose_upgrade'](managed,candidates,set(self.positions),open_orders)
            upgrade=self.rules['confirm_upgrade_persistence'](upgrade,self.state,advance)
        if self.rotation and self.rotation['stage']=='NEEDS_BUY':
            s=self.rotation['candidate']
            if s in snapshot and s not in self.positions and s not in open_orders:
                budget=risk_sized_budget(self.equity(),max(0.,self.cash-self.equity()*.35),
                    self.cfg.position_fraction,self.cfg.risk_fraction_per_trade,self.cfg.hard_stop_pct)
                self.queue('BUY',s,at,budget);self.rotation=None
        elif not self.rotation and upgrade and upgrade['upgrade_confirmed']:
            s=upgrade['replace_symbol'];target=upgrade['candidate']['symbol']
            if s not in open_orders and target not in open_orders:
                self.queue('SELL',s,at,rotation=target);self.rotation={'stage':'WAIT_SELL'}
        if not self.rotation:
            reserved=sum(o['notional'] or 0 for o in self.pending if o['side']=='BUY')
            buys={o['symbol'] for o in self.pending if o['side']=='BUY'}
            slots=self.cfg.max_positions-len(self.positions)-len(buys)
            limit=min(self.cfg.max_new_entries_per_cycle,max(0,slots))
            spendable=max(0.,self.cash-self.equity()*self.cfg.minimum_cash_reserve_fraction-reserved)
            budget=risk_sized_budget(self.equity(),spendable/max(1,limit),
                       self.cfg.position_fraction,self.cfg.risk_fraction_per_trade,self.cfg.hard_stop_pct)
            # Match the shortlist slice, even if an open order consumes a candidate slot.
            for row in candidates[:limit]:
                if row['symbol'] not in {o['symbol'] for o in self.pending} and budget>=50:
                    self.queue('BUY',row['symbol'],at,budget)
        equity=self.equity()
        self.curve.append(dict(at=at.isoformat(),equity=equity,cash=self.cash,positions=len(self.positions)))
        if self.cash < -.000001 or len(self.positions)>self.cfg.max_positions:
            raise AssertionError('Portfolio accounting invariant violated')
    def summary(self):
        peak=self.initial;dd=0.;exposure=[]
        for x in self.curve:
            peak=max(peak,x['equity']);dd=min(dd,x['equity']/peak-1)
            exposure.append(1-x['cash']/x['equity'])
        # End value includes assumed liquidation costs without creating fictitious fills.
        end=self.cash+sum(p['qty']*self.prices[s]*(1-self.cost) for s,p in self.positions.items())
        sells=[e for e in self.events if e['side']=='SELL']
        return dict(variant=self.variant,cost_bps=round(self.cost*10000),delay_bars=self.delay,
                    return_pct=100*(end/self.initial-1),drawdown_pct=100*dd,
                    fills=len(self.events),closed_trades=len(sells),wins=sum(e['pnl']>0 for e in sells),
                    mean_exposure_pct=100*sum(exposure)/max(1,len(exposure)),
                    end_cash=self.cash,end_positions=len(self.positions),filtered_checks=self.filters,
                    missing_quote_checks=self.stale_fills)
