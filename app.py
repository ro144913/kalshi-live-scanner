import math
from datetime import datetime, timezone
import requests
import streamlit as st

st.set_page_config(page_title='Kalshi Signal Scanner',page_icon='📈',layout='wide')
st.title('Kalshi 15-minute signal scanner')
st.caption('EXPERIMENTAL direction screen • Not validated or a guaranteed edge • Manual trades only')
BASE='https://external-api.kalshi.com/trade-api/v2'
SERIES={'BTC':'KXBTC15M','ETH':'KXETH15M','SOL':'KXSOL15M','XRP':'KXXRP15M'}
COINBASE={'BTC':'BTC-USD','ETH':'ETH-USD','SOL':'SOL-USD','XRP':'XRP-USD'}
@st.cache_data(ttl=10)
def fetch(url,params=None):
 r=requests.get(url,params=params,timeout=12,headers={'User-Agent':'kalshi-15m-research/1.0'});r.raise_for_status();return r.json()
def quote(m,key):
 k=key+'_dollars'
 if m.get(k) is not None:return float(m[k])
 if m.get(key) is not None:return float(m[key])/100
 return None
def fmt(x):return 'N/A' if x is None else f'{x*100:.1f}¢'
def minutes(m):
 try:return (datetime.fromisoformat((m.get('close_time') or m['expiration_time']).replace('Z','+00:00'))-datetime.now(timezone.utc)).total_seconds()/60
 except (ValueError,KeyError,TypeError):return None

def candles(symbol):
 # Coinbase public spot candles: [time,low,high,open,close,volume].
 rows=fetch(f'https://api.exchange.coinbase.com/products/{COINBASE[symbol]}/candles',{'granularity':60})
 rows=sorted(rows,key=lambda x:x[0]);return rows[-35:]

def screen(m,rows,up,down,mins):
 if mins is None or mins<=0:return 'WAIT','Market is closed or time is unavailable',None
 if up is None or down is None or not (0<up<1 and 0<down<1):return 'WAIT','No executable two-sided quote',None
 if len(rows)<22:return 'WAIT','Not enough price history',None
 closes=[float(r[4]) for r in rows]
 returns=[math.log(closes[i]/closes[i-1]) for i in range(1,len(closes)) if closes[i]>0 and closes[i-1]>0]
 if len(returns)<20:return 'WAIT','Insufficient clean returns',None
 momentum=math.log(closes[-1]/closes[-6])
 avg=sum(returns[-20:])/20
 sigma=math.sqrt(sum((r-avg)**2 for r in returns[-20:])/19)
 strength=abs(momentum)/(sigma*math.sqrt(5)) if sigma>0 else 0
 # Momentum is NOT a calibrated win probability. No target/reference feed is integrated.
 if mins<2:return 'WAIT','Less than 2 minutes left; high settlement risk',strength
 if mins>13:return 'WAIT','Early in window; no strong price evidence',strength
 if strength<1.5:return 'WAIT','Recent price momentum is weak relative to noise',strength
 direction='UP' if momentum>0 else 'DOWN'
 cost=up if direction=='UP' else down
 if cost>.65:return 'WAIT',f'{direction} momentum, but contract costs {cost*100:.0f}¢; price too high for experimental screen',strength
 if cost<.12:return 'WAIT',f'{direction} contract is very cheap; likely conflicts with the market target',strength
 return direction,'Experimental momentum signal ONLY. Target distance and settlement benchmark are not verified; do not treat this as positive expected value.',strength

st.warning('IMPORTANT: UP/DOWN below is a research momentum indicator, NOT a calibrated prediction or a recommended real-money bet. The model does not yet know the contract target price or exact settlement benchmark. Paper-test before trading.')
symbols=st.multiselect('Coins',list(SERIES),default=list(SERIES))
if st.button('Refresh live quotes'):st.cache_data.clear()
st.caption('Quotes are refreshed when you tap the button or reload. Public spot prices may differ from the Kalshi settlement benchmark.')
for sym in symbols:
 try:
  data=fetch(BASE+'/markets',{'series_ticker':SERIES[sym],'status':'open','limit':100})
  markets=data.get('markets',[])
  markets=[m for m in markets if (minutes(m) or -1)>0]
  if not markets:st.info(f'{sym}: No active contract found');continue
  m=min(markets,key=lambda m:minutes(m))
  up=quote(m,'yes_ask');down=quote(m,'no_ask');yb=quote(m,'yes_bid');nb=quote(m,'no_bid')
  if up is None and nb is not None:up=1-nb
  if down is None and yb is not None:down=1-yb
  remaining=minutes(m)
  try:
   rows=candles(sym);signal,reason,strength=screen(m,rows,up,down,remaining);spot=float(rows[-1][4])
  except Exception as e:
   signal,reason,strength='WAIT',f'Spot data unavailable: {e}',None;spot=None
  with st.container(border=True):
   st.subheader(f'{sym} — {signal}')
   st.caption(f"{m.get('title','')} | {m.get('ticker','')}")
   a,b,c=st.columns(3)
   a.metric('UP ask',fmt(up));b.metric('DOWN ask',fmt(down));c.metric('Minutes left',f'{remaining:.1f}')
   st.write('Coinbase spot:',f'${spot:,.4f}' if spot else 'Unavailable')
   st.write('Momentum strength:',f'{strength:.2f}× noise' if strength is not None else 'N/A')
   st.info(reason)
   st.caption('Missing: exact Kalshi target, benchmark settlement price, order book depth, fees, and historical calibration. Signals may be wrong.')
 except Exception as e:st.error(f'{sym}: {e}')
st.divider()
st.write('Next upgrade: contract-specific target/reference feed, settlement-aware probabilities, fee-adjusted entry thresholds, and backtesting. Until then, this is a research tool, not a trade recommendation.')
