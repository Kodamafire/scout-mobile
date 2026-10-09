"""Read-only feed probes and scanner reporting; no order or risk-policy changes."""
from collections import Counter
from datetime import timedelta
from html import escape


def quote_result(quotes, symbol, now):
    quote = quotes.get(symbol) if quotes else None
    if quote is None:
        return {'access': 'UNKNOWN', 'quote_at': None, 'age_seconds': None}
    at = quote.timestamp
    age = (now - at).total_seconds() if at.tzinfo is not None else None
    return {'access': 'CONFIRMED', 'quote_at': at.isoformat(),
            'age_seconds': round(age, 3) if age is not None else None}


def failed_probe(exc):
    status = getattr(exc, 'status_code', None)
    return {'access': 'DENIED' if status in (401, 403) else 'UNKNOWN',
            'error': type(exc).__name__,
            'http': status if type(status) is int and 100 <= status <= 599 else None}


def probe_feeds(data, trading, options, now):
    """REST authorization only. Neither streams nor local Options service are verified."""
    from alpaca.data.enums import DataFeed, OptionsFeed
    from alpaca.data.requests import StockLatestQuoteRequest, OptionLatestQuoteRequest
    from alpaca.trading.requests import GetOptionContractsRequest
    from alpaca.trading.enums import AssetStatus
    from zoneinfo import ZoneInfo
    result = {'checked_at': now.isoformat(), 'stock_feed': 'iex',
              'scope': 'REST access only; not streaming or Scout Options worker readiness'}
    try:
        quotes = data.get_stock_latest_quote(StockLatestQuoteRequest(
            symbol_or_symbols=['SPY'], feed=DataFeed.SIP))
        result['sip'] = quote_result(quotes, 'SPY', now)
    except Exception as exc:
        result['sip'] = failed_probe(exc)
    # Use one feed consistently for daily and same-time volume history.
    if result['sip']['access'] == 'CONFIRMED':
        result['stock_feed'] = 'sip'
    try:
        day = now.astimezone(ZoneInfo('America/New_York')).date()
        response = trading.get_option_contracts(GetOptionContractsRequest(
            underlying_symbols=['QQQ'], status=AssetStatus.ACTIVE,
            expiration_date_gte=day + timedelta(days=7),
            expiration_date_lte=day + timedelta(days=30), limit=50))
        choices = [c for c in response.option_contracts
                   if c.tradable and int(c.size) == 100 and c.root_symbol == 'QQQ']
        if not choices:
            result['opra'] = {'access': 'UNKNOWN', 'reason': 'No standard contract returned'}
        else:
            symbol = max(choices, key=lambda c: int(c.open_interest or 0)).symbol
            quotes = options.get_option_latest_quote(OptionLatestQuoteRequest(
                symbol_or_symbols=[symbol], feed=OptionsFeed.OPRA))
            result['opra'] = quote_result(quotes, symbol, now)
    except Exception as exc:
        result['opra'] = failed_probe(exc)
    data._scout_feed = DataFeed(result['stock_feed'])
    return result


def scanner_summary(scan, candidates, config):
    reasons = Counter()
    rejected = []
    qualified_symbols = {c['symbol'] for c in candidates}
    for row in scan.get('rows', []):
        failures = []
        metrics = row.get('metrics')
        if metrics is None:
            failures.append('DATA UNAVAILABLE')
        else:
            price = metrics.get('close', 0)
            if not config.min_price <= price <= config.max_price:
                failures.append('PRICE OUTSIDE RANGE')
            if not metrics.get('avg_dollar_volume', 0) >= config.min_avg_dollar_volume:
                failures.append('LIQUIDITY BELOW MINIMUM')
            if metrics.get('relative_volume_status', 'OK') != 'OK':
                failures.append('SAME-TIME VOLUME UNAVAILABLE')
            if row.get('entry_score', 0) < config.entry_score_min:
                failures.append('ENTRY SCORE BELOW MINIMUM')
        if failures:
            rejected.append({'symbol': row['symbol'], 'reasons': failures})
            reasons.update(failures)
        elif row['symbol'] not in qualified_symbols:
            reasons['QUALIFIED OUTSIDE SHORTLIST'] += 1
    return {'status': scan.get('status', 'UNKNOWN'),
            'symbols_scanned': len(scan.get('symbols', [])),
            'qualified_shortlist': len(candidates), 'rejected_candidates': len(rejected),
            'rejection_counts': dict(reasons), 'rejected': rejected,
            'failed_scoring_checks': dict(Counter(
                k for row in scan.get('rows', []) for k, passed in row.get('checks', {}).items() if not passed))}


def health_panel(report):
    health = report.get('data_health', {})
    scan = report.get('scanner_summary', {})
    feeds = ''.join('<li>' + name.upper() + ': ' + escape(str(health.get(name, {}).get('access', 'UNKNOWN')))
                    + ' · quote time ' + escape(str(health.get(name, {}).get('quote_at') or 'unavailable')) + '</li>'
                    for name in ('sip', 'opra'))
    reasons = ''.join('<li>' + escape(reason) + ': ' + str(count) + '</li>'
                      for reason, count in sorted(scan.get('rejection_counts', {}).items()))
    rows = ''.join('<li><b>' + escape(r['symbol']) + '</b> · ' + escape('; '.join(r['reasons'])) + '</li>'
                   for r in scan.get('rejected', []))
    return ('<section class="panel"><h2>Data connections & scanner</h2><p>Stock analysis feed: '
            + escape(str(health.get('stock_feed', 'unverified')).upper())
            + '.</p><ul>' + feeds + '</ul><p class="sub">REST access checks only. This stock cycle does not verify '
            'the local Options worker or streaming quotes. Saved snapshot; see the update time above.</p><p>Scan: '
            + escape(str(scan.get('status', 'UNKNOWN'))) + ' · ' + str(scan.get('symbols_scanned', 0))
            + ' symbols · ' + str(scan.get('qualified_shortlist', 0)) + ' shortlisted · '
            + str(scan.get('rejected_candidates', 0)) + ' rejected.</p><ul>' + reasons
            + '</ul><details><summary>Candidate rejection reasons</summary><ul>' + rows
            + '</ul></details><p class="sub">A candidate may fail several rules, so reason counts overlap. '
            'Qualified candidates can still be blocked by cash, capacity, open orders or replacement requirements.</p></section>')
