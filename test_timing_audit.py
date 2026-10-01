import unittest
from dataclasses import replace
from datetime import datetime,timedelta
from profit_strategy_variants import ReferenceStrategy, VARIANTS
from backtest_timing_audit import future_metrics, audit_events, classify

class TimingAuditTests(unittest.TestCase):
    def obs(self, prices):
        start=datetime.fromisoformat('2026-10-01T09:00:00-07:00')
        return [dict(at=start+timedelta(minutes=i*15),price=p,atr=2,score=0,regime='BULLISH TREND',
                     signal={'entry_score':90,'decision':'QUALIFIED'},fast={'weak':False,'qualified':True})
                for i,p in enumerate(prices)]
    def test_incomplete_future_is_pending(self):
        self.assertIsNone(future_metrics(self.obs([100,101]),0,5))
    def test_exact_future_horizon(self):
        r=future_metrics(self.obs([100,101,99,102,98,103,10000]),0,5)
        self.assertAlmostEqual(r['max_gain_pct'],3)
        self.assertAlmostEqual(r['max_loss_pct'],-2)
    def test_future_metrics_do_not_change_historical_fills(self):
        config=VARIANTS[1]
        a=self.obs([100,90,89,90,91,92]+[93]*30)
        b=self.obs([100,90,89,90,91,92]+[50]*30)
        early_a=[(e['at'],e['side'],e['price']) for e in audit_events('ABC',a,config,.001) if e['index']<=5]
        early_b=[(e['at'],e['side'],e['price']) for e in audit_events('ABC',b,config,.001) if e['index']<=5]
        self.assertEqual(early_a,early_b)
    def test_no_reclaim_changes_only_the_sale_price_gate(self):
        baseline=ReferenceStrategy(VARIANTS[1])
        relaxed=ReferenceStrategy(replace(VARIANTS[1],name='relaxed',reclaim_sale=False))
        for o in self.obs([100,90,89,87,88,88.5]):baseline.step(o);relaxed.step(o)
        self.assertIsNone(baseline.state['pending'])
        self.assertEqual(relaxed.state['pending']['side'],'BUY')
    def test_pending_outcomes_excluded_from_success_counts(self):
        events=audit_events('ABC',self.obs([100,90,89]),VARIANTS[1],.001)
        r=classify(events)
        self.assertEqual(r['exits'],0)
        self.assertEqual(r['pending'],1)
    def test_rebound_and_decline_categories_can_overlap(self):
        event=dict(side='SELL',future_20=dict(max_gain_pct=2,max_loss_pct=-2),best_move_while_out_pct=2)
        r=classify([event])
        self.assertEqual(r['rebound_1pct_after_exit'],1)
        self.assertEqual(r['decline_1pct_after_exit'],1)
    def test_diagnostics_do_not_import_order_api(self):
        import ast,pathlib
        tree=ast.parse(pathlib.Path('backtest_timing_audit.py').read_text())
        names=[x.module for x in ast.walk(tree) if isinstance(x,ast.ImportFrom)]
        self.assertFalse(any('alpaca' in x for x in names))

if __name__=='__main__':unittest.main()
