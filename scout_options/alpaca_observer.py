"""Read-only Alpaca feed adapter. TradingClient is permanently paper=True.

Quotes drive a LOCAL reference simulator; no orders are sent to Alpaca.
"""
import asyncio
import os
import threading
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from alpaca.data.enums import DataFeed, OptionsFeed
from alpaca.data.historical import StockHistoricalDataClient, OptionHistoricalDataClient
from alpaca.data.live.option import OptionDataStream
from alpaca.data.requests import StockBarsRequest, OptionChainRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import QueryOrderStatus, AssetStatus
from alpaca.trading.requests import GetOrdersRequest, GetOptionContractsRequest
from .core import Config, Contract, PaperEngine, Quote, directional_signal
from .service import manage, write_status

EASTERN = ZoneInfo('America/New_York')
UNIVERSE = ('SPY', 'QQQ', 'IWM')


def enum_value(value): return getattr(value, 'value', value)


class Observer:
    def __init__(self, engine, key, secret, feed):
        self.engine = engine
        # No configurable endpoint or live-trading switch.
        self.trading = TradingClient(key, secret, paper=True)
        self.stocks = StockHistoricalDataClient(key, secret)
        self.options = OptionHistoricalDataClient(key, secret)
        self.stream = OptionDataStream(key, secret, feed=feed)
        self.feed = feed.value
        self.context = (False, datetime.now(timezone.utc), None)
        self.account = None
        self.contracts = {}
        self.subscribed = set()
        self.decisions = []

    def refresh_account(self):
        clock = self.trading.get_clock()
        account = self.trading.get_account()
        positions = self.trading.get_all_positions()
        orders = self.trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))
        now = datetime.now(timezone.utc)
        self.context = (bool(clock.is_open), clock.next_close, now)
        self.account = dict(at=now, equity=float(account.equity), cash=float(account.cash),
                            gross_exposure=sum(abs(float(p.market_value)) for p in positions),
                            pending_orders=bool(orders))

    async def quote_handler(self, q):
        self.engine.on_quote(Quote(q.symbol, float(q.bid_price), float(q.ask_price),
            int(q.bid_size), int(q.ask_size), q.timestamp, self.feed), datetime.now(timezone.utc))

    def discover(self):
        now = datetime.now(timezone.utc)
        day = now.astimezone(EASTERN).date()
        # Closed one-minute bars, restricted to today's regular session.
        bars = self.stocks.get_stock_bars(StockBarsRequest(symbol_or_symbols=list(UNIVERSE),
            timeframe=TimeFrame.Minute, start=datetime.combine(day, datetime.min.time(), EASTERN),
            end=now, feed=DataFeed.IEX))
        signals = []
        decisions = []
        for symbol in UNIVERSE:
            rows = [(b.timestamp, float(b.close), float(b.volume)) for b in bars.data.get(symbol, [])
                    if b.timestamp.astimezone(EASTERN).hour*60+b.timestamp.astimezone(EASTERN).minute >= 570
                    and b.timestamp+timedelta(minutes=1) <= now]
            signal = directional_signal(symbol, rows, now)
            if not signal:
                decisions.append(dict(underlying=symbol, stage='WAIT', reason='No completed-minute trend/volume signal.'))
                continue
            signals.append(signal)
            lower, upper = day+timedelta(days=self.engine.cfg.min_dte), day+timedelta(days=self.engine.cfg.max_dte)
            close = rows[-1][1]
            response = self.trading.get_option_contracts(GetOptionContractsRequest(
                underlying_symbols=[symbol], status=AssetStatus.ACTIVE,
                expiration_date_gte=lower, expiration_date_lte=upper,
                strike_price_gte=str(close*.90), strike_price_lte=str(close*1.10), limit=1000))
            snapshots = self.options.get_option_chain(OptionChainRequest(underlying_symbol=symbol,
                expiration_date_gte=lower, expiration_date_lte=upper,
                strike_price_gte=str(close*.90), strike_price_lte=str(close*1.10), feed=OptionsFeed(self.feed)))
            choices = []
            for c in response.option_contracts:
                snap = snapshots.get(c.symbol)
                greeks = getattr(snap, 'greeks', None)
                if (not snap or greeks is None or not c.open_interest_date
                    or not 0 <= (day-c.open_interest_date).days <= 7 or c.root_symbol != symbol):
                    continue
                contract = Contract(c.symbol, symbol, str(enum_value(c.type)).upper(), c.expiration_date,
                    float(greeks.delta), float(snap.implied_volatility or 0), int(c.open_interest or 0),
                    now, int(c.size), bool(c.tradable))
                if (contract.kind == signal.direction and contract.multiplier == 100 and contract.tradable
                    and .35 <= abs(contract.delta) <= .65 and contract.open_interest >= self.engine.cfg.min_open_interest):
                    choices.append(contract)
            choices.sort(key=lambda c: (abs(abs(c.delta)-.5), -c.open_interest, c.symbol))
            # Bounded stream subscriptions. This is a shortlist, not a full-market scan.
            for contract in choices[:3]:
                self.contracts[contract.symbol] = contract
                if contract.symbol not in self.subscribed and len(self.subscribed) < 50:
                    self.stream.subscribe_quotes(self.quote_handler, contract.symbol)
                    self.subscribed.add(contract.symbol)
            decisions.append(dict(underlying=symbol, stage=signal.direction, reason=signal.reason,
                                  shortlisted=min(3,len(choices)), truncated=bool(response.next_page_token)))
        self.decisions = decisions
        return signals

    def restore_subscriptions(self):
        # Recovered held options require fresh quotes; never reuse saved prices for exits.
        snapshot = self.engine.snapshot()
        symbols = set(snapshot['positions']) | {o['symbol'] for o in snapshot['orders'].values() if o['status'] == 'OPEN'}
        for symbol in symbols:
            self.stream.subscribe_quotes(self.quote_handler, symbol)
            self.subscribed.add(symbol)


