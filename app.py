import math, re, statistics, csv, io, time
from datetime import datetime, timezone
import requests
import streamlit as st
from model import empirical_forecast, price_limit, classify
from analytics import first_contract_records, metrics, calibration

st.set_page_config(page_title='Kalshi 15m Research Scanner',page_icon='📊',layout='wide')
st.title('Kalshi 15-minute historical opportunity scanner — V8')
if 'signal_log' not in st.session_state: st.session_state.signal_log = {}
if 'outcome_cache' not in st.session_state: st.session_state.outcome_cache = {}
if 'contract_forecasts' not in st.session_state: st.session_state.contract_forecasts = {}
st.caption('Auto-refresh ~5 seconds • Manual decisions only • Public market data; no Kalshi account connection')
st.caption('Historical Coinbase analog model • Fixed direction per contract • Contract-level accuracy dashboard • Experimental WATCH')
BASE='https://external-api.kalshi.com/trade-api/v2'
SERIES={'BTC':'KXBTC15M','ETH':'KXETH15M','SOL':'KXSOL15M','XRP':'KXXRP15M'}
PRODUCT={'BTC':'BTC-USD','ETH':'ETH-USD','SOL':'SOL-USD','XRP':'XRP-USD'}

@st.cache_data(ttl=4)
def fetch(url, params=None):
    response=requests.get(url,params=params,timeout=12,headers={'User-Agent':'kalshi-research-scanner/2.0'})
    response.raise_for_status()
    return response.json()

def quote(m, key):
    try:
        if m.get(key+'_dollars') is not None:return float(m[key+'_dollars'])
        if m.get(key) is not None:return float(m[key])/100
    except (ValueError,TypeError):pass
    return None

def remaining(m):
    try:
        t=m.get('close_time') or m.get('expiration_time')
        return (datetime.fromisoformat(t.replace('Z','+00:00'))-datetime.now(timezone.utc)).total_seconds()
    except (TypeError,ValueError,AttributeError):return -1

def get_target(m):
    # Prefer the exchange-provided numeric strike. Never guess from ticker or title.
    for key in ('floor_strike','strike_price'):
        v=m.get(key)
        if v is not None:
            try:return float(v),key
            except (ValueError,TypeError):pass
    return None,None

def live_coinbase_quote(sym):
    # Coinbase Exchange ticker contains a last trade price and its own exchange timestamp.
    # A fresh ticker still does NOT prove equivalence with Kalshi's settlement index.
    data = fetch(f'https://api.exchange.coinbase.com/products/{PRODUCT[sym]}/ticker')
    px = float(data['price'])
    raw_time = data.get('time')
    if not raw_time:
        raise ValueError('Coinbase quote has no timestamp')
    quoted_at = datetime.fromisoformat(raw_time.replace('Z', '+00:00'))
    age = (datetime.now(timezone.utc) - quoted_at).total_seconds()
    if not (0 <= age <= 15):
        raise ValueError(f'Coinbase quote stale or future-dated ({age:.1f}s)')
    return px, age, quoted_at.isoformat()

def get_candles(sym):
    rows=fetch(f'https://api.exchange.coinbase.com/products/{PRODUCT[sym]}/candles',{'granularity':60})
    return sorted(rows,key=lambda r:r[0])[-300:]

def probability(spot,target,rows,seconds):
    closes=[float(r[4]) for r in rows if len(r)>4 and float(r[4])>0]
    if len(closes)<31 or spot<=0 or target<=0 or seconds<=0:return None,None
    returns=[math.log(b/a) for a,b in zip(closes[:-1],closes[1:])]
    vol=statistics.stdev(returns[-30:])
    if vol<=0:return None,None
    # Zero-drift lognormal approximation. No historical calibration or benchmark adjustment.
    z=math.log(spot/target)/(vol*math.sqrt(seconds/60))
    p=.5*(1+math.erf(z/math.sqrt(2)))
    return max(.001,min(.999,p)),vol

def cents(p):return 'N/A' if p is None else f'{p*100:.1f}¢'

symbols=st.multiselect('Markets',list(SERIES),default=list(SERIES))
fee_cents=st.number_input('Estimated all-in fees per contract (cents; verify with Kalshi)',min_value=0.0,max_value=20.0,value=2.0,step=0.5)
safety_cents=st.slider('Additional safety margin (cents)',0.0,20.0,5.0,0.5)
if st.button('Refresh now'):st.cache_data.clear()

