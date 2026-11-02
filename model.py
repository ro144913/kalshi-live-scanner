"""Leakage-resistant empirical directional model using historical 1-minute Coinbase candles.
Coinbase is a proxy, NOT Kalshi's CF Benchmarks settlement index.
"""
import math
from datetime import datetime, timezone


def clean_candles(rows):
    """Return chronological, complete one-minute (timestamp, close) observations."""
    values = sorted({int(r[0]): float(r[4]) for r in rows if len(r) > 4 and float(r[4]) > 0}.items())
    return values


def empirical_forecast(candles, spot, target, minutes_left, min_samples=80):
    """Walk-forward analogs, excluding overlapping target horizon and avoiding future leakage.

    Analog samples use only features known at their historical timestamp, and
    outcomes at a matching horizon. This is a Coinbase-only proxy forecast.
    """
    series = clean_candles(candles)
    horizon = max(1, min(15, int(math.ceil(minutes_left))))
    if len(series) < 120 or not spot or not target:
        return {'valid': False, 'reason': 'Insufficient historical one-minute candles.'}
    times = [t for t, _ in series]
    prices = [p for _, p in series]
    # Require continuous minute bars to prevent gaps creating false horizons.
    analogs = []
    for i in range(30, len(series) - horizon):
        if times[i+horizon] - times[i] != 60*horizon or times[i] - times[i-30] != 1800:
            continue
        past = [math.log(prices[k]/prices[k-1]) for k in range(i-29, i+1)]
        volatility = math.sqrt(sum(x*x for x in past)/len(past))
        if volatility < 1e-9:
            continue
        momentum = math.log(prices[i]/prices[i-5]) / (volatility * math.sqrt(5))
        # Standardized distance to target at present. Compare analogous historical
        # movements to current required move, without pretending target was known then.
        future = math.log(prices[i+horizon]/prices[i]) / (volatility * math.sqrt(horizon))
        analogs.append((momentum, future, volatility))
    if len(analogs) < min_samples:
        return {'valid': False, 'reason': f'Only {len(analogs)} historical analogs; need {min_samples}.'}
    recent = [math.log(prices[k]/prices[k-1]) for k in range(len(prices)-30, len(prices))]
    vol = math.sqrt(sum(x*x for x in recent)/len(recent))
    if vol < 1e-9:
        return {'valid': False, 'reason': 'Zero observed volatility.'}
    momentum_now = math.log(prices[-1]/prices[-6])/(vol*math.sqrt(5))
    required = math.log(target/spot)/(vol*math.sqrt(horizon))
    # Smooth similarity weighting. No fitted parameters and no invented accuracy claim.
    weighted = [(math.exp(-0.5*((m-momentum_now)/1.25)**2), f >= required) for m, f, _ in analogs]
    weight = sum(w for w, _ in weighted)
    effective_n = weight*weight/sum(w*w for w, _ in weighted) if weight else 0
    if effective_n < 30:
        return {'valid': False, 'reason': 'Too few comparable historical conditions.'}
    up = sum(w for w, result in weighted if result)/weight
    return {'valid': True, 'p_up': up, 'analogs': len(analogs), 'effective_n': effective_n,
            'horizon_min': horizon, 'proxy': 'Coinbase 1m analogs; NOT settlement-calibrated'}


def price_limit(probability, fee_cents, safety_cents):
    if probability is None or not 0 <= probability <= 1:
        return None
    return max(0, min(100, probability*100 - fee_cents - safety_cents))


def classify(direction, ask_cents, maximum_cents, seconds_left, validated=False):
    if ask_cents is None or maximum_cents is None:
        return 'WAIT', 'Missing quote or model estimate.'
    if seconds_left < 120:
        return 'WAIT', 'Too close to settlement (under two minutes).'
    if seconds_left > 780:
        return 'WAIT', 'Opening minute: wait for initial quote formation.'
    if ask_cents > maximum_cents:
        return 'WAIT', 'Current ask exceeds maximum model-derived price.'
    if not validated:
        return 'WATCH ' + direction, 'Experimental proxy screen only; Kalshi settlement alignment and out-of-sample profitability are not verified.'
    return 'ENTER ' + direction, 'Validated model entry (subject to current risk limits).'
