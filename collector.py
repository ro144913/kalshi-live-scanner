"""Scheduled, public-data-only Kalshi snapshot collector.

Runs once per GitHub Actions invocation. No account keys, orders, or promises
of precise execution times. Forecasts use Coinbase proxy data, not settlement index.
"""
import os
import time
from datetime import datetime, timedelta, timezone

import requests
from model import empirical_forecast, price_limit, classify

BASE = 'https://external-api.kalshi.com/trade-api/v2'
SERIES = {'BTC': 'KXBTC15M', 'ETH': 'KXETH15M', 'SOL': 'KXSOL15M', 'XRP': 'KXXRP15M'}
PRODUCTS = {s: f'{s}-USD' for s in SERIES}
SESSION = requests.Session()
SESSION.headers.update({'User-Agent': 'kalshi-research-collector/1.0'})


def now_utc():
    return datetime.now(timezone.utc)


def get_json(url, params=None):
    response = SESSION.get(url, params=params, timeout=20)
    response.raise_for_status()
    return response.json()


def db_headers():
    url = os.environ.get('SUPABASE_URL', '').rstrip('/')
    key = os.environ.get('SUPABASE_KEY', '')
    if not url.startswith('https://') or not key:
        raise RuntimeError('SUPABASE_URL or SUPABASE_KEY is missing from GitHub Actions secrets')
    return url + '/rest/v1/scanner_snapshots', {
        'apikey': key, 'Authorization': 'Bearer ' + key,
        'Content-Type': 'application/json',
    }


def db_get(params):
    url, headers = db_headers()
    response = SESSION.get(url, headers=headers, params=params, timeout=25)
    response.raise_for_status()
    return response.json()


def db_insert(row):
    url, headers = db_headers()
    headers['Prefer'] = 'resolution=ignore-duplicates,return=minimal'
    response = SESSION.post(url, headers=headers, json=[row], timeout=25)
    response.raise_for_status()


def db_update_result(ticker, result):
    url, headers = db_headers()
    headers['Prefer'] = 'return=minimal'
    response = SESSION.patch(url, headers=headers,
                             params={'ticker': 'eq.' + ticker, 'actual_result': 'eq.PENDING'},
                             json={'actual_result': result}, timeout=25)
    response.raise_for_status()


def numeric_quote(m, key):
    try:
        if m.get(key + '_dollars') is not None:
            return float(m[key + '_dollars'])
        if m.get(key) is not None:
            return float(m[key]) / 100
    except (TypeError, ValueError):
        return None
    return None


def strike(m):
    for key in ('floor_strike', 'strike_price'):
        try:
            if m.get(key) is not None:
                return float(m[key])
        except (TypeError, ValueError):
            pass
    return None


def remaining(m):
    try:
        raw = m.get('close_time') or m.get('expiration_time')
        return (datetime.fromisoformat(raw.replace('Z', '+00:00')) - now_utc()).total_seconds()
    except (ValueError, AttributeError, TypeError):
        return -1


def historical_forecast(ticker):
    # Use earliest durable observation, including those from the Streamlit app,
    # so each contract keeps its first direction across worker restarts.
    rows = db_get({'select': 'payload', 'ticker': 'eq.' + ticker,
                   'order': 'observed_utc.asc', 'limit': '1'})
    if not rows:
        return None
    first = rows[0].get('payload') or {}
    p = first.get('model_up_probability')
    direction = first.get('direction')
    try:
        p = float(p)
    except (ValueError, TypeError):
        return None
    if direction not in ('UP', 'DOWN') or not 0 <= p <= 1:
        return None
    return {'direction': direction, 'p_up': p}


