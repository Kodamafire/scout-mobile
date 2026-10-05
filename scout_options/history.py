"""Resumable free IEX stock history collection. No orders or performance claims."""
import argparse
import json
import math
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SYMBOLS = ('SPY', 'QQQ', 'IWM')
UTC = timezone.utc


def aware(value):
    if value.tzinfo is None:
        raise ValueError('Timezone-aware timestamp required')
    return value.astimezone(UTC)


class HistoryStore:
    def __init__(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS bars (
                symbol TEXT, at TEXT, session TEXT, open REAL, high REAL,
                low REAL, close REAL, volume REAL, PRIMARY KEY(symbol, at));
            CREATE INDEX IF NOT EXISTS bars_session ON bars(session);
            CREATE TABLE IF NOT EXISTS coverage (
                session TEXT, symbol TEXT, expected INTEGER, received INTEGER,
                rejected INTEGER, checked_at TEXT, PRIMARY KEY(session, symbol));
        ''')

    def close(self):
        self.db.close()

    def done(self, day, retry_incomplete=False):
        rows = self.db.execute('SELECT expected,received,rejected FROM coverage WHERE session=?',
                               (day.isoformat(),)).fetchall()
        return len(rows) == len(SYMBOLS) and (not retry_incomplete or
                    all(expected == received and rejected == 0 for expected, received, rejected in rows))

    def save_session(self, day, opened, closed, data, now):
        opened, closed, now = aware(opened), aware(closed), aware(now)
        if closed > now or closed <= opened:
            raise ValueError('Only completed exchange sessions can be saved')
        expected = int((closed-opened).total_seconds()/60)
        prepared = []
        coverage = []
        for symbol in SYMBOLS:
            unique = {}
            rejected = 0
            for bar in data.get(symbol, []):
                try:
                    at = aware(bar.timestamp)
                    values = tuple(float(getattr(bar, k)) for k in ('open','high','low','close','volume'))
                    o, h, l, c, v = values
                    if (not opened <= at < closed or at+timedelta(minutes=1) > closed
                        or at.second or at.microsecond or not all(math.isfinite(x) for x in values)
                        or min(o,h,l,c) <= 0 or v < 0 or not l <= min(o,c) <= max(o,c) <= h):
                        raise ValueError('Invalid bar')
                    if at in unique:
                        raise ValueError('Duplicate bar')
                    unique[at] = (symbol,at.isoformat(),day.isoformat(),*values)
                except (ValueError,TypeError,AttributeError,OverflowError):
                    rejected += 1
            prepared.extend(unique.values())
            coverage.append((day.isoformat(),symbol,expected,len(unique),rejected,now.isoformat()))
        # A failed or interrupted download never creates a completed checkpoint.
        with self.db:
            self.db.execute('DELETE FROM bars WHERE session=?',(day.isoformat(),))
            self.db.executemany('INSERT INTO bars VALUES (?,?,?,?,?,?,?,?)',prepared)
            self.db.executemany('INSERT OR REPLACE INTO coverage VALUES (?,?,?,?,?,?)',coverage)

    def summary(self, start, end):
        params = (start.isoformat(),end.isoformat())
        rows = self.db.execute('''SELECT symbol, COUNT(*), SUM(received), SUM(expected-received),
                    SUM(rejected), SUM(CASE WHEN expected=received AND rejected=0 THEN 1 ELSE 0 END)
                    FROM coverage WHERE session>=? AND session<? GROUP BY symbol''',params).fetchall()
        limits = self.db.execute('SELECT MIN(at), MAX(at) FROM bars WHERE session>=? AND session<?',params).fetchone()
        return dict(dataset='IEX stock minute bars — not option prices or a profitability backtest',
                    adjustment='raw',start_inclusive=start.isoformat(),end_exclusive=end.isoformat(),
                    first_bar=limits[0],last_bar=limits[1],
                    symbols={s:dict(sessions_checked=n,bars_saved=b,missing_minutes=m,
                                   rejected_bars=r,full_minute_sessions=f) for s,n,b,m,r,f in rows})


def collect(store, stocks, trading, start, end, now, retry_incomplete=False, max_sessions=None):
    from alpaca.data.enums import DataFeed, Adjustment
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    from alpaca.trading.requests import GetCalendarRequest
    calendar = trading.get_calendar(GetCalendarRequest(start=start,end=end-timedelta(days=1)))
    count = 0
    for session in calendar:
        day = session.date
        if not start <= day < end or store.done(day,retry_incomplete):
            continue
        # Alpaca calendar open/close are naive exchange-local values in some SDKs.
        from zoneinfo import ZoneInfo
        eastern = ZoneInfo('America/New_York')
        opened, closed = session.open, session.close
        if opened.tzinfo is None: opened = opened.replace(tzinfo=eastern)
        if closed.tzinfo is None: closed = closed.replace(tzinfo=eastern)
        if aware(closed) > aware(now):
            continue
        bars = stocks.get_stock_bars(StockBarsRequest(symbol_or_symbols=list(SYMBOLS),
            timeframe=TimeFrame.Minute,start=opened,end=closed,feed=DataFeed.IEX,adjustment=Adjustment.RAW))
        store.save_session(day,opened,closed,bars.data,now)
        count += 1
        if count == 1 or count % 10 == 0:
            print(f'Saved exchange session {day}; {count} sessions downloaded this run.',flush=True)
        if max_sessions is not None and count >= max_sessions:
            break
    return count


def six_year_start(today):
    try:
        return today.replace(year=today.year-6)
    except ValueError:
        return today.replace(year=today.year-6,day=28)


def main():
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo('America/New_York')).date()
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--collect',action='store_true',help='Download using locally saved paper credentials')
    mode.add_argument('--replay',action='store_true',help='Evaluate historical stock signals; not option P&L')
    parser.add_argument('--start',type=date.fromisoformat,default=six_year_start(today))
    parser.add_argument('--end',type=date.fromisoformat,default=today,help='Exclusive end; today excluded by default')
    parser.add_argument('--database',default='.scout-options/stock-history.sqlite')
    parser.add_argument('--retry-incomplete',action='store_true')
    parser.add_argument('--max-sessions',type=int)
    args = parser.parse_args()
    if args.start >= args.end or (args.max_sessions is not None and args.max_sessions < 1):
        parser.error('Use start before end and a positive session limit')
    store = HistoryStore(args.database)
    try:
        if args.collect:
            from .local_runner import private_load
            from alpaca.data.historical import StockHistoricalDataClient
            from alpaca.trading.client import TradingClient
            key, secret = private_load()
            print('Downloading free IEX stock history. No option prices or brokerage orders.',flush=True)
            collect(store,StockHistoricalDataClient(key,secret),TradingClient(key,secret,paper=True),
                    args.start,args.end,datetime.now(UTC),args.retry_incomplete,args.max_sessions)
        print(json.dumps(store.summary(args.start,args.end),indent=2))
        if args.replay:
            from .replay import replay
            print(json.dumps(replay(store.db,args.start,args.end),indent=2))
    except KeyboardInterrupt:
        print('Download paused. Saved sessions will be reused on the next run.')
    except Exception as exc:
        print('History download unavailable: '+type(exc).__name__+'. Saved sessions remain; no remote payload is displayed.')
    finally:
        store.close()


if __name__ == '__main__':
    main()
