# Kalshi 15-minute Research Scanner

**Research only. No orders, no automated trading, no verified entry signals.** The Kalshi settlement benchmark has NOT been validated against Coinbase; all trade signals intentionally remain `WAIT`.

## One-time installation

1. Upload `app.py`, `collector.py`, `requirements.txt`, `setup.sql`, and `.github/workflows/collector.yml` to the **root of your GitHub repository** (preserve the `.github/workflows` folders). Commit them. GitHub ZIP upload does not automatically extract; extract locally first or use GitHub's file uploader.
2. Run `setup.sql` in Supabase SQL Editor once. If `scanner_snapshots` already exists with the correct schema, the `IF NOT EXISTS` statements are safe to rerun. Existing table schemas are **not** repaired automatically.
3. In Streamlit Community Cloud, set app main file to `app.py`. Under **Manage app → Settings → Secrets**, set:
   ```toml
   SUPABASE_URL = "https://YOUR-PROJECT.supabase.co"
   SUPABASE_KEY = "YOUR_NEW_SUPABASE_SECRET_KEY"
   ```
4. In GitHub repository **Settings → Secrets and variables → Actions → New repository secret**, create **two** repository secrets named `SUPABASE_URL` and `SUPABASE_KEY`, with the same values. Do not put keys in committed files. Rotate any previously exposed keys.
5. In GitHub **Actions**, enable workflows if prompted. Select **Collect Kalshi research snapshots → Run workflow** to test. Check the run logs for `Saved ...`. Scheduled runs are *approximately* every 5 minutes and can be delayed or dropped on GitHub-hosted runners. They are **not** guaranteed always-on or per-minute collection.
6. In Supabase SQL Editor, run:
   ```sql
   select count(*) as saved_snapshots from public.scanner_snapshots;
   select source, count(*) from (
     select coalesce(payload->>'source','streamlit_app') as source
     from public.scanner_snapshots
   ) t group by source;
   ```
   A nonzero `scheduled_collector` count confirms the GitHub Actions collector wrote data. The Streamlit app also collects while open.

## Limitations

- This is an operational **research collection and dashboard** package, not a verified profitable trading system.
- The background collector records Kalshi quotes and fresh Coinbase spot prices and updates finalized Kalshi YES/NO outcomes. It deliberately does not calculate a trade probability or emit entry signals.
- Streamlit app still shows an **uncalibrated model** and blocks entries because the Kalshi settlement reference has not been independently verified.
- GitHub Actions scheduled jobs can be late or skipped; a paid always-on worker is required for reliable high-frequency collection.
- GitHub Actions repository secrets are separate from Streamlit Secrets.
- For safety, keep RLS enabled and do not create public write policies for the snapshot table.
- Back up Supabase data and review provider usage limits.
