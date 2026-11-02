# Kalshi V7 — Historical Opportunity Scanner

This is a **working experimental scanner**, not a validated or guaranteed profitable trading model. It is **not** an automatic trader and does not require a Kalshi account API key.

## What is implemented

- Public Kalshi 15-minute market discovery and current executable ask quotes for BTC, ETH, SOL, XRP.
- Coinbase 1-minute historical candle analog probability estimator with no future information used in each analog's features. Requires sufficient historical samples; otherwise WAIT.
- **One frozen directional forecast per contract per Streamlit session**, with live quote/fee/price checks. If Streamlit restarts, the forecast can be recreated at a different time; full persistence of frozen forecasts across restarts is **not yet implemented**.
- Explicit maximum model-derived price, WATCH UP / WATCH DOWN / WAIT, data freshness checks, current fees and safety margin inputs.
- Existing `scanner_snapshots` Supabase storage and settled-outcome lookup from Kalshi's public market API. No SQL changes required.
- A unit-test suite for core model checks.

## Important limits

1. Coinbase is **not** Kalshi's official CF Benchmarks settlement reference. Historical analog probabilities are not calibrated to Kalshi settlements.
2. Historical Kalshi bid/ask quotes at candidate entry times are **not** available in this package. Consequently, no empirically optimized entry time or demonstrated after-fee trading edge is claimed. The entry timing rules (wait through first minute; avoid final two minutes) are conservative heuristics, **not backtested optimal windows**.
3. One-minute Coinbase candles typically cover a short rolling lookback, not thousands of independently settled Kalshi markets. Multiple overlapping analogs are correlated; effective sample size is a heuristic, not proof of reliability.
4. The system tracks observed contracts only while the Streamlit app is active. It is not a guaranteed 24/7 collector.
5. WATCH is **not** a verified recommendation to bet. Do not interpret its probability as proven accuracy.

## Install on Streamlit

1. Extract ZIP. Upload **both** `app.py` and `model.py` to the **root** of your existing GitHub repository. Keep `requirements.txt` (or replace with included one). Preserve the `model.py` filename exactly.
2. Commit. Streamlit redeploys from GitHub.
3. Keep existing `SUPABASE_URL` and `SUPABASE_KEY` in Streamlit Secrets, not in source control. **Rotate any Supabase key previously exposed in screenshots.**
4. Existing Supabase table `public.scanner_snapshots` is reused. No new SQL.
5. Verify app loads, refresh market quotes, and confirm new snapshots in Supabase.

## Test locally

```bash
python -m unittest discover -s tests -v
streamlit run app.py
```

## Next validation requirements before real-money confidence

Obtain historical Kalshi order-book/ask data, official settlement reference data, and a sufficiently long period of finalized contract outcomes. Train and evaluate using chronological train/validation/test splits with realistic fills, fees, slippage, and repeated-market correlation. Only then select entry windows and report measured out-of-sample calibration and net returns. No such results are asserted here.
