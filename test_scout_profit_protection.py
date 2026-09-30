"""Offline runner exit and risk-budget regressions; no broker connections."""
import unittest
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import Mock
from scout_profit_protection import update_profit_floor, update_runner_floor, risk_sized_budget
from test_scout_safety import ns

class RunnerTests(unittest.TestCase):
    def test_ten_percent_gain_does_not_sell(self):
        floor, sell = update_runner_floor(10, 10, 2)
        self.assertFalse(sell)
        self.assertAlmostEqual(floor, 3.4)
        self.assertFalse(update_runner_floor(10, 7, 2, floor)[1])
        self.assertTrue(update_profit_floor(10, 7, 6)[1])

    def test_activation_and_no_forced_break_even(self):
        self.assertEqual(update_runner_floor(4.99, 0, 2), (None, False))
        floor, sell = update_runner_floor(5, 5, 3)
        self.assertLess(floor, 0)
        self.assertFalse(sell)

    def test_minimum_distance_is_percent_of_peak_price(self):
        floor, _ = update_runner_floor(20, 20, .5)
        self.assertAlmostEqual(floor, 14)
        self.assertTrue(update_runner_floor(20, 14, .5, floor)[1])

    def test_floor_ratchets_and_chart_outage_retains_it(self):
        floor, _ = update_runner_floor(20, 20, 2)
        self.assertEqual(update_runner_floor(20, 12, 8, floor), (floor, True))
        self.assertEqual(update_runner_floor(20, 12, None, floor), (floor, True))
        self.assertEqual(update_runner_floor(20, 12, None), (None, False))
        self.assertEqual(update_runner_floor(20, float('nan'), 2, floor), (floor, False))
        self.assertEqual(update_runner_floor(5, 5, 100)[0], -7.5)

    def test_old_floor_migrates_to_observation_only(self):
        state = {'high_water': {'ABC': 10}, 'profit_floor': {'ABC': 7},
                 'warning_streak': {'ABC': 2}}
        position = NS(symbol='ABC', unrealized_plpc=.07, qty=10, market_value=100)
        m = dict(close=110, ema20=100, ema50=95, sma200=90,
                 macd=2, macd_signal=1, macd_rising=True, rsi14=55, atr_pct=2)
        row = ns['analyze_position'](position, m, state, False)
        self.assertFalse(row['exit_confirmed'])
        self.assertTrue(row['legacy_exit_signal'])
        self.assertEqual(row['exit_score'], 0)
        self.assertEqual(row['confirmation'], 0)
        self.assertLess(row['profit_floor_pct'], 7)
        saved = row['profit_floor_pct']
        row = ns['analyze_position'](position, None, state, False)
        self.assertEqual(row['profit_floor_pct'], saved)
        self.assertFalse(row['exit_confirmed'])

    def test_runner_breach_orders_respect_market_and_duplicate_gates(self):
        state = {'high_water': {'ABC': 20}, 'profit_floor': {'ABC': 14},
                 'warning_streak': {}, 'exit_policy': 'runner_v1'}
        row = ns['analyze_position'](NS(symbol='ABC', unrealized_plpc=.13,
                                      qty=10, market_value=100), None, state, False)
        self.assertTrue(row['exit_confirmed'])
        self.assertEqual(row['exit_trigger'], 'RUNNER TRAIL')
        trading = NS(submit_order=Mock(return_value=NS(id='fake')))
        ns['MarketOrderRequest'] = lambda **kw: kw
        ns['OrderSide'] = NS(SELL='sell', BUY='buy')
        ns['TimeInForce'] = NS(DAY='day')
        for market, orders in [(False, {}), (True, {'ABC': object()})]:
            ns['submit_sell_if_allowed'](trading, row, market, orders)
        trading.submit_order.assert_not_called()
        self.assertIn('SUBMITTED', ns['submit_sell_if_allowed'](trading, row, True, {}))
        trading.submit_order.assert_called_once()

    def test_risk_budget_caps_and_invalid_inputs(self):
        self.assertEqual(risk_sized_budget(100000, 50000), 10000)
        self.assertEqual(risk_sized_budget(100000, 50000, risk_fraction=.005), 6666.66)
        self.assertEqual(risk_sized_budget(100000, 1000), 1000)
        self.assertEqual(risk_sized_budget(100000, 50000, stop_pct=-15), 5000)
        self.assertEqual(risk_sized_budget(100000, 50000, stop_pct=0), 0)
        self.assertEqual(risk_sized_budget(float('nan'), 100), 0)

    def test_entry_orders_use_risk_budget_and_closed_market_blocks(self):
        trading = NS(submit_order=Mock(return_value=NS(id='fake')))
        ns['MarketOrderRequest'] = lambda **kw: kw
        ns['OrderSide'] = NS(BUY='buy', SELL='sell')
        ns['TimeInForce'] = NS(DAY='day')
        candidates = [{'symbol': 'ABC'}, {'symbol': 'XYZ'}]
        account = NS(equity=100000, cash=36000)
        ns['submit_buys_if_allowed'](trading, candidates, account, False, set(), {})
        trading.submit_order.assert_not_called()
        rows = ns['submit_buys_if_allowed'](trading, candidates, account, True, set(), {})
        self.assertEqual(trading.submit_order.call_count, 2)
        self.assertEqual(sum(r['planned_notional'] for r in rows), 1000)
        self.assertTrue(all(r['planned_risk_dollars'] <= 750 for r in rows))

    def test_rotation_buy_uses_same_risk_cap_after_sell_fill(self):
        saved_cfg = ns['CFG']
        saved_wait = ns['_wait_for_terminal_order']
        saved_orders = ns.get('open_orders_by_symbol')
        try:
            ns['CFG'] = replace(saved_cfg, risk_fraction_per_trade=.005)
            ns['_wait_for_terminal_order'] = Mock(return_value=(True, NS(status='filled')))
            ns['open_orders_by_symbol'] = lambda _: {}
            ns['MarketOrderRequest'] = lambda **kw: kw
            ns['OrderSide'] = NS(BUY='buy', SELL='sell')
            ns['TimeInForce'] = NS(DAY='day')
            trading = NS(get_all_positions=Mock(side_effect=[
                [NS(symbol='OLD', qty=10)], []]),
                get_account=Mock(return_value=NS(equity=100000, cash=60000)),
                submit_order=Mock(side_effect=[NS(id='sell'), NS(id='buy')]))
            upgrade = {'upgrade_confirmed': True, 'upgrade_confirmation': 3,
                       'replace_symbol': 'OLD', 'candidate': {'symbol': 'NEW'}}
            result = ns['execute_upgrade_rotation_if_allowed'](trading, upgrade, True, {})
            self.assertTrue(result['buy_filled'])
            buy = trading.submit_order.call_args_list[1].kwargs['order_data']
            self.assertEqual(buy['notional'], 6666.66)
        finally:
            ns['CFG'] = saved_cfg
            ns['_wait_for_terminal_order'] = saved_wait
            ns['open_orders_by_symbol'] = saved_orders

if __name__ == '__main__':
    unittest.main()
