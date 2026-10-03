"""Entry gates and completed, same-time volume; all broker calls are mocked."""
import unittest
from datetime import datetime
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
import pandas as pd
from scout_volume import volume_window, same_time_volume
from scout_portfolio_replay import load_rules, PortfolioReplay
from scout_shadow_v48 import directional_decision


def metric(**changes):
    row = dict(close=100, ema20=99, ema50=95, sma200=90, macd=2,
               macd_signal=1, macd_rising=True, rsi14=60, atr_pct=1,
               relative_volume=1.5, avg_dollar_volume=1e8, rs20=5)
    row.update(changes)
    return row


class VolumeTests(unittest.TestCase):
    def setUp(self):
        self.days = pd.bdate_range('2026-09-01', '2026-10-02')
        self.sessions = [NS(open=f'{d.date()} 09:30', close=f'{d.date()} 16:00') for d in self.days]
        self.now = pd.Timestamp('2026-10-02 10:16', tz='America/New_York')
        stamps, vols = [], []
        for day in self.days:
            for stamp in pd.date_range(f'{day.date()} 09:30', periods=26, freq='15min', tz='America/New_York'):
                stamps.append(stamp)
                vols.append(150 if day.date() == self.now.date() else 100)
        self.frame = pd.DataFrame({'volume': vols}, index=stamps)

    def test_same_time_ratio_excludes_forming_bar_and_future_volume(self):
        self.frame.loc[self.now.floor('15min'):, 'volume'] = 1e9
        result = same_time_volume(self.frame, volume_window(self.sessions, self.now))
        self.assertEqual(result['relative_volume'], 1.5)
        self.assertEqual(result['relative_volume_observed'], 450)
        self.assertEqual(result['relative_volume_baseline'], 300)
        self.assertEqual(result['relative_volume_sessions'], 20)

    def test_current_session_is_not_part_of_baseline(self):
        window = volume_window(self.sessions, self.now)
        self.assertTrue(all(a.date() < self.now.date() for a, _ in window[1]))

    def test_open_has_no_completed_interval(self):
        for time in ('09:30', '09:44', '09:45'):
            self.assertIsNone(volume_window(self.sessions, pd.Timestamp('2026-10-02 '+time, tz='America/New_York')))
        self.assertIsNotNone(volume_window(self.sessions, pd.Timestamp('2026-10-02 09:46', tz='America/New_York')))

    def test_missing_current_or_reference_last_bar_blocks(self):
        window = volume_window(self.sessions, self.now)
        for date in ('2026-10-02', '2026-10-01'):
            frame = self.frame.drop(pd.Timestamp(date+' 10:00', tz='America/New_York'))
            self.assertEqual(same_time_volume(frame, window)['relative_volume_status'], 'UNAVAILABLE')

    def test_short_history_zero_baseline_and_bad_numbers_block(self):
        self.assertIsNone(volume_window(self.sessions[-10:], self.now))
        window = volume_window(self.sessions, self.now)
        for value in (0, float('nan'), float('inf'), -1):
            frame = self.frame.copy();frame['volume'] = value
            self.assertEqual(same_time_volume(frame, window)['relative_volume_status'], 'UNAVAILABLE')

    def test_missing_interior_bar_blocks_current_and_reference_windows(self):
        window = volume_window(self.sessions, self.now)
        for date in ('2026-10-02', '2026-10-01'):
            with self.subTest(date=date):
                frame = self.frame.drop(pd.Timestamp(date+' 09:45', tz='America/New_York'))
                self.assertEqual(same_time_volume(frame, window)['relative_volume_status'], 'UNAVAILABLE')

    def test_off_grid_bar_cannot_replace_missing_interval(self):
        window = volume_window(self.sessions, self.now)
        frame = self.frame.drop(pd.Timestamp('2026-10-02 09:45', tz='America/New_York'))
        frame.loc[pd.Timestamp('2026-10-02 09:46', tz='America/New_York'), 'volume'] = 150
        self.assertEqual(same_time_volume(frame, window)['relative_volume_status'], 'UNAVAILABLE')

    def test_missing_volume_column_blocks_without_crashing(self):
        self.assertEqual(same_time_volume(self.frame.rename(columns={'volume':'close'}),
            volume_window(self.sessions, self.now))['relative_volume_status'], 'UNAVAILABLE')

    def test_explicit_zero_interval_is_valid_and_duplicates_do_not_inflate(self):
        window = volume_window(self.sessions, self.now)
        frame = self.frame.copy()
        frame.loc[pd.Timestamp('2026-10-02 09:45', tz='America/New_York'), 'volume'] = 0
        frame = pd.concat([frame, frame.iloc[[-1]]])
        result = same_time_volume(frame, window)
        self.assertEqual(result['relative_volume_status'], 'OK')
        self.assertEqual(result['relative_volume'], 1.0)

    def test_early_close_excluded_when_window_is_longer(self):
        self.sessions[-2].close = '2026-10-01 13:00'
        window = volume_window(self.sessions, pd.Timestamp('2026-10-02 14:16', tz='America/New_York'))
        self.assertNotIn(pd.Timestamp('2026-10-01').date(), [a.date() for a, _ in window[1]])

    def test_current_early_close_and_weekend_stop_at_session_close(self):
        self.sessions[-1].close = '2026-10-02 13:00'
        for now in ('2026-10-02 14:16', '2026-10-03 12:00'):
            window = volume_window(self.sessions, pd.Timestamp(now, tz='America/New_York'))
            self.assertEqual(window[0][1].hour, 13)

    def test_dst_uses_local_session_time(self):
        days = pd.bdate_range('2026-10-01', '2026-11-03')
        sessions = [NS(open=f'{d.date()} 09:30', close=f'{d.date()} 16:00') for d in days]
        window = volume_window(sessions, pd.Timestamp('2026-11-03 10:16', tz='America/New_York'))
        self.assertEqual(window[0][0].utcoffset().total_seconds(), -18000)
        self.assertTrue(any(a.utcoffset().total_seconds() == -14400 for a, _ in window[1]))
        self.assertTrue(all((b-a).total_seconds() == 2700 for a, b in window[1]))


