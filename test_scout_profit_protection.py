"""Offline checks of independent exit behavior and the paper-order gates."""
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock
from scout_profit_protection import update_profit_floor
from test_scout_safety import ns


class ProfitProtectionTests(unittest.TestCase):
    def test_small_winner_needs_two_percent_peak(self):
        self.assertEqual(update_profit_floor(1.99, 0, 2), (None, False))
        self.assertEqual(update_profit_floor(2, 0, 6), (0, True))

    def test_volatility_leash_and_winner_tiers(self):
        self.assertEqual(update_profit_floor(3, 2, 1.25), (1.75, False))
        self.assertEqual(update_profit_floor(6, 3, 6), (6 * .65, True))
        self.assertEqual(update_profit_floor(10, 7, 6), (7, True))

    def test_floor_never_loosens_when_volatility_increases(self):
        floor, _ = update_profit_floor(6, 5, 1.25)
        self.assertEqual(update_profit_floor(6, 4.7, 6, floor), (4.75, True))
        self.assertEqual(update_profit_floor(6, 5, None, floor), (4.75, False))

    def test_missing_data_retains_floor_and_seeds_strong_winner(self):
        self.assertEqual(update_profit_floor(3, .5, None, 1), (1, True))
        self.assertEqual(update_profit_floor(6, 2, None), (6 * .65, True))
        self.assertEqual(update_profit_floor(3, .5, None), (None, False))

    def test_invalid_observation_does_not_trigger(self):
        self.assertEqual(update_profit_floor(6, float('nan'), 2, 4), (4, False))

    def test_profit_exit_bypasses_score_and_confirmation(self):
        state = {'high_water': {'INTC': 6.253}, 'warning_streak': {}}
        position = NS(symbol='INTC', unrealized_plpc=.03, qty=10, market_value=100)
        metrics = dict(close=110, ema20=100, ema50=95, sma200=90,
                       macd=2, macd_signal=1, macd_rising=False, rsi14=55, atr_pct=2)
        row = ns['analyze_position'](position, metrics, state, False)
        self.assertLess(row['exit_score'], 10)
        self.assertEqual(row['confirmation'], 0)
        self.assertTrue(row['exit_confirmed'])
        self.assertEqual(row['exit_trigger'], 'PROFIT FLOOR')
        trading = NS(submit_order=Mock(return_value=NS(id='fake')))
        ns['MarketOrderRequest'] = lambda **kw: kw
        ns['OrderSide'] = NS(SELL='sell')
        ns['TimeInForce'] = NS(DAY='day')
        self.assertIn('MARKET CLOSED', ns['submit_sell_if_allowed'](trading, row, False, {}))
        self.assertIn('OPEN ORDER', ns['submit_sell_if_allowed'](trading, row, True, {'INTC': object()}))
        trading.submit_order.assert_not_called()
        self.assertIn('SUBMITTED', ns['submit_sell_if_allowed'](trading, row, True, {}))
        trading.submit_order.assert_called_once()

    def test_cycle_data_failure_still_protects_winner(self):
        row = ns['analyze_position'](
            NS(symbol='INTC', unrealized_plpc=-.014, qty=10, market_value=100),
            None, {'high_water': {'INTC': 6.253}, 'warning_streak': {}}, False)
        self.assertTrue(row['profit_exit'])
        self.assertTrue(row['exit_confirmed'])
        self.assertIn('Profit floor breached', '; '.join(row['reasons']))

    def test_recovery_above_floor_does_not_force_sale(self):
        self.assertEqual(update_profit_floor(6, 5, 6, 3.9), (6 * .65, False))


if __name__ == '__main__':
    unittest.main()
