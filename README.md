import math
import requests
import streamlit as st
from datetime import datetime, timezone

BASE = 'https://external-api.kalshi.com/trade-api/v2'
SERIES = {'BTC':'KXBTC15M','ETH':'KXETH15M','SOL':'KXSOL15M','XRP':'KXXRP15M'}

st.set_page_config(page_title='Kalshi 15m Scanner', page_icon='📊', layout='wide')
st.title('Kalshi 15-minute live scanner')
st.caption('Public market data • Manual execution • Screening only, not a validated prediction model')

@st.cache_data(ttl=8)
def get(path, params=None):
    r=requests.get(BASE+path,params=params,timeout=12)
    r.raise_for_status()
    return r.json()

def number(m, key):
    if m.get(key+'_dollars') is not None:
        return float(m[key+'_dollars'])
    if m.get(key) is not None:
        return float(m[key])/100
    return None

def evaluate(m):
    yb=number(m,'yes_bid'); ya=number(m,'yes_ask')
    nb=number(m,'no_bid'); na=number(m,'no_ask')
    if ya is None and nb is not None: ya=1-nb
    if na is None and yb is not None: na=1-yb
    close=m.get('close_time') or m.get('expiration_time')
    remain=None
    if close:
        try: remain=(datetime.fromisoformat(close.replace('Z','+00:00'))-datetime.now(timezone.utc)).total_seconds()/60
        except ValueError: pass
    if ya is None or na is None or remain is None: return 'WAIT','Missing quote or closing time',ya,na,remain
    if remain<=0: return 'CLOSED','Contract closing/closed',ya,na,remain
    if remain<2: return 'WAIT','Under two minutes left',ya,na,remain
    if ya>=.85 and na>=.85: return 'WAIT','Expensive quotes on both sides',ya,na,remain
    return 'WAIT','Live quotes available; no validated predictive edge',ya,na,remain

symbols=st.multiselect('Coins',list(SERIES),default=list(SERIES))
if st.button('Refresh quotes'): st.cache_data.clear()
st.info('Use Refresh quotes for new data. Kalshi markets and series identifiers can change. Verify the contract wording and settlement rule in Kalshi before trading.')
for sym in symbols:
    try:
        data=get('/markets',{'series_ticker':SERIES[sym],'status':'open','limit':100})
        markets=data.get('markets',[])
        if not markets:
            st.warning(f'{sym}: No open markets returned for {SERIES[sym]}. Check series identifier on Kalshi.')
            continue
        markets.sort(key=lambda m:m.get('close_time',''))
        m=markets[0]
        signal,why,ya,na,remaining=evaluate(m)
        with st.container(border=True):
            st.subheader(f'{sym} — {signal}')
            st.write(m.get('title',''), ' | ', m.get('ticker',''))
            a,b,c=st.columns(3)
            a.metric('UP ask',f'{ya*100:.1f}¢' if ya is not None else 'N/A')
            b.metric('DOWN ask',f'{na*100:.1f}¢' if na is not None else 'N/A')
            c.metric('Minutes remaining',f'{remaining:.1f}' if remaining is not None else 'N/A')
            st.caption(why)
            st.write('Market status:',m.get('status','unknown'))
    except Exception as exc:
        st.error(f'{sym}: Data unavailable: {exc}')

st.divider()
st.markdown('**Next development step:** Add the contract-specific reference-price feed, historical volatility, settlement-window model, fee schedule, and backtesting. Only then calculate calibrated UP/DOWN probabilities and actionable estimated edge.')
