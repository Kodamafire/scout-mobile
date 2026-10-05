"""One-second service + deterministic synthetic demo. No broker orders."""
import argparse
import asyncio
import json
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from .core import Config, Contract, PaperEngine, Quote, Signal


def write_status(path, engine, **extra):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(dict(engine.snapshot(), **extra), indent=2, allow_nan=False))
    temp.replace(path)


async def manage(engine, context, status_path, stop, status_extra=None):
    """Network scans run elsewhere; deadline scheduled with a monotonic clock."""
    deadline = time.monotonic()
    while not stop.is_set():
        now = datetime.now(timezone.utc)
        try:
            market_open, close_at, context_at = context()
            fresh = context_at is not None and 0 <= (now-context_at).total_seconds() <= 15
            engine.tick(now, market_open and fresh, close_at)
            write_status(status_path, engine, exchange_context_fresh=fresh,
                         **(status_extra() if status_extra else {}))
        except Exception as exc:
            # Do not log credentials or remote payloads.
            with engine.lock:
                engine.state['accepting'] = False
                engine._alert(now, 'Management error: '+type(exc).__name__)
                engine._save()
        deadline += engine.cfg.tick_seconds
        if deadline <= time.monotonic(): deadline = time.monotonic()+engine.cfg.tick_seconds
        try:
            await asyncio.wait_for(stop.wait(), timeout=max(0, deadline-time.monotonic()))
        except asyncio.TimeoutError:
            pass


async def demo(path, seconds, status):
    """Synthetic 1-second scenario; output is not performance evidence."""
    engine = PaperEngine(path, 10000, allow_synthetic=True)
    now = datetime.now(timezone.utc)
    close = now+timedelta(hours=2)
    contract = Contract('DEMO-CALL', 'DEMO', 'CALL', now.date()+timedelta(days=14), .5, .3, 500, now)
    try:
        for i in range(seconds):
            now = datetime.now(timezone.utc)
            # Rise then fall to exercise trailing and exit handling.
            ask = .9 if i < 3 else 1.25 if i < 6 else 1.05
            engine.on_quote(Quote(contract.symbol, ask-.02, ask, 1, 1, now, 'synthetic'), now)
            engine.tick(now, True, close)
            if i == 0:
                engine.enter([contract], Signal('DEMO', 'CALL', now, 'Synthetic lifecycle test'), now)
            write_status(status, engine, synthetic=True)
            await asyncio.sleep(1)
        print(json.dumps(engine.snapshot(), indent=2))
    finally:
        engine.close()


def main():
    parser = argparse.ArgumentParser(description='Scout options reference simulator; never submits broker orders.')
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--seconds', type=int, default=10)
    parser.add_argument('--ledger', default='.scout-options/ledger.sqlite')
    parser.add_argument('--status', default='.scout-options/status.json')
    parser.add_argument('--capital', type=float, help='Explicit isolated research capital for read-only feed mode')
    args = parser.parse_args()
    if args.demo:
        if args.seconds < 1: parser.error('--seconds must be positive')
        asyncio.run(demo(args.ledger, args.seconds, args.status))
    else:
        if args.capital is None: parser.error('--capital is required; this is a separate virtual portfolio')
        from .alpaca_observer import run
        asyncio.run(run(args.ledger, args.capital, args.status))


if __name__ == '__main__': main()
