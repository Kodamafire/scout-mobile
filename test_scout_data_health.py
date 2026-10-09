import unittest
from datetime import datetime, timezone
from types import SimpleNamespace as NS
from unittest.mock import Mock
from scout_data_health import probe_feeds, scanner_summary, health_panel


class FeedTests(unittest.TestCase):
    def test_confirmed_sip_used_and_opra_probe_cannot_submit_orders(self):
        now = datetime(2026, 10, 9, 18, tzinfo=timezone.utc)
        data = Mock(get_stock_latest_quote=Mock(return_value={'SPY': NS(timestamp=now)}))
        trading = Mock(get_option_contracts=Mock(return_value=NS(option_contracts=[
            NS(symbol='QQQ261023C00500000', tradable=True, size='100', root_symbol='QQQ', open_interest='500')])))
        options = Mock(get_option_latest_quote=Mock(return_value={'QQQ261023C00500000': NS(timestamp=now)}))
        result = probe_feeds(data, trading, options, now)
        self.assertEqual(result['stock_feed'], 'sip')
        self.assertEqual(data._scout_feed.value, 'sip')
        self.assertEqual(result['opra']['access'], 'CONFIRMED')
        self.assertEqual(result['sip']['age_seconds'], 0)
        trading.submit_order.assert_not_called()

    def test_empty_or_failed_sip_not_mislabeled_and_private_errors_hidden(self):
        class Refused(Exception):
            status_code = 403
        now = datetime.now(timezone.utc)
        for value in ({}, Refused('SECRET REMOTE BODY')):
            data = NS(get_stock_latest_quote=Mock())
            if isinstance(value, Exception):
                data.get_stock_latest_quote.side_effect = value
            else:
                data.get_stock_latest_quote.return_value = value
            trading = NS(get_option_contracts=Mock(return_value=NS(option_contracts=[])))
            result = probe_feeds(data, trading, Mock(), now)
            self.assertEqual(result['stock_feed'], 'iex')
            self.assertEqual(result['opra']['access'], 'UNKNOWN')
            self.assertNotIn('SECRET', str(result))
            self.assertNotEqual(result['sip']['access'], 'CONFIRMED')

    def test_sip_used_for_daily_and_intraday_bars(self):
        import pandas as pd
        import scout_runner as runner
        from alpaca.data.enums import DataFeed
        from test_scout_entry_quality import VolumeTests
        from scout_volume import volume_window
        case = VolumeTests(); case.setUp()
        client = NS(_scout_feed=DataFeed.SIP, _scout_volume_cache={},
                    _scout_volume_window=volume_window(case.sessions, case.now),
                    get_stock_bars=Mock(side_effect=[NS(df=pd.DataFrame({'close': [100]})), NS(df=case.frame)]))
        runner.bars_frame(client, ['ABC'], with_volume=True)
        self.assertEqual([call.args[0].feed.value for call in client.get_stock_bars.call_args_list], ['sip', 'sip'])

    def test_rejection_counts_keep_distinct_candidates_and_escape_html(self):
        cfg = NS(min_price=2, max_price=200, min_avg_dollar_volume=20_000_000, entry_score_min=7)
        scan = {'status': 'COMPLETE', 'symbols': ['BAD', 'GOOD', 'MISSING'], 'rows': [
            {'symbol': 'BAD', 'entry_score': 6, 'metrics': {'close': 1, 'avg_dollar_volume': 1}, 'checks': {'liquid': False}},
            {'symbol': 'GOOD', 'entry_score': 8, 'metrics': {'close': 100, 'avg_dollar_volume': 30_000_000}},
            {'symbol': 'MISSING', 'status': 'DATA UNAVAILABLE'}]}
        summary = scanner_summary(scan, [{'symbol': 'GOOD'}], cfg)
        self.assertEqual(summary['rejected_candidates'], 2)
        self.assertEqual(summary['rejection_counts']['ENTRY SCORE BELOW MINIMUM'], 1)
        self.assertEqual(summary['failed_scoring_checks'], {'liquid': 1})
        html = health_panel({'scanner_summary': summary, 'data_health': {'stock_feed': '<script>'}})
        self.assertIn('&lt;SCRIPT&gt;', html)
        self.assertNotIn('<script>', html)


if __name__ == '__main__':
    unittest.main()
