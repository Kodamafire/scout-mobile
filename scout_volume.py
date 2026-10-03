"""Same-time IEX volume from completed regular-session 15-minute bars.

No linear full-day extrapolation. Calendar sessions include early closes.
Missing/stale data are explicit and cannot qualify a new entry.
"""
import pandas as pd
from math import isfinite

NY = 'America/New_York'


def eastern(value):
    stamp = pd.Timestamp(value)
    return stamp.tz_localize(NY) if stamp.tzinfo is None else stamp.tz_convert(NY)


def volume_window(sessions, observed_at, lookback=20):
    now = eastern(observed_at)
    # Wait one minute for the most recently completed aggregation to arrive.
    ready = now - pd.Timedelta(minutes=1)
    calendar = sorted([(eastern(s.open), eastern(s.close)) for s in sessions])
    if any(op.date() == now.date() and op <= now and ready.floor('15min') <= op
           for op, cl in calendar):
        return None
    started = [(op, cl) for op, cl in calendar if op <= ready]
    if not started:
        return None
    op, cl = started[-1]
    cutoff = min(ready.floor('15min'), cl)
    if cutoff <= op:
        return None
    elapsed = cutoff - op
    # Compare identical elapsed regular-session windows; omit short sessions
    # that closed before this window could complete.
    prior = [(a, a + elapsed) for a, b in calendar
             if a.date() < op.date() and a + elapsed <= b][-lookback:]
    if len(prior) != lookback:
        return None
    return (op, cutoff), prior


def same_time_volume(frame, window):
    unavailable = dict(relative_volume=0.0, relative_volume_status='UNAVAILABLE',
                       relative_volume_method='same_time_20_sessions_iex')
    if frame is None or frame.empty or window is None or 'volume' not in frame:
        return unavailable
    current, prior = window
    df = frame.copy()
    df.index = pd.DatetimeIndex(df.index)
    if df.index.tz is None:
        return unavailable
    df.index = df.index.tz_convert(NY)
    df = df[~df.index.duplicated(keep='last')].sort_index()
    volume = pd.to_numeric(df['volume'], errors='coerce')
    totals = []
    for start, end in [current, *prior]:
        part = volume[(volume.index >= start) & (volume.index < end)]
        # Every completed interval must exist, including interior bars.
        # Sparse IEX activity is unknown, not an implicit zero-volume bar.
        expected = pd.date_range(start, end, freq='15min', inclusive='left')
        if (part.empty or not part.index.equals(expected)
                or not all(isfinite(v) for v in part) or (part < 0).any()):
            return unavailable
        totals.append(float(part.sum()))
    baseline = sum(totals[1:]) / len(prior)
    if baseline <= 0:
        return unavailable
    return dict(relative_volume=totals[0] / baseline, relative_volume_status='OK',
                relative_volume_method='same_time_20_sessions_iex',
                relative_volume_asof=current[1].isoformat(),
                relative_volume_sessions=len(prior),
                relative_volume_observed=totals[0], relative_volume_baseline=baseline)
