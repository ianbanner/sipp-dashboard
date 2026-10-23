# SIPP dashboard

A self-contained static page (`index.html`, inline CSS/JS, no build step) mirroring the nightly SIPP brief
email for Ian's AJ Bell SIPP, plus the latest Buy-Sell Analysis advice.

**Headline section:** the top of the page shows this week's trades — one line per BUY/SELL (sells first) with
ticker, quantity, approx price, limit, cost and timing, parsed from the `H) Trade Tickets` block of the report.
If the report has no tickets it shows "No trades this week". `data.json` holds the same data
the page shows (it is also embedded inside `index.html`, so the page works when opened straight from disk).

**These are research notes for Ian's own decisions, not financial advice.** Nothing here places trades.

## Sources (read-only)
- Newest `YYYYMMDD daily_history.csv` in Drive `2026 Investment Club/08 Analysis Scripts`
- Newest `YYYYMMDD 2026-Wnn Buy-Sell Analysis.md` in `05 Weekly Reports` (ignore Archive)
- Holdings: newest `YYYYMMDD Portfolio.md` (quantities are recorded in the history note)
- Diary: latest `SIPP:` brief email in [redacted] Sent

## Update
1. Download the newest history CSV and Buy-Sell report; optionally save the latest brief body to `brief.txt`.
2. Run:
   ```
   python3 update.py --history "20261022 daily_history.csv" \
       --report "20261023 2026-W43 Buy-Sell Analysis.md" --brief brief.txt
   ```
   This rewrites `data.json` and `index.html` (from `template.html`).
3. `git commit -am "Update YYYY-MM-DD" && git push`

Anything the script can't parse shows as "unavailable" — figures are never invented.
Don't commit source CSV/MD files or anything from `api-keys.env`.