def collect_one(symbol):
    markets = get_json(BASE + '/markets', {'series_ticker': SERIES[symbol], 'status': 'open', 'limit': 100}).get('markets', [])
    markets = [m for m in markets if remaining(m) > 0]
    if not markets:
        print(symbol, 'no open market')
        return
    market = min(markets, key=remaining)
    ticker = market['ticker']
    seconds = remaining(market)
    target = strike(market)
    if target is None or target <= 0:
        print(symbol, 'missing numeric strike; skipping')
        return

    product = PRODUCTS[symbol]
    quote = get_json(f'https://api.exchange.coinbase.com/products/{product}/ticker')
    spot = float(quote['price'])
    quote_time = datetime.fromisoformat(quote['time'].replace('Z', '+00:00'))
    quote_age = (now_utc() - quote_time).total_seconds()
    if not (0 <= quote_age <= 15):
        print(symbol, f'stale quote ({quote_age:.1f}s); skipping')
        return
    candles = get_json(f'https://api.exchange.coinbase.com/products/{product}/candles', {'granularity': 60})
    candles = sorted(candles, key=lambda r: r[0])[-300:]
    if not candles or not (0 <= now_utc().timestamp() - candles[-1][0] <= 180):
        print(symbol, 'stale candles; skipping')
        return

    frozen = historical_forecast(ticker)
    if frozen is None:
        forecast = empirical_forecast(candles, spot, target, seconds / 60)
        if not forecast.get('valid'):
            print(symbol, 'no valid historical forecast:', forecast.get('reason'))
            return
        p_up = float(forecast['p_up'])
        direction = 'UP' if p_up >= .5 else 'DOWN'
    else:
        p_up = frozen['p_up']
        direction = frozen['direction']

    up = numeric_quote(market, 'yes_ask')
    down = numeric_quote(market, 'no_ask')
    side_ask = up if direction == 'UP' else down
    side_prob = p_up if direction == 'UP' else 1 - p_up
    fee = float(os.environ.get('ESTIMATED_FEE_CENTS', '2'))
    margin = float(os.environ.get('SAFETY_MARGIN_CENTS', '5'))
    max_price = price_limit(side_prob, fee, margin)
    signal, reason = classify(direction, side_ask * 100 if side_ask is not None else None,
                              max_price, seconds, validated=False)
    now = now_utc()
    snapshot_id = f'{ticker}:{int(now.timestamp() // 60)}'
    edge = side_prob * 100 - side_ask * 100 - fee if side_ask is not None else None
    payload = {
        'snapshot_id': snapshot_id, 'observed_utc': now.isoformat(),
        'symbol': symbol, 'ticker': ticker, 'signal': signal, 'direction': direction,
        'model_up_probability': round(p_up, 5),
        'up_ask_cents': round(up * 100, 2) if up is not None else None,
        'down_ask_cents': round(down * 100, 2) if down is not None else None,
        'spot_coinbase': spot, 'coinbase_quote_age_seconds': round(quote_age, 2),
        'coinbase_quote_time_utc': quote_time.isoformat(),
        'kalshi_target': target, 'benchmark_verified': False,
        'seconds_remaining': round(seconds, 1),
        'model_edge_cents': round(edge, 2) if edge is not None else None,
        'actual_result': 'PENDING', 'source': 'github_actions',
        'reason': reason,
    }
    db_insert({'snapshot_id': snapshot_id, 'ticker': ticker,
               'observed_utc': now.isoformat(), 'payload': payload, 'actual_result': 'PENDING'})
    print(symbol, ticker, signal, direction, 'snapshot stored')


def settle_recent():
    # Scan recent pending rows only. Re-check older results in the dashboard if
    # settlement is delayed beyond this window. Pagination prevents truncation.
    cutoff = (now_utc() - timedelta(hours=72)).isoformat()
    tickers = set()
    offset = 0
    while offset < 6000:
        rows = db_get({'select': 'ticker,payload', 'actual_result': 'eq.PENDING',
                       'observed_utc': 'gte.' + cutoff,
                       'order': 'observed_utc.desc', 'limit': '1000', 'offset': str(offset)})
        if not rows:
            break
        for row in rows:
            payload = row.get('payload') or {}
            try:
                observed = datetime.fromisoformat(payload['observed_utc'].replace('Z', '+00:00'))
                seconds = float(payload['seconds_remaining'])
            except (KeyError, ValueError, TypeError, AttributeError):
                continue
            if observed + timedelta(seconds=seconds) + timedelta(minutes=2) < now_utc():
                tickers.add(row['ticker'])
        if len(rows) < 1000:
            break
        offset += len(rows)
    settled = 0
    for ticker in sorted(tickers):
        try:
            market = get_json(BASE + '/markets/' + ticker).get('market', {})
            result = str(market.get('result') or '').lower()
            if result in ('yes', 'no'):
                db_update_result(ticker, 'UP' if result == 'yes' else 'DOWN')
                settled += 1
        except requests.RequestException as exc:
            print('Settlement lookup failed', ticker, str(exc)[:150])
    print('Finalized contracts updated:', settled)


def main():
    db_headers()  # fail early if configuration is missing
    failures = 0
    for symbol in SERIES:
        try:
            collect_one(symbol)
        except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
            failures += 1
            print(symbol, 'collection error:', str(exc)[:300])
    settle_recent()
    if failures == len(SERIES):
        raise RuntimeError('All four market collections failed')


if __name__ == '__main__':
    main()
