import math, re, statistics, csv, io, time
from datetime import datetime, timezone
import requests
import streamlit as st

st.set_page_config(page_title='Kalshi 15m Research Scanner',page_icon='📊',layout='wide')
st.title('Kalshi 15-minute research scanner')
if 'signal_log' not in st.session_state: st.session_state.signal_log = {}
if 'outcome_cache' not in st.session_state: st.session_state.outcome_cache = {}
st.caption('Auto-refresh ~5 seconds • Manual decisions only • Experimental, unvalidated model')
st.warning('Signals are experimental, not proven profitable. Coinbase spot is NOT necessarily Kalshi’s settlement benchmark. Never use this tool alone to justify larger bets.')
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
            spot=float(rows[-1][4])
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
            # One timestamped snapshot per contract per minute, plus any signal change.
            # Session-only storage: Streamlit may restart and clear this history.
            now = datetime.now(timezone.utc)
            ticker = m.get('ticker', '')
            log_key = f'{ticker}:{int(now.timestamp() // 60)}'
            if ticker and log_key not in st.session_state.signal_log:
                st.session_state.signal_log[log_key] = {
                    'observed_utc': now.isoformat(), 'symbol': sym, 'ticker': ticker,
                    'signal': signal, 'direction': direction,
                    'model_up_probability': round(p, 5) if p is not None else None,
                    'up_ask_cents': round(up*100, 2) if up is not None else None,
                    'down_ask_cents': round(down*100, 2) if down is not None else None,
                    'spot_coinbase': spot, 'kalshi_target': target,
                    'seconds_remaining': round(seconds, 1),
                    'model_edge_cents': round(edge, 2) if edge is not None else None,
                    'actual_result': 'PENDING'
                }
            with st.container(border=True):
                st.subheader(f'{sym} — {signal}')
                st.caption(f"{m.get('title','')} • {m.get('ticker','')}")
                a,b,c=st.columns(3)
                a.metric('UP ask',cents(up));b.metric('DOWN ask',cents(down));c.metric('Time left',f'{max(0,seconds)//60:.0f}:{int(max(0,seconds)%60):02d}')
                st.write(f'Coinbase spot: **${spot:,.4f}**')
                st.write(f'Kalshi numeric target: **${target:,.4f}**' if target is not None else 'Kalshi numeric target: **Unavailable — no prediction**')
                st.write(f'Experimental direction: **{direction}**')
                st.write(f'Uncalibrated UP probability: **{p:.1%}**' if p is not None else 'Uncalibrated UP probability: N/A')
                st.write(f'Maximum model-derived entry: **{entry:.1f}¢**' if entry is not None else 'Maximum model-derived entry: N/A')
                if edge is not None:st.write(f'Estimated edge before safety margin: **{edge:+.1f}¢** (highly model-dependent)')
                st.info(reason)
                st.caption('Not a trade instruction. Uses Coinbase spot rather than confirmed Kalshi settlement benchmark. Estimates can be materially wrong. No validated live performance or demonstrated profitable edge.')
        except Exception as e:
            st.error(f'{sym}: Data unavailable ({e}). No signal.')
live()
st.divider()
st.subheader('Prediction tracking (this browser session)')
st.caption('Captures at most one snapshot per contract per minute while this page is running. Reloads, app restarts, or sleeping iPhones may erase or interrupt tracking. Download the CSV regularly. This is not a permanent database.')
if st.session_state.signal_log:
    rows_log = list(st.session_state.signal_log.values())
    st.metric('Snapshots collected', len(rows_log))
    st.metric('Experimental entry signals', sum(r['signal'].startswith('EXPERIMENTAL') for r in rows_log))
    # Resolve only records whose contract has expired. Kalshi may not have finalized the result yet.
    if st.button('Check finalized outcomes'):
        tickers = sorted(set(r['ticker'] for r in rows_log if r['ticker']))
        for ticker in tickers:
            try:
                data = fetch(BASE + '/markets/' + ticker).get('market', {})
                result = str(data.get('result') or '').lower()
                if result in ('yes', 'no'):
                    st.session_state.outcome_cache[ticker] = 'UP' if result == 'yes' else 'DOWN'
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
st.caption('Next research requirements: confirm Kalshi benchmark and strike for every series, integrate executable order-book depth and actual fees, record timestamped signals, and backtest out of sample before any position-size increase.')
