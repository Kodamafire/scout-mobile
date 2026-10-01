import unittest
from datetime import timedelta
import pandas as pd
from backtest_profit_trial import frame, CUTOFF, observations
from unittest.mock import patch

class HistoricalIntegrityTests(unittest.TestCase):
    def test_incomplete_intraday_bar_is_excluded(self):
        ts=[int((CUTOFF-timedelta(minutes=30)).timestamp()),int((CUTOFF-timedelta(minutes=5)).timestamp())]
        raw={'chart':{'result':[{'timestamp':ts,'indicators':{'quote':[dict(close=[100,101],high=[100,101],low=[100,101],volume=[10,10])]}}]}}
        result=frame(raw,'15m')
        self.assertEqual(len(result),1)
        self.assertEqual(result.index[0].to_pydatetime(),CUTOFF-timedelta(minutes=15))
    def test_future_daily_prices_do_not_change_past_signals(self):
        daily=pd.DataFrame({'close':[100,10000]},index=pd.to_datetime(['2026-09-30T13:30Z','2026-10-01T13:30Z']))
        intra=pd.DataFrame({'close':[101]},index=pd.to_datetime(['2026-10-01T14:00Z']))
        seen=[]
        def capture(df,*args):
            seen.extend(df['close'].tolist())
            return None
        with patch('backtest_profit_trial.indicators',side_effect=capture):
            observations('ABC',intra,daily,daily)
        self.assertTrue(seen)
        self.assertNotIn(10000,seen)

if __name__=='__main__': unittest.main()
