import copy
import unittest
from datetime import datetime, timedelta
from shadow_profit_trial import update_trial, trial_html

class TrialTests(unittest.TestCase):
    def setUp(self):
        self.state = {}
        self.now = datetime.fromisoformat('2026-10-01T09:00:00-07:00')
    def tick(self, price=100, weak=False, qualified=True, minutes=15, open=True, status='OK', evidence=True):
        self.now += timedelta(minutes=minutes)
        row = dict(symbol='ABC', direction='LONG', price=price,
                   entry_score=60 if weak else 90, decision='QUALIFIED' if qualified else 'WAIT')
        shadow = dict(status=status, evidence=[row] if evidence else [], held=[])
        return update_trial(self.state, shadow, [dict(symbol='ABC', exit_score=3 if weak else 0)], self.now, open)
    def row(self): return self.state['profit_trial']['rows']['ABC']
    def test_no_hindsight_initialization(self):
        self.tick(100)
        self.assertEqual(self.row()['peak'],100)
        self.assertEqual(self.row()['fills'],0)
    def test_strong_runner_not_sold_on_small_dip(self):
        self.tick(); self.tick(105); self.tick(104)
        self.assertIsNone(self.row()['pending'])
    def test_weak_momentum_drop_signals_and_next_price_fills(self):
        self.tick(); self.tick(105); self.tick(104,weak=True)
        self.assertEqual(self.row()['pending'],'SELL')
        units=self.row()['units'];self.tick(103)
        self.assertAlmostEqual(self.row()['cash'],units*103*.999)
        self.assertEqual(self.row()['units'],0)
    def test_floor_fires_even_without_momentum_warning(self):
        self.tick();self.tick(106);self.tick(102.9)
        self.assertEqual(self.row()['pending'],'SELL')
    def test_tiny_gains_do_not_activate_protection(self):
        self.tick();self.tick(100.5);self.tick(100,weak=True)
        self.assertIsNone(self.row()['pending'])
    def test_hard_loss_and_gap_fill(self):
        self.tick();self.tick(92)
        self.assertEqual(self.row()['pending'],'SELL')
        self.tick(80)
        self.assertLess(self.row()['cash'],.81)
    def cash_position(self):
        self.tick();self.tick(105);self.tick(102);self.tick(101)
    def test_two_spaced_confirmations_and_next_observation_buy(self):
        self.cash_position();self.tick(102)
        self.tick(102.1,minutes=1)
        self.assertIsNone(self.row()['pending'])
        self.tick(103,minutes=14)
        self.assertEqual(self.row()['pending'],'BUY')
        cash=self.row()['cash'];self.tick(104)
        self.assertAlmostEqual(self.row()['units'],cash/(104*1.001))
    def test_unqualified_bounce_resets_confirmation(self):
        self.cash_position();self.tick(102);self.tick(102,qualified=False);self.tick(103)
        self.assertEqual(self.row()['confirmations'],1)
    def test_closed_market_never_fills_pending_order(self):
        self.tick();self.tick(92);before=copy.deepcopy(self.row())
        self.tick(80,open=False)
        self.assertEqual(self.row(),before)
    def test_missing_or_unavailable_data_never_fills(self):
        self.tick();self.tick(92);before=copy.deepcopy(self.row())
        self.tick(80,evidence=False);self.assertEqual(self.row(),before)
        self.tick(80,status='UNAVAILABLE');self.assertEqual(self.row(),before)
    def test_same_observation_is_idempotent(self):
        self.tick();self.tick(92);before=copy.deepcopy(self.row())
        self.tick(80,minutes=0);self.assertEqual(self.row(),before)
    def test_session_resets_confirmation_and_reentry_limit(self):
        self.cash_position();self.tick();self.row()['reentries']=2
        self.now += timedelta(days=1);self.tick()
        self.assertEqual(self.row()['reentries'],0)
        self.assertEqual(self.row()['confirmations'],1)
    def test_two_reentries_per_session_limit(self):
        self.cash_position();self.row()['reentries']=2
        self.tick();self.tick()
        self.assertIsNone(self.row()['pending'])
    def test_initial_and_liquidation_costs_match_baseline(self):
        trial=self.tick();html=trial_html(trial)
        self.assertIn('Hold -0.20%',html)
        self.assertIn('Protect/re-enter -0.20%',html)
    def test_bad_prices_ignored(self):
        self.tick();before=copy.deepcopy(self.row())
        for p in [0,-10,float('nan'),float('inf')]:
            self.tick(p);self.assertEqual(self.row(),before)
    def test_state_round_trip(self):
        import json
        self.cash_position();self.state=json.loads(json.dumps(self.state))
        self.tick();self.tick();self.assertEqual(self.row()['pending'],'BUY')
    def test_all_paths_preserve_nonnegative_cash_and_units(self):
        import random
        rng=random.Random(47);self.tick();price=100
        for i in range(500):
            price *= 1+rng.uniform(-.06,.06)
            self.tick(price,weak=rng.choice([True,False]),qualified=rng.choice([True,False]))
            self.assertGreaterEqual(self.row()['cash'],0)
            self.assertGreaterEqual(self.row()['units'],0)
            self.assertFalse(self.row()['cash'] and self.row()['units'])
    def test_older_observation_cannot_rewind_or_fill(self):
        self.tick();self.tick(92);before=copy.deepcopy(self.row())
        self.tick(80,minutes=-5);self.assertEqual(self.row(),before)
    def test_stale_buy_confirmation_canceled_next_session(self):
        self.cash_position();self.tick();self.tick()
        self.assertEqual(self.row()['pending'],'BUY')
        self.now += timedelta(days=1);self.tick(105)
        self.assertEqual(self.row()['units'],0)
        self.assertIsNone(self.row()['pending'])
    def test_higher_cost_reduces_same_path_result(self):
        from shadow_profit_trial import COST
        def replay(cost):
            st={}
            for i,price in enumerate([100,105,102,101,102,103,104]):
                signal=dict(symbol='ABC',direction='LONG',price=price,entry_score=90,decision='QUALIFIED')
                update_trial(st,dict(status='OK',evidence=[signal]),[dict(symbol='ABC',exit_score=0)],
                             self.now+timedelta(minutes=15*i),True,cost)
            r=st['profit_trial']['rows']['ABC']
            return r['cash']+r['units']*104*(1-cost)
        self.assertGreater(replay(.001),replay(.0025))

    def test_module_has_no_broker_or_order_imports(self):
        import ast, pathlib
        tree=ast.parse(pathlib.Path('shadow_profit_trial.py').read_text())
        imports=[n for n in ast.walk(tree) if isinstance(n,(ast.Import,ast.ImportFrom))]
        self.assertTrue(all(getattr(n,'module','') in ('datetime','html','math') for n in imports))

if __name__=='__main__': unittest.main()
