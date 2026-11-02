# Kalshi opportunity scanner V6

Replace your GitHub repository root `app.py` with the included file. Keep the existing Streamlit Supabase secrets (`SUPABASE_URL`, `SUPABASE_KEY`) and existing `scanner_snapshots` table. No new SQL is needed. Do not upload secrets to GitHub.

V6 removes the large V5 forced-WAIT banner and allows **WATCH UP / WATCH DOWN** for opportunities that pass the experimental model, fee and time-window checks. Other markets show WAIT. WATCH is **not** a verified trade recommendation: Coinbase spot does not necessarily match the Kalshi settlement benchmark and the model is uncalibrated. No Kalshi account connection or order placement is included. Existing saved snapshots are retained.

Streamlit auto-redeploys after GitHub commit. Verify app loads and Supabase history remains present. Rotate any previously exposed Supabase secret.
