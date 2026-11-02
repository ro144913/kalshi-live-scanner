# Kalshi 15-minute research scanner v2

Deploy `app.py` with `requirements.txt` at the root of a Streamlit Community Cloud GitHub repository.

This is an experimental research tool, not a validated betting strategy. It does not place trades. It refreshes while open, fetches Kalshi market asks and Coinbase one-minute candles, uses an exchange-provided numeric target when available, and computes an uncalibrated zero-drift volatility estimate. If a target is unavailable, it refuses to generate a directional recommendation. Coinbase spot may differ from Kalshi's settlement benchmark. Verify fees, market specifications and all live data before acting. Never scale based on these signals without independent forward testing.