class EntryTests(unittest.TestCase):
    def test_high_score_cannot_outweigh_liquidity(self):
        rules = load_rules()
        weak = metric(avg_dollar_volume=2450)
        self.assertEqual(rules['entry_score'](weak)[0], 8)
        self.assertFalse(rules['entry_eligible'](weak))
        self.assertTrue(rules['entry_eligible'](metric(avg_dollar_volume=20_000_000)))
        self.assertFalse(rules['entry_eligible'](metric(avg_dollar_volume=float('nan'))))

    def test_unavailable_volume_blocks_entries_but_low_valid_ratio_is_scored(self):
        rules = load_rules()
        self.assertFalse(rules['entry_eligible'](metric(relative_volume_status='UNAVAILABLE')))
        self.assertTrue(rules['entry_eligible'](metric(relative_volume=.5, relative_volume_status='OK')))

    def test_shadow_both_directions_obey_gates(self):
        regime = dict(name='CHOP / MIXED', risk='NORMAL', long_gate=True, short_gate=True)
        for side in ('LONG', 'SHORT'):
            self.assertIn('LIQUIDITY', directional_decision(metric(avg_dollar_volume=100), regime, side)['decision'])
            self.assertIn('UNAVAILABLE', directional_decision(metric(relative_volume_status='UNAVAILABLE'), regime, side)['decision'])

    def test_replay_does_not_buy_illiquid_high_score(self):
        e = PortfolioReplay()
        e.step(datetime.fromisoformat('2026-10-02T09:00:00-07:00'),
               {'ABC': dict(price=100, metrics=metric(avg_dollar_volume=2450), regime='BULLISH TREND')})
        self.assertFalse(e.pending)

    def test_order_paths_reject_gate_failure_before_any_sell_or_buy(self):
        from test_scout_safety import ns
        trading = NS(submit_order=Mock())
        bad = dict(symbol='AMOD', entry_score=8, **metric(avg_dollar_volume=2450))
        rows = ns['submit_buys_if_allowed'](trading, [bad], NS(equity=100000, cash=60000), True, set(), {})
        self.assertIn('QUALITY', rows[0]['order_status'])
        result = ns['execute_upgrade_rotation_if_allowed'](trading,
            dict(candidate=bad, replace_symbol='OLD', upgrade_confirmed=True), True, {})
        self.assertIn('QUALITY', result['status'])
        trading.submit_order.assert_not_called()


class VolumeIntegrationTests(unittest.TestCase):
    def test_runner_attaches_volume_and_reuses_same_cycle_cache(self):
        import scout_runner as runner
        case = VolumeTests();case.setUp()
        window = volume_window(case.sessions, case.now)
        daily = pd.DataFrame({'close':[100]}, index=pd.DatetimeIndex(['2026-10-02'], tz='UTC'))
        daily.index = pd.MultiIndex.from_product([['ABC'], daily.index], names=['symbol','timestamp'])
        intra = case.frame.copy()
        intra.index = pd.MultiIndex.from_product([['ABC'], intra.index], names=['symbol','timestamp'])
        client = NS(_scout_volume_window=window, _scout_volume_cache={},
                    get_stock_bars=Mock(side_effect=[NS(df=daily),NS(df=intra),NS(df=daily)]))
        first = runner.bars_frame(client,['ABC'],with_volume=True)
        second = runner.bars_frame(client,['ABC'],with_volume=True)
        self.assertEqual(first['ABC'].attrs['scout_volume']['relative_volume'],1.5)
        self.assertEqual(first['ABC'].attrs,second['ABC'].attrs)
        self.assertEqual(client.get_stock_bars.call_count,3)
        request = client.get_stock_bars.call_args_list[1].args[0]
        self.assertEqual(str(request.timeframe),'15Min')
        self.assertEqual(str(request.feed.value),'iex')

    def test_intraday_failure_preserves_daily_position_data(self):
        import scout_runner as runner
        case = VolumeTests();case.setUp()
        daily = pd.DataFrame({'close':[100]}, index=pd.DatetimeIndex(['2026-10-02'],tz='UTC'))
        client = NS(_scout_volume_window=volume_window(case.sessions,case.now),_scout_volume_cache={},
                    get_stock_bars=Mock(side_effect=[NS(df=daily),RuntimeError('offline')]))
        frames = runner.bars_frame(client,['ABC'],with_volume=True)
        self.assertEqual(frames['ABC']['close'].iloc[0],100)
        self.assertEqual(frames['ABC'].attrs['scout_volume']['relative_volume_status'],'UNAVAILABLE')
