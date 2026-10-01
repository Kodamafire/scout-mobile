import copy
from datetime import datetime,timedelta
import unittest
from scout_portfolio_replay import PortfolioReplay,entry_allowed,load_rules


def metrics(close=100):
    return dict(close=close,ema20=99,ema50=95,sma200=90,macd=2,macd_signal=1,
                macd_rising=True,rsi14=60,atr_pct=1,relative_volume=1.3,avg_dollar_volume=1e8,
                rs20=5,return20=6)

class PortfolioReplayTests(unittest.TestCase):
    def setUp(self):self.now=datetime.fromisoformat('2026-10-01T09:00:00-07:00')
    def snap(self,symbols=('ABC',),price=100,m=None):
        return {s:dict(price=price,metrics=m or metrics(),regime='BULLISH TREND') for s in symbols}
    def tick(self,e,snapshot,minutes=15):
        self.now+=timedelta(minutes=minutes);e.step(self.now,snapshot)
    def test_signal_never_fills_at_same_observation(self):
        e=PortfolioReplay();self.tick(e,self.snap());self.assertEqual(len(e.events),0)
        self.tick(e,self.snap(price=105));self.assertEqual(e.events[0]['price'],105)
    def test_position_and_new_entry_limits_include_pending_buys(self):
        e=PortfolioReplay();snap=self.snap(('A','B','C','D','E','F'))
        self.tick(e,snap);self.assertEqual(len(e.pending),2)
        self.tick(e,snap);self.assertEqual(len(e.positions),2)
        self.tick(e,snap);self.assertEqual(len(e.positions),4)
        for _ in range(10):self.tick(e,snap)
        self.assertEqual(len(e.positions),4)
        self.assertEqual(len([x for x in e.events if x['side']=='BUY']),4)
    def test_risk_budget_and_cash_reserve(self):
        e=PortfolioReplay();self.tick(e,self.snap(('A','B','C','D')))
        self.assertTrue(all(o['notional']<=10000 for o in e.pending))
        for _ in range(3):self.tick(e,self.snap(('A','B','C','D')))
        self.assertGreaterEqual(e.cash,e.equity()*.35)
    def test_buy_cost_and_quantity_accounting(self):
        e=PortfolioReplay(cost=.001);self.tick(e,self.snap());planned=e.pending[0]['notional']
        self.tick(e,self.snap(price=101))
        self.assertAlmostEqual(e.cash,100000-planned)
        self.assertAlmostEqual(e.positions['ABC']['qty'],planned/(101*1.001))
    def test_actual_source_hard_exit_rule_is_reused(self):
        e=PortfolioReplay();self.tick(e,self.snap());self.tick(e,self.snap())
        self.tick(e,self.snap(price=90));self.assertTrue(any(o['side']=='SELL' for o in e.pending))
        self.tick(e,self.snap(price=80));sales=[x for x in e.events if x['side']=='SELL']
        self.assertEqual(sales[0]['price'],80);self.assertLess(sales[0]['pnl'],0)
    def test_no_chasing_is_one_entry_filter_only(self):
        m=metrics();m['ema20']=90
        a=PortfolioReplay();b=PortfolioReplay('no_chasing')
        self.tick(a,self.snap(m=m));self.tick(b,self.snap(m=m))
        self.assertTrue(a.pending);self.assertFalse(b.pending)
        self.assertGreater(b.filters,0)
        self.assertEqual(vars(a.cfg),vars(b.cfg))
    def test_relative_strength_and_mixed_filters(self):
        m=metrics();m['rs20']=3
        self.assertFalse(entry_allowed('stronger_relative_strength',m,9,'BULLISH TREND'))
        self.assertFalse(entry_allowed('stricter_mixed_market',m,8,'CHOP / MIXED'))
        self.assertTrue(entry_allowed('stricter_mixed_market',m,8,'BULLISH TREND'))
    def test_buy_orders_expire_across_session(self):
        e=PortfolioReplay(delay=2);self.tick(e,self.snap())
        self.now+=timedelta(days=1);self.tick(e,{})
        self.assertFalse(e.positions);self.assertFalse(e.pending)
    def test_missing_quote_does_not_invent_a_fill(self):
        e=PortfolioReplay();self.tick(e,self.snap());cash=e.cash
        self.tick(e,{});self.assertEqual(e.cash,cash);self.assertFalse(e.events)
        self.assertEqual(e.stale_fills,1)
    def test_rotation_buys_only_after_sell_fill(self):
        e=PortfolioReplay();self.tick(e,self.snap());self.tick(e,self.snap())
        e.pending=[];e.queue('SELL','ABC',self.now,rotation='XYZ');e.rotation={'stage':'WAIT_SELL'}
        self.tick(e,self.snap(('ABC','XYZ')))
        self.assertNotIn('XYZ',e.positions)
        self.assertTrue(any(o['symbol']=='XYZ' and o['side']=='BUY' for o in e.pending))
        self.tick(e,self.snap(('ABC','XYZ')))
        self.assertIn('XYZ',e.positions)
        sides=[x['side'] for x in e.events];self.assertEqual(sides[:3],['BUY','SELL','BUY'])
    def test_stalled_review_waits_five_sessions_and_weakness(self):
        e=PortfolioReplay('stalled_trade_review');self.tick(e,self.snap());self.tick(e,self.snap())
        m=metrics();m['ema20']=110;m['macd_rising']=False
        for _ in range(4):self.now+=timedelta(days=1);self.tick(e,self.snap(m=m))
        self.assertFalse(any(o['side']=='SELL' for o in e.pending))
        self.now+=timedelta(days=1);self.tick(e,self.snap(m=m))
        self.assertTrue(any(o['side']=='SELL' for o in e.pending))
    def test_old_tick_cannot_change_accounting(self):
        e=PortfolioReplay();self.tick(e,self.snap());before=copy.deepcopy(e.pending)
        self.tick(e,self.snap(price=500),minutes=0)
        self.assertEqual(e.pending,before);self.assertFalse(e.events)
    def test_new_position_does_not_inherit_old_floor(self):
        e=PortfolioReplay();self.tick(e,self.snap());self.tick(e,self.snap())
        e.state['profit_floor']['ABC']=20;e.queue('SELL','ABC',self.now)
        self.tick(e,self.snap());self.assertNotIn('ABC',e.state['profit_floor'])
    def test_future_prices_cannot_change_earlier_orders(self):
        orders=[]
        for future in (50,500):
            e=PortfolioReplay()
            for p in [100,101,102]:self.tick(e,self.snap(price=p))
            early=copy.deepcopy(e.events);self.tick(e,self.snap(price=future));orders.append(early)
        self.assertEqual([(e['side'],e['price']) for e in orders[0]],[(e['side'],e['price']) for e in orders[1]])
    def test_rules_loader_does_not_execute_top_level_broker_imports(self):
        ns=load_rules();self.assertNotIn('connect',ns);self.assertNotIn('TradingClient',ns)
    def test_higher_cost_reduces_identical_simple_path(self):
        values=[]
        for cost in (.001,.0025):
            e=PortfolioReplay(cost=cost)
            for p in [100,100,101]:self.tick(e,self.snap(price=p))
            values.append(e.summary()['return_pct'])
        self.assertGreater(values[0],values[1])

if __name__=='__main__':unittest.main()