# Optional durable storage: Supabase REST. Never place credentials in GitHub files.
def db_config():
    try:
        url = str(st.secrets.get('SUPABASE_URL', '')).rstrip('/')
        key = str(st.secrets.get('SUPABASE_KEY', ''))
        if url.startswith('https://') and key:
            return url, {'apikey': key, 'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'}
    except Exception:
        pass
    return None, None

def db_request(method, endpoint, payload=None, params=None, extra_headers=None):
    url, headers = db_config()
    if not url: return None
    if extra_headers: headers.update(extra_headers)
    response = requests.request(method, url + '/rest/v1/' + endpoint,
        headers=headers, json=payload, params=params, timeout=12)
    response.raise_for_status()
    if response.text.strip(): return response.json()
    return []

def persist_snapshot(record):
    # Stable primary key makes retries and Streamlit reruns idempotent.
    data = {'snapshot_id': record['snapshot_id'], 'ticker': record['ticker'],
            'observed_utc': record['observed_utc'], 'payload': record,
            'actual_result': record.get('actual_result', 'PENDING')}
    return db_request('POST', 'scanner_snapshots', [data],
        extra_headers={'Prefer': 'resolution=ignore-duplicates,return=minimal'})

def load_snapshots():
    rows = db_request('GET', 'scanner_snapshots',
        params={'select': 'snapshot_id,payload,actual_result', 'order': 'observed_utc.desc', 'limit': '5000'})
    if rows is None: return None
    result = []
    for row in rows:
        r = dict(row['payload'])
        r['actual_result'] = row['actual_result']
        result.append(r)
    return result

def persist_result(ticker, result):
    return db_request('PATCH', 'scanner_snapshots', {'actual_result': result},
        params={'ticker': 'eq.' + ticker}, extra_headers={'Prefer': 'return=minimal'})

