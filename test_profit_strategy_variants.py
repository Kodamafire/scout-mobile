import ast
import copy
from datetime import datetime,timedelta
from pathlib import Path
import unittest
import pandas as pd
from profit_strategy_variants import ReferenceStrategy, Variant, VARIANTS, fast_features

class VariantTests(unittest.TestCase):
    def setUp(self):self.now=datetime.fromisoformat('2026-10-01T09:00:00-07:00')
    def o(self,p,weak=False,qual=True,atr=2,minutes=15):
        self.now+=timedelta(minutes=minutes)
        return dict(at=self.now,price=p,atr=atr,score=3 if weak else 0,regime='BULLISH TREND',
                    signal={'entry_score':60 if weak else 90,'decision':'QUALIFIED' if qual else 'WAIT'},
                    fast={'weak':weak,'qualified':qual})
    def test_same_initial_cost_as_holding(self):
        e=ReferenceStrategy(VARIANTS[0]);v=e.step(self.o(100))
        self.assertAlmostEqual(v,.999/1.001)
    def test_strong_adaptive_runner_gets_breathing_room(self):
        e=ReferenceStrategy(VARIANTS[2]);e.step(self.o(100));e.step(self.o(105));e.step(self.o(104))
        self.assertIsNone(e.state['pending'])
    def test_weakness_tightens_floor_without_loosening_it(self):
        e=ReferenceStrategy(VARIANTS[2]);e.step(self.o(100));e.step(self.o(105));before=e.state['floor']
        e.step(self.o(104,weak=True));tight=e.state['floor']
        self.assertGreater(tight,before)
        e.step(self.o(104));self.assertGreaterEqual(e.state['floor'],tight)
    def test_volatility_gives_wilder_stock_more_room(self):
        floors=[]
        for atr in (1,4):
            e=ReferenceStrategy(VARIANTS[0]);e.step(self.o(100));e.step(self.o(110,atr=atr));floors.append(e.state['floor'])
        self.assertGreater(floors[0],floors[1])
    def test_partial_sale_preserves_half_and_conserves_capital(self):
        e=ReferenceStrategy(VARIANTS[3]);e.step(self.o(100));e.step(self.o(105));e.step(self.o(103.9,weak=True))
        self.assertEqual(e.state['pending']['side'],'HALF');units=e.state['units']
        v=e.step(self.o(103.8))
        self.assertAlmostEqual(e.state['units'],units/2)
        self.assertAlmostEqual(e.state['cash'],units/2*103.8*.999)
        self.assertAlmostEqual(v,units*103.8*.999)
    def test_hard_stop_sells_full_position_not_half(self):
        e=ReferenceStrategy(VARIANTS[3]);e.step(self.o(100));e.step(self.o(90,weak=True))
        self.assertEqual(e.state['pending']['side'],'SELL')
    def cash(self,e):
        e.step(self.o(100));e.step(self.o(90));e.step(self.o(89))
    def test_fast_reentry_requires_recovery_and_rising_prices(self):
        e=ReferenceStrategy(VARIANTS[2]);self.cash(e)
        e.step(self.o(88));self.assertEqual(e.state['confirmations'],0)
        e.step(self.o(90));e.step(self.o(89.5));self.assertEqual(e.state['confirmations'],0)
        e.step(self.o(90));e.step(self.o(91));self.assertEqual(e.state['pending']['side'],'BUY')
        e.step(self.o(92));self.assertGreater(e.state['units'],0)
    def test_fast_reentry_blocks_bearish_regime(self):
        e=ReferenceStrategy(VARIANTS[2]);self.cash(e)
        for p in (90,91,92):
            o=self.o(p);o['regime']='BEARISH TREND';e.step(o)
        self.assertIsNone(e.state['pending'])
    def test_two_bar_delay_does_not_use_signal_price(self):
        e=ReferenceStrategy(VARIANTS[0],delay=2);e.step(self.o(100));e.step(self.o(90))
        e.step(self.o(80));self.assertGreater(e.state['units'],0)
        e.step(self.o(70));self.assertEqual(e.state['units'],0)
        self.assertLess(e.state['cash'],.71)
    def test_old_or_duplicate_observations_do_not_fill(self):
        e=ReferenceStrategy(VARIANTS[0]);e.step(self.o(100));e.step(self.o(90));before=copy.deepcopy(e.state)
        e.step(self.o(80,minutes=0));self.assertEqual(before,e.state)
        e.step(self.o(70,minutes=-5));self.assertEqual(before,e.state)
    def test_session_rollover_cancels_stale_buy(self):
        e=ReferenceStrategy(VARIANTS[2]);self.cash(e);e.step(self.o(90));e.step(self.o(91))
        self.now+=timedelta(days=1);e.step(self.o(92))
        self.assertEqual(e.state['units'],0);self.assertIsNone(e.state['pending'])
    def test_reentry_limit(self):
        e=ReferenceStrategy(VARIANTS[2]);self.cash(e);e.state['reentries']=2
        e.step(self.o(90));e.step(self.o(91));self.assertIsNone(e.state['pending'])
    def test_bad_price_does_not_corrupt_state(self):
        e=ReferenceStrategy(VARIANTS[0]);e.step(self.o(100));before=copy.deepcopy(e.state)
        for p in (0,-5,float('nan'),float('inf')):
            self.assertIsNone(e.step(self.o(p)));self.assertEqual(e.state,before)
    def test_invalid_execution_settings(self):
        for cost,delay in [(-1,1),(1,1),(.001,0)]:
            with self.assertRaises(ValueError):ReferenceStrategy(VARIANTS[0],cost,delay)
    def test_features_have_no_future_price_or_volume_leak(self):
        frame=pd.DataFrame({'close':[100+i*.1 for i in range(60)],'volume':[1000]*60})
        before=fast_features(frame)
        changed=frame.copy();changed.loc[45:,'close']=10000;changed.loc[45:,'volume']=100000
        self.assertEqual(before[:45],fast_features(changed)[:45])
    def test_no_order_or_broker_imports(self):
        tree=ast.parse(Path('profit_strategy_variants.py').read_text())
        imports=[x for x in ast.walk(tree) if isinstance(x,(ast.Import,ast.ImportFrom))]
        self.assertTrue(all(getattr(x,'module','') in ('dataclasses','math') for x in imports))
    def test_randomized_capital_never_becomes_negative(self):
        import random
        for config in VARIANTS:
            rng=random.Random(123);e=ReferenceStrategy(config);price=100
            for i in range(1000):
                price*=1+rng.uniform(-.05,.05)
                value=e.step(self.o(price,weak=rng.choice([True,False]),qual=rng.choice([True,False])))
                self.assertGreaterEqual(value,0)
                self.assertGreaterEqual(e.state['units'],0);self.assertGreaterEqual(e.state['cash'],0)
    def test_fee_drag_cannot_improve_identical_trade_path(self):
        values=[]
        for cost in (.001,.005):
            e=ReferenceStrategy(VARIANTS[0],cost)
            for p in (100,105,90,89):value=e.step(self.o(p))
            values.append(value)
        self.assertGreater(values[0],values[1])

if __name__=='__main__':unittest.main()