async def run(path, capital, status_path):
    key, secret = os.environ.get('ALPACA_API_KEY'), os.environ.get('ALPACA_SECRET_KEY')
    if not key or not secret:
        raise RuntimeError('Existing Alpaca paper credentials must be available in the service environment.')
    feed = OptionsFeed(os.environ.get('SCOUT_OPTIONS_FEED', 'opra'))
    engine = PaperEngine(path, capital)
    observer = Observer(engine, key, secret, feed)
    stop = asyncio.Event()
    observer.restore_subscriptions()
    stream_thread = None

    def run_stream():
        try:
            observer.stream.run()
        except Exception as exc:
            with engine.lock:
                engine._alert(datetime.now(timezone.utc), 'Option stream failed: '+type(exc).__name__)
                engine._save()

    async def account_task():
        while not stop.is_set():
            try:
                await asyncio.to_thread(observer.refresh_account)
            except Exception as exc:
                with engine.lock:
                    engine._alert(datetime.now(timezone.utc), 'Account/clock read unavailable: '+type(exc).__name__)
                    engine._save()
            await asyncio.sleep(5)

    async def scan_task():
        nonlocal stream_thread
        while not stop.is_set():
            try:
                opened, close_at, observed = observer.context
                now = datetime.now(timezone.utc)
                if observed and opened and 0 <= (now-observed).total_seconds() <= 15:
                    signals = await asyncio.to_thread(observer.discover)
                    engine.update_signals(signals)
                    if observer.subscribed and stream_thread is None:
                        stream_thread = threading.Thread(target=run_stream, daemon=True)
                        stream_thread.start()
                    for signal in signals:
                        if observer.account is None:
                            continue
                        # Fresh streamed quote, not REST chain marks, is required to enter.
                        result = engine.enter(list(observer.contracts.values()), signal,
                                              datetime.now(timezone.utc), observer.account)
                        observer.decisions.append(dict(underlying=signal.underlying, stage=result))

            except Exception as exc:
                with engine.lock:
                    engine._alert(datetime.now(timezone.utc), 'Scanner unavailable: '+type(exc).__name__)
                    engine._save()
            await asyncio.sleep(engine.cfg.scan_seconds)

    tasks = [asyncio.create_task(account_task()), asyncio.create_task(scan_task()),
             asyncio.create_task(manage(engine, lambda: observer.context, status_path, stop,
                 lambda: dict(feed=observer.feed, scanner=observer.decisions, universe=list(UNIVERSE))))]
    try:
        await asyncio.gather(*tasks)
    finally:
        stop.set()
        for task in tasks: task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        observer.stream.stop()
        if stream_thread: stream_thread.join(timeout=3)
        engine.close()