@st.fragment(run_every='5s')
def live():
    for sym in symbols:
        try:
            markets=fetch(BASE+'/markets',{'series_ticker':SERIES[sym],'status':'open','limit':100}).get('markets',[])
            markets=[m for m in markets if remaining(m)>0]
            if not markets:
                st.info(f'{sym}: No open contract found');continue
            m=min(markets,key=remaining)
            seconds=remaining(m)
            up=quote(m,'yes_ask');down=quote(m,'no_ask')
            # Bid complements are estimates, not executable asks; never silently substitute.
            target,source=get_target(m)
            rows=get_candles(sym)
            spot, quote_age, quote_time = live_coinbase_quote(sym)
            candle_age = datetime.now(timezone.utc).timestamp() - float(rows[-1][0])
            if not (0 <= candle_age <= 180):
                raise ValueError(f'Coinbase candles stale or future-dated ({candle_age:.0f}s)')
            forecast = empirical_forecast(rows, spot, target, seconds/60) if target is not None else {'valid': False, 'reason': 'No verified numeric strike in market record.'}
            p = forecast.get('p_up') if forecast.get('valid') else None
            vol = None

            ticker = m.get('ticker', '')
            # Freeze initial direction and model probability for each contract.
            # This prevents intracontract flip-flopping. Quote/timing gates update live.
            frozen = st.session_state.contract_forecasts.get(ticker)
            if frozen is None and p is not None and ticker:
                frozen = {'direction': 'UP' if p >= .5 else 'DOWN', 'p_up': p,
                          'created_utc': datetime.now(timezone.utc).isoformat(),
                          'analogs': forecast['analogs'], 'effective_n': forecast['effective_n']}
                st.session_state.contract_forecasts[ticker] = frozen
            direction = frozen['direction'] if frozen else 'UNKNOWN'
            locked_p = frozen['p_up'] if frozen else None
            signal = 'WAIT'
            reason = forecast.get('reason', 'Insufficient history for a model forecast.')
            entry = None
            edge = None
            if frozen:
                side_price = up if direction == 'UP' else down
                side_prob = locked_p if direction == 'UP' else 1-locked_p
                entry = price_limit(side_prob, fee_cents, safety_cents)
                if side_price is not None:
                    edge = side_prob*100 - side_price*100 - fee_cents
                    signal, reason = classify(direction, side_price*100, entry, seconds, validated=False)
                else:
                    reason = 'No executable ask quote for preferred side.'
            if p is not None and frozen and ((p >= .5) != (direction == 'UP')):
                reason += ' Live proxy estimate changed direction; original contract forecast remains locked.'
            # One timestamped snapshot per contract per minute.
            # Session-only storage: Streamlit may restart and clear this history.
            now = datetime.now(timezone.utc)
            log_key = f'{ticker}:{int(now.timestamp() // 60)}'
            if ticker and log_key not in st.session_state.signal_log:
                st.session_state.signal_log[log_key] = {
                    'snapshot_id': log_key, 'observed_utc': now.isoformat(), 'symbol': sym, 'ticker': ticker,
                    'signal': signal, 'direction': direction,
                    'model_up_probability': round(locked_p, 5) if locked_p is not None else None,
                    'up_ask_cents': round(up*100, 2) if up is not None else None,
                    'down_ask_cents': round(down*100, 2) if down is not None else None,
                    'spot_coinbase': spot, 'coinbase_quote_age_seconds': round(quote_age, 2),
                    'coinbase_quote_time_utc': quote_time, 'kalshi_target': target,
                    'benchmark_verified': False,
                    'seconds_remaining': round(seconds, 1),
                    'model_edge_cents': round(edge, 2) if edge is not None else None,
                    'actual_result': 'PENDING'
                }
            if ticker and log_key in st.session_state.signal_log and db_config()[0]:
                if log_key not in st.session_state.setdefault('persisted_keys', set()):
                    try:
                        persist_snapshot(st.session_state.signal_log[log_key])
                        st.session_state.persisted_keys.add(log_key)
                    except Exception as err:
                        st.session_state['db_error'] = str(err)
            with st.container(border=True):
                st.subheader(f'{sym} — {signal}')
                st.caption(f"{m.get('title','')} • {m.get('ticker','')}")
                a,b,c=st.columns(3)
                a.metric('UP ask',cents(up));b.metric('DOWN ask',cents(down));c.metric('Time left',f'{max(0,seconds)//60:.0f}:{int(max(0,seconds)%60):02d}')
                st.write(f'Coinbase last trade: **${spot:,.4f}** (quote age **{quote_age:.1f}s**)')
                st.caption(f'Coinbase exchange timestamp: {quote_time}; candle age {candle_age:.0f}s')
                st.caption('Benchmark not verified • Model estimates are exploratory; not a confirmed trading edge.')
                st.write(f'Kalshi numeric target: **${target:,.4f}**' if target is not None else 'Kalshi numeric target: **Unavailable — no prediction**')
                st.write(f'**Locked contract forecast: {direction}**' if frozen else 'Locked contract forecast: unavailable (insufficient history)')
                st.write(f'Historical proxy UP probability at first observation: **{locked_p:.1%}**' if locked_p is not None else 'Historical proxy probability: unavailable')
                if frozen: st.caption(f"Based on {frozen['analogs']} historical analogs; effective weighted sample {frozen['effective_n']:.0f}. Not calibrated to Kalshi settlements.")
                st.write(f'Maximum model-derived entry: **{entry:.1f}¢**' if entry is not None else 'Maximum model-derived entry: N/A')
                if edge is not None:st.write(f'Estimated edge before safety margin: **{edge:+.1f}¢** (highly model-dependent)')
                st.info(reason)
                st.caption('WATCH is an experimental price screen, not a verified buy recommendation. Historical Coinbase analogs are NOT Kalshi settlement-aligned data.')
        except Exception as e:
            st.error(f'{sym}: Data unavailable ({e}). No signal.')
live()
def _csv_records(records):
    if not records: return ''
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(records[0].keys()))
    writer.writeheader()
    writer.writerows(records)
    return buf.getvalue()

st.divider()
st.subheader('Prediction tracking and outcomes')
if db_config()[0]:
    st.info('Supabase credentials detected. Cloud persistence is confirmed only after a successful database read/write.')
else:
    st.warning('Cloud persistence NOT configured: history is session-only. Follow SETUP.md to connect Supabase.')
if st.session_state.get('db_error'):
    st.error('Cloud save failed: ' + st.session_state['db_error'])
st.caption('Records one snapshot per contract per minute while Streamlit is actively running. An inactive or sleeping phone does not guarantee continuous collection. Cloud persistence saves collected observations; it does not run a background worker.')
rows_log = list(st.session_state.signal_log.values())
if db_config()[0]:
    try:
        cloud_rows = load_snapshots()
        if cloud_rows is not None:
            merged = {r['snapshot_id']: r for r in cloud_rows}
            merged.update({r['snapshot_id']: r for r in rows_log if r['snapshot_id'] not in merged})
            rows_log = sorted(merged.values(), key=lambda r: r['observed_utc'])
    except Exception as err:
        st.error('Cloud history could not be loaded: ' + str(err))
