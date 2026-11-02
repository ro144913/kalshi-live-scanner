import math, re, statistics, csv, io, time
from datetime import datetime, timezone
import requests
import streamlit as st

st.set_page_config(page_title='Kalshi 15m Research Scanner',page_icon='📊',layout='wide')
st.title('Kalshi 15-minute research scanner — V5')
if 'signal_log' not in st.session_state: st.session_state.signal_log = {}
if 'outcome_cache' not in st.session_state: st.session_state.outcome_cache = {}
st.caption('Auto-refresh ~5 seconds • Manual decisions only • V5 benchmark safety gate')
st.warning('V5 SAFETY: UP/DOWN are research directions only. The scanner will show WAIT until the exact Kalshi settlement benchmark is integrated and verified. Coinbase spot is not an interchangeable settlement price.')
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
    return sorted(rows,key=lambda r:r[0])[-60:]

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
            p,vol=probability(spot,target,rows,seconds) if target is not None else (None,None)
            direction='UP' if p is not None and p>=.5 else 'DOWN' if p is not None else 'UNKNOWN'
            signal='WAIT';reason='No verified numeric target available from this market record.'
            entry=None;edge=None
            if p is not None:
                side_price=up if direction=='UP' else down
                side_prob=p if direction=='UP' else 1-p
                entry=max(0,side_prob*100-fee_cents-safety_cents)
                if side_price is None:reason='No available ask quote for preferred side.'
                elif not (0<side_price<1):reason='Quote is at an extreme or invalid.'
                else:
                    edge=side_prob*100-side_price*100-fee_cents
                    if seconds<120:reason='Under 2 minutes: settlement/latency risk; no entry.'
                    elif seconds>780:reason='Very early in window: no entry.'
                    elif side_price*100<=entry and vol is not None:
                        signal=f'EXPERIMENTAL {direction}'
                        reason='Model-implied price clears the fee and safety margin. NOT a validated positive edge.'
                    else:reason='Price does not clear estimated fee and safety margin.'
            # CRITICAL: Kalshi's in-app benchmark may not equal Coinbase spot.
            # A fresh quote and model-derived probability are insufficient to recommend a trade.
            # Do not emit actionable UP/DOWN until a verified settlement-aligned feed exists.
            if signal.startswith('EXPERIMENTAL'):
                reason = ('Research price screen passed, but the exact Kalshi settlement '
                          'benchmark is NOT verified against Coinbase. WAIT; no trade signal.')
            elif p is not None:
                reason = reason + ' Settlement benchmark NOT verified; WAIT.'
            signal = 'WAIT'
            # One timestamped snapshot per contract per minute.
            # Session-only storage: Streamlit may restart and clear this history.
            now = datetime.now(timezone.utc)
            ticker = m.get('ticker', '')
            log_key = f'{ticker}:{int(now.timestamp() // 60)}'
            if ticker and log_key not in st.session_state.signal_log:
                st.session_state.signal_log[log_key] = {
                    'snapshot_id': log_key, 'observed_utc': now.isoformat(), 'symbol': sym, 'ticker': ticker,
                    'signal': signal, 'direction': direction,
                    'model_up_probability': round(p, 5) if p is not None else None,
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
                st.error('Settlement benchmark: NOT VERIFIED. This is a research estimate, not an executable recommendation.')
                st.write(f'Kalshi numeric target: **${target:,.4f}**' if target is not None else 'Kalshi numeric target: **Unavailable — no prediction**')
                st.write(f'Experimental direction: **{direction}**')
                st.write(f'Uncalibrated UP probability: **{p:.1%}**' if p is not None else 'Uncalibrated UP probability: N/A')
                st.write(f'Maximum model-derived entry: **{entry:.1f}¢**' if entry is not None else 'Maximum model-derived entry: N/A')
                if edge is not None:st.write(f'Estimated edge before safety margin: **{edge:+.1f}¢** (highly model-dependent)')
                st.info(reason)
                st.caption('V5 blocks all entry signals until a confirmed Kalshi settlement-aligned price feed is available. No validated performance or demonstrated profitable edge.')
        except Exception as e:
            st.error(f'{sym}: Data unavailable ({e}). No signal.')
live()
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
    st.metric('Entry signals (benchmark safety gate)', sum(r['signal'].startswith('EXPERIMENTAL') for r in rows_log))
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
    finalized = [r for r in rows_log if r['actual_result'] in ('UP','DOWN') and r['direction'] in ('UP','DOWN')]
    if finalized:
        correct = sum(r['direction'] == r['actual_result'] for r in finalized)
        st.write(f'**Directional agreement across snapshots:** {correct}/{len(finalized)} ({correct/len(finalized):.1%}). Repeated snapshots of one contract are NOT independent predictions.')
        unique = {}
        for r in finalized:
            unique.setdefault(r['ticker'], r)
        if unique:
            n = len(unique)
            st.write(f'**First-snapshot accuracy by distinct settled contract:** {sum(r["direction"] == r["actual_result"] for r in unique.values())}/{n}. This does not measure trading profitability.')
    st.dataframe(rows_log, use_container_width=True, hide_index=True)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(rows_log[0]))
    writer.writeheader()
    writer.writerows(rows_log)
    st.download_button('Download prediction history CSV', buf.getvalue(), file_name='kalshi_prediction_history.csv', mime='text/csv')
else:
    st.info('No snapshots yet. Keep the scanner open to collect observations.')
st.divider()
st.caption('Next: obtain and verify the exact Kalshi settlement-aligned index/price feed, then test source divergence, latency, fees, depth, and out-of-sample performance. Do not scale position sizes on unvalidated signals.')
