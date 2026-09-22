"""Offline regression checks; no broker connection or real orders are possible."""
import ast
import unittest
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
from scout_journal import record_cycle, journal_html

source = Path(__file__).with_name('scout_runner.py').read_text(encoding='utf-8')
tree = ast.parse(source)
names = {'ScoutConfig', 'prepare_confirmation_cycle', 'analyze_position',
         'confirm_upgrade_persistence', '_order_status_text', '_wait_for_terminal_order',
         'submit_sell_if_allowed', 'dashboard_html', 'run_scout_cycle'}
ns = {'dataclass': dataclass, 'record_cycle': record_cycle, 'journal_html': journal_html,
      'refresh_loss_log': Mock()}
exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))
                             and n.name in names], type_ignores=[]), '<isolated scout functions>', 'exec'), ns)
ns['CFG'] = ns['ScoutConfig']()

class ScoutSafetyTests(unittest.TestCase):
    def setUp(self):
        self.state = {'high_water': {}, 'warning_streak': {}}
        self.now = datetime(2026, 9, 21, 14, 0, tzinfo=timezone.utc)
        self.position = NS(symbol='OXY', unrealized_plpc=-0.075, qty=10, market_value=100)
        self.upgrade = {'candidate': {'symbol': 'MSTR', 'entry_score': 9},
                        'replace_symbol': 'USB', 'replace_exit_score': 7, 'score_advantage': 7}

    def test_weekend_and_legacy_confirmations_are_discarded(self):
        self.state['upgrade_challenge'] = {'confirmations': 4}
        self.state['warning_streak'] = {'USB': 2}
        for _ in range(4):
            advance = ns['prepare_confirmation_cycle'](self.state, False, self.now)
            result = ns['confirm_upgrade_persistence'](dict(self.upgrade), self.state, advance)
            self.assertEqual(result['upgrade_confirmation'], 0)
            self.assertFalse(result['upgrade_confirmed'])
        self.assertEqual(self.state['warning_streak'], {})

    def test_spacing_and_session_reset(self):
        counts = []
        for minutes in [0, 2, 15, 30]:
            advance = ns['prepare_confirmation_cycle'](self.state, True, self.now + timedelta(minutes=minutes))
            result = ns['confirm_upgrade_persistence'](dict(self.upgrade), self.state, advance)
            counts.append(result['upgrade_confirmation'])
        self.assertEqual(counts, [1, 1, 2, 3])
        self.assertTrue(result['upgrade_confirmed'])
        advance = ns['prepare_confirmation_cycle'](self.state, True, self.now + timedelta(days=1))
        result = ns['confirm_upgrade_persistence'](dict(self.upgrade), self.state, advance)
        self.assertEqual(result['upgrade_confirmation'], 1)

    def test_legacy_counts_do_not_survive_first_open_check(self):
        self.state['upgrade_challenge'] = {'candidate_symbol': 'MSTR', 'replace_symbol': 'USB', 'confirmations': 4}
        advance = ns['prepare_confirmation_cycle'](self.state, True, self.now)
        self.assertEqual(ns['confirm_upgrade_persistence'](dict(self.upgrade), self.state, advance)['upgrade_confirmation'], 1)

    def test_missing_chart_hard_stop_and_boundary(self):
        result = ns['analyze_position'](self.position, None, self.state, False)
        self.assertTrue(result['exit_confirmed'])
        self.position.unrealized_plpc = -0.0749
        self.assertFalse(ns['analyze_position'](self.position, None, self.state)['exit_confirmed'])

    def test_warning_needs_two_eligible_checks(self):
        self.position.unrealized_plpc = -0.04
        metrics = dict(close=1, ema20=2, ema50=3, sma200=4, macd=0, macd_signal=1,
                       macd_rising=False, rsi14=30, atr_pct=1)
        for eligible, expected in [(True, False), (False, False), (True, True)]:
            self.assertEqual(ns['analyze_position'](self.position, metrics, self.state, eligible)['exit_confirmed'], expected)

    def test_partial_fill_is_not_terminal(self):
        ns['time'] = NS(time=Mock(side_effect=[0, 1, 2]), sleep=Mock())
        trading = NS(get_order_by_id=Mock(side_effect=[NS(status='partially_filled'), NS(status=NS(value='filled'))]))
        filled, _ = ns['_wait_for_terminal_order'](trading, 'fake')
        self.assertTrue(filled)
        self.assertEqual(trading.get_order_by_id.call_count, 2)
        ns['time'].sleep.assert_called_once()

    def test_partial_fill_timeout_and_rejection(self):
        for status, times in [('partially_filled', [0, 1, 50]), ('rejected', [0, 1])]:
            ns['time'] = NS(time=Mock(side_effect=times), sleep=Mock())
            trading = NS(get_order_by_id=Mock(return_value=NS(status=status)))
            self.assertFalse(ns['_wait_for_terminal_order'](trading, 'fake')[0])

    def test_hard_stop_order_gates(self):
        row = ns['analyze_position'](self.position, None, self.state)
        trading = NS(submit_order=Mock(return_value=NS(id='fake')))
        ns['MarketOrderRequest'] = lambda **kw: kw
        ns['OrderSide'] = NS(SELL='sell')
        ns['TimeInForce'] = NS(DAY='day')
        self.assertIn('MARKET CLOSED', ns['submit_sell_if_allowed'](trading, row, False, {}))
        self.assertIn('OPEN ORDER', ns['submit_sell_if_allowed'](trading, row, True, {'OXY': object()}))
        trading.submit_order.assert_not_called()
        self.assertIn('SUBMITTED', ns['submit_sell_if_allowed'](trading, row, True, {}))
        trading.submit_order.assert_called_once()

    def test_dashboard_explains_replacement_and_loss_rules(self):
        row = ns['analyze_position'](self.position, None, self.state)
        row['order_status'] = 'WAITING — MARKET CLOSED'
        upgrade = {**self.upgrade, 'upgrade_confirmation': 0, 'order_status': 'WAITING <closed>'}
        report = dict(positions=[row], candidates=[], upgrade=upgrade, updated='saved sample', equity=100,
                      market_open=False)
        html = ns['dashboard_html'](report)
        for text in ['USB → MSTR', '0/3', '15 minutes', 'Hard loss limit', '&lt;closed&gt;', 'trigger: 10']:
            self.assertIn(text, html)
        report['upgrade'] = None
        self.assertIn('No replacement currently qualifies', ns['dashboard_html'](report))

    def test_chart_request_failure_still_checks_loss_in_cycle(self):
        trading = NS(get_clock=Mock(return_value=NS(is_open=True, timestamp=self.now)),
                     get_all_positions=Mock(return_value=[self.position]),
                     submit_order=Mock(return_value=NS(id='fake')))
        ns.update(datetime=datetime, PACIFIC=timezone.utc,
                  connect=lambda: (trading, object(), object(), NS(equity=1000, cash=500)),
                  mount_and_load_state=lambda: self.state,
                  open_orders_by_symbol=lambda _: {},
                  bars_frame=Mock(side_effect=RuntimeError('simulated unavailable data')),
                  scan_candidates=Mock(side_effect=RuntimeError('simulated unavailable data')),
                  choose_upgrade=lambda *a: None, submit_buys_if_allowed=lambda *a: [],
                  save_state=Mock(), append_history=Mock(), publish_dashboard=Mock(),
                  pd=NS(DataFrame=lambda _: NS(__unused=True)),
                  MarketOrderRequest=lambda **kw: kw, OrderSide=NS(SELL='sell'), TimeInForce=NS(DAY='day'))
        class Table:
            def __getitem__(self, key): return self
            def to_string(self, **kw): return 'simulated report'
        ns['pd'] = NS(DataFrame=lambda _: Table())
        report = ns['run_scout_cycle']()
        self.assertTrue(report['positions'][0]['exit_confirmed'])
        trading.submit_order.assert_called_once()
        ns['save_state'].assert_called_once()

if __name__ == '__main__':
    unittest.main()