if rows_log:
    st.metric('Snapshots collected', len(rows_log))
    st.metric('Experimental WATCH snapshots', sum(r['signal'].startswith('WATCH') for r in rows_log))
    # Resolve only records whose contract has expired. Kalshi may not have finalized the result yet.
    if st.button('Check finalized outcomes'):
        tickers = sorted(set(r['ticker'] for r in rows_log if r['ticker']))
        for ticker in tickers:
            try:
                data = fetch(BASE + '/markets/' + ticker).get('market', {})
                result = str(data.get('result') or '').lower()
                if result in ('yes', 'no'):
                    st.session_state.outcome_cache[ticker] = 'UP' if result == 'yes' else 'DOWN'
                    if db_config()[0]:
                        try: persist_result(ticker, st.session_state.outcome_cache[ticker])
                        except Exception as err: st.error(f'Could not save {ticker} outcome: {err}')
            except Exception:
                pass
        for r in rows_log:
            if r['ticker'] in st.session_state.outcome_cache:
                r['actual_result'] = st.session_state.outcome_cache[r['ticker']]
        st.success('Checked available finalized results. Unsettled contracts remain PENDING.')
    # Each contract contributes at most one forecast to accuracy metrics.
    # Repeated per-minute observations must never inflate the win rate.
    contracts = first_contract_records(rows_log)
    st.subheader('V8 — Forecast accuracy by settled contract')
    st.caption('Only finalized YES/NO Kalshi results are scored. The forecast is taken from the earliest stored snapshot per ticker. Earlier V7 snapshots may not be settlement-benchmark aligned.')
    results = metrics(contracts, fee_cents)
    x, y, z = st.columns(3)
    x.metric('Settled contracts scored', results['settled'])
    y.metric('Direction accuracy', f"{results['accuracy']:.1%}" if results['accuracy'] is not None else 'N/A')
    z.metric('Brier score (lower is better)', f"{results['brier']:.3f}" if results['brier'] is not None else 'N/A')
    if results['settled'] < 100:
        st.warning('Small evaluation sample: do not interpret this as proven predictive accuracy. At least 100 distinct settled contracts is a preliminary checkpoint, not a guarantee of reliability.')
    if not any(r['benchmark_verified'] for r in contracts):
        st.warning('Settlement benchmark alignment remains unverified. Kalshi outcomes can be recorded, but Coinbase-based model probabilities are not independently calibrated.')
    st.subheader('Results by cryptocurrency')
    by_symbol = []
    for sym in symbols:
        group = [r for r in contracts if r['symbol'] == sym]
        stats = metrics(group, fee_cents)
        by_symbol.append({'Symbol': sym, 'Settled contracts': stats['settled'],
                          'Correct forecasts': stats['correct'],
                          'Accuracy': f"{stats['accuracy']:.1%}" if stats['accuracy'] is not None else 'N/A',
                          'Brier score': round(stats['brier'], 3) if stats['brier'] is not None else None})
    st.dataframe(by_symbol, hide_index=True, use_container_width=True)
    st.subheader('Probability calibration')
    calibration_rows = calibration(contracts)
    if calibration_rows:
        st.dataframe(calibration_rows, hide_index=True, use_container_width=True)
        st.caption('Compare average predicted UP probability with actual UP frequency. Sparse bands are highly uncertain.')
    else:
        st.info('Calibration requires finalized contracts with stored probabilities.')
    st.subheader('Hypothetical WATCH-entry outcomes')
    st.caption('Uses the first observed WATCH ask for each settled contract and the fee estimate currently selected above. This is a hypothetical quote-based calculation, NOT executed trades, fill prices, or realized P/L. Slippage and order-book depth are not included.')
    st.metric('Settled contracts with WATCH quotes', results['hypothetical_n'])
    st.metric('Hypothetical net (cents, one contract per WATCH)', f"{results['hypothetical_net_cents']:+.1f}¢" if results['hypothetical_net_cents'] is not None else 'N/A')
    st.subheader('Contract-level forecast ledger')
    st.dataframe(contracts, hide_index=True, use_container_width=True)
    st.download_button('Download contract-level evaluation CSV', _csv_records(contracts), file_name='v8_contract_evaluation.csv', mime='text/csv')
    st.dataframe(rows_log, use_container_width=True, hide_index=True)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows_log[0]))
    writer.writeheader()
    writer.writerows(rows_log)
    st.download_button('Download prediction history CSV', buf.getvalue(), file_name='kalshi_prediction_history.csv', mime='text/csv')
else:
    st.info('No snapshots yet. Keep the scanner open to collect observations.')
st.divider()
st.caption('V8 limitation: Coinbase analog forecasts are proxies; no verified CF Benchmarks input, historical Kalshi quote replay, calibrated win probability, or demonstrated net-positive backtest. No account access or automatic execution.')
