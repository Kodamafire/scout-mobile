"""Offline decision, accounting and shared-budget research regressions."""
import copy
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from zoneinfo import ZoneInfo
from scout_strategy_desk import (PausePolicy, evaluate_pause, evaluate_entry_risk,
                                 build_desk, desk_html)

CFG = NS(minimum_cash_reserve_fraction=.35, max_positions=4,
         minimum_order_dollars=50, position_fraction=.10,
         hard_stop_pct=-7.5, risk_fraction_per_trade=.0075)


class StrategyDeskTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 5, 9, tzinfo=ZoneInfo('America/Los_Angeles'))

    def test_profit_pause_latches_and_keeps_position_management(self):
        policy = PausePolicy(profit_activation=100, max_giveback=30)
        state, result = evaluate_pause({}, self.now, True, 120, policy)
        self.assertTrue(result['allow_new_entries'])
        state, result = evaluate_pause(state, self.now+timedelta(minutes=15), True, 90, policy)
        self.assertEqual(result['status'], 'PROFIT GIVEBACK LIMIT')
        self.assertTrue(result['manage_positions'])
        state, result = evaluate_pause(state, self.now+timedelta(minutes=30), True, 200, policy)
        self.assertFalse(result['allow_new_entries'])
        _, result = evaluate_pause(state, self.now+timedelta(days=1), True, 0, policy)
        self.assertTrue(result['allow_new_entries'])

    def test_loss_boundary_and_disabled_policy(self):
        for pnl, allowed in [(-99, True), (-100, False)]:
            _, r = evaluate_pause({}, self.now, True, pnl, PausePolicy(loss_limit=100))
            self.assertEqual(r['allow_new_entries'], allowed)
        _, r = evaluate_pause({}, self.now, True, -1000, PausePolicy())
        self.assertTrue(r['allow_new_entries'])

    def test_closed_duplicate_stale_and_bad_data_do_not_advance(self):
        state, _ = evaluate_pause({}, self.now, True, 100, PausePolicy(loss_limit=100))
        for at, opened, pnl in [(self.now, True, -200), (self.now-timedelta(minutes=1), True, -200),
                                (self.now+timedelta(days=1), False, 0),
                                (self.now+timedelta(minutes=15), True, float('nan'))]:
            saved = copy.deepcopy(state)
            updated, r = evaluate_pause(state, at, opened, pnl, PausePolicy(loss_limit=100))
            self.assertEqual(updated, saved)
            self.assertEqual(state, saved)
            self.assertFalse(r['allow_new_entries'])
            self.assertTrue(r['manage_positions'])

    def test_policy_rejects_unusable_thresholds(self):
        for value in (0, -1, float('nan'), float('inf')):
            with self.assertRaises(ValueError): PausePolicy(loss_limit=value)
        with self.assertRaises(ValueError): PausePolicy(profit_activation=100)

    def test_session_uses_pacific_date_even_for_utc_observations(self):
        at = datetime(2026, 10, 6, 1, tzinfo=timezone.utc)
        state, _ = evaluate_pause({}, at, True, 0, PausePolicy())
        self.assertEqual(state['session'], '2026-10-05')
        with self.assertRaises(ValueError):
            evaluate_pause({}, at.replace(tzinfo=None), True, 0, PausePolicy())

    def test_shared_cash_cannot_be_spent_by_two_workers(self):
        rows = evaluate_entry_risk(1000, 500, [],
            [{'symbol':'A', 'notional':100}, {'symbol':'B', 'notional':100}], [], CFG)
        self.assertTrue(rows[0]['allowed'])
        self.assertEqual(rows[1]['reason'], 'SHARED CASH RESERVE')

    def test_duplicates_slots_and_pending_orders(self):
        rows = evaluate_entry_risk(1000, 1000, ['A','B','C'],
            [{'symbol':'D', 'notional':100}, {'symbol':'d', 'notional':100},
             {'symbol':'E', 'notional':100}], [], CFG)
        self.assertEqual([r['reason'] for r in rows],
            ['RESEARCH BUDGET RESERVED','DUPLICATE EXPOSURE','POSITION LIMIT'])
        rows = evaluate_entry_risk(1000, 1000, [], [{'symbol':'A', 'notional':100}], ['OTHER'], CFG)
        self.assertFalse(rows[0]['allowed'])

    def test_bad_account_and_notional_fail_closed(self):
        for equity in (0, -1, float('nan'), None):
            self.assertFalse(evaluate_entry_risk(equity, 500, [], [{'symbol':'A','notional':100}], [], CFG)[0]['allowed'])
        for amount in (None, -100, float('nan'), 20, 101):
            self.assertFalse(evaluate_entry_risk(1000, 1000, [], [{'symbol':'A','notional':amount}], [], CFG)[0]['allowed'])
        stricter = NS(**{**vars(CFG), 'risk_fraction_per_trade':.005})
        self.assertEqual(evaluate_entry_risk(1000, 1000, [], [{'symbol':'A','notional':100}], [], stricter)[0]['reason'], 'MODELED TRADE RISK LIMIT')

    def test_results_use_only_matched_fills_not_requests(self):
        report = dict(equity=1000, cash=500, positions=[], candidates=[], journal={'orders':{
            'buy':dict(symbol='A', status='filled', qty='2', price='100', side='buy', filled_at='2026-10-01T09:00:00-07:00'),
            'sell':dict(symbol='A', status='filled', qty='2', price='110', side='sell', filled_at='2026-10-02T09:00:00-07:00'),
            'unknown':dict(symbol='B', status='filled', qty='1', price='100', side='sell', filled_at='2026-10-02T09:00:00-07:00'),
            'request':dict(symbol='C', status='submitted', qty='0', price=None)}})
        saved = copy.deepcopy(report)
        desk = build_desk(report, CFG)
        r = desk['workers'][0]['results']
        self.assertEqual((r['realized_pnl'], r['matched_sales'], r['unknown_sales']), (20,1,1))
        self.assertEqual(report, saved)
        self.assertNotIn('results', desk['workers'][1])

    def test_risk_snapshot_is_gross_and_missing_values_are_unknown(self):
        report=dict(equity=1000, cash=500, positions=[dict(symbol='A',market_value=-200)])
        desk=build_desk(report, CFG)
        self.assertEqual(desk['risk']['gross_exposure_pct'],20)
        report['positions'][0]['market_value']=None
        self.assertEqual(build_desk(report, CFG)['risk']['status'],'UNAVAILABLE')

    def test_dashboard_escapes_decisions_and_does_not_invent_profit(self):
        report=dict(equity=1000,cash=500,positions=[],candidates=[dict(symbol='<script>',entry_score=8,order_status='WAIT <now>')])
        html=desk_html(build_desk(report,CFG))
        self.assertIn('&lt;script&gt;',html)
        self.assertNotIn('<script>',html)
        self.assertIn('P&amp;L: Unavailable',html)
        self.assertIn('inactive',html)
        self.assertIn('pending orders and correlations',html)

if __name__ == '__main__': unittest.main()
