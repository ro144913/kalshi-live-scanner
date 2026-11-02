"""Contract-level evaluation of experimental forecasts, with no invented fills."""
from collections import defaultdict
import math


def first_contract_records(rows):
    grouped = defaultdict(list)
    for row in rows:
        if row.get('ticker'):
            grouped[row['ticker']].append(row)
    out = []
    for ticker, records in grouped.items():
        records.sort(key=lambda r: r.get('observed_utc') or '')
        first = records[0]
        outcome = next((r.get('actual_result') for r in records if r.get('actual_result') in ('UP', 'DOWN')), 'PENDING')
        direction = first.get('direction')
        probability = first.get('model_up_probability')
        try:
            probability = float(probability) if probability is not None else None
        except (TypeError, ValueError):
            probability = None
        if probability is not None and not 0 <= probability <= 1:
            probability = None
        correct = (direction == outcome) if direction in ('UP','DOWN') and outcome in ('UP','DOWN') else None
        watch = next((r for r in records if str(r.get('signal', '')).startswith('WATCH') and r.get('direction') == direction), None)
        ask = None
        if watch:
            ask = watch.get('up_ask_cents') if direction == 'UP' else watch.get('down_ask_cents')
            try:
                ask = float(ask)
                if not 0 <= ask <= 100: ask = None
            except (ValueError, TypeError):
                ask = None
        out.append({'ticker': ticker, 'symbol': first.get('symbol',''), 'first_seen_utc': first.get('observed_utc',''),
                    'direction': direction, 'p_up': probability, 'actual_result': outcome, 'correct': correct,
                    'watch_observed': watch is not None, 'watch_ask_cents': ask,
                    'watch_time_utc': watch.get('observed_utc') if watch else None,
                    'benchmark_verified': bool(first.get('benchmark_verified', False))})
    return sorted(out, key=lambda r: r['first_seen_utc'], reverse=True)


def metrics(records, fee_cents=2.0):
    settled = [r for r in records if r['correct'] is not None]
    n = len(settled)
    correct = sum(r['correct'] for r in settled)
    probabilistic = [r for r in settled if r['p_up'] is not None]
    brier = (sum((r['p_up'] - (1 if r['actual_result'] == 'UP' else 0))**2 for r in probabilistic) / len(probabilistic)) if probabilistic else None
    # Observed WATCH quote is a hypothetical entry, not a trade or a fill.
    hypothetical = [r for r in settled if r['watch_observed'] and r['watch_ask_cents'] is not None]
    hypothetical_net = sum((100 if r['correct'] else 0) - r['watch_ask_cents'] - fee_cents for r in hypothetical)
    return {'settled': n, 'correct': correct, 'accuracy': correct/n if n else None,
            'brier': brier, 'brier_n': len(probabilistic), 'hypothetical_n': len(hypothetical),
            'hypothetical_net_cents': hypothetical_net if hypothetical else None}


def calibration(records):
    bins = []
    settled = [r for r in records if r['correct'] is not None and r['p_up'] is not None]
    for lower in (0, .2, .4, .6, .8):
        subset = [r for r in settled if lower <= r['p_up'] < lower+.2 or (lower == .8 and r['p_up'] == 1)]
        if subset:
            bins.append({'Probability band': f'{lower:.0%}–{lower+.2:.0%}', 'Contracts': len(subset),
                         'Average predicted UP': sum(r['p_up'] for r in subset)/len(subset),
                         'Actual UP frequency': sum(r['actual_result']=='UP' for r in subset)/len(subset)})
    return bins
