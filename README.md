# SIPP dashboard

A self-contained static page (`index.html`, inline CSS/JS, no build step) mirroring the nightly SIPP brief
email for Ian's AJ Bell SIPP, plus the latest Buy-Sell Analysis advice.

**Headline section:** the top of the page shows this week's trades — one line per BUY/SELL (sells first) with
ticker, quantity, approx price, limit, cost and timing, parsed from the `H) Trade Tickets` block of the report.
If the report has no tickets it shows "No trades this week". `data.json` holds the same data
the page shows (it is also embedded inside `index.html`, so the page works when opened straight from disk).

**These are research notes for Ian's own decisions, not financial advice.** Nothing here places trades.

**Headline card:** under the trades headline, a card like the daily email: pot total, cash/invested, and
24h / 1-week / 1-month moves (computed from the history CSV, contribution/transfer-adjusted) plus the
day's commentary (from `--brief`, i.e. the paragraph above "Diary - what's coming" in the brief email,
else from the history note; omitted if neither has one).


**Sell-to-cash estimate:** below the move charts, a card estimates the cost of selling (a) all US shares and (b) all UK-listed holdings to cash, using AJ Bell's published SIPP dealing and FX charges in `ajbell_charges.json` (source URL and the date those charges were checked). One online deal per holding; no stamp duty on sales; spreads and price moves are not included. `update.py` rebuilds this every run, including the nightly brief.

**Chart:** `update.py` also writes `chart.png` (two panels: day / rolling 1-week / rolling 1-month % moves on top,
and the same three lines in £ below, for the last 30 days, contribution-adjusted) to the repo root and embeds it in the page.
The nightly brief email embeds this same `chart.png`, so both panels go out automatically:
`<img src="https://ianbanner.github.io/sipp-dashboard/chart.png?d=YYYYMMDD">`

## Sources (read-only)
- Newest `YYYYMMDD daily_history.csv` in Drive `2026 Investment Club/08 Analysis Scripts`
- Newest `YYYYMMDD 2026-Wnn Buy-Sell Analysis.md` in `05 Weekly Reports` (ignore Archive)
- Holdings: newest `YYYYMMDD Portfolio.md` (quantities are recorded in the history note)
- Diary: latest `SIPP:` brief email in Ian's Sent mail

## Update
1. Download the newest history CSV and Buy-Sell report; optionally save the latest brief body to `brief.txt`.
2. Run:
   ```
   python3 update.py --history "20261026 daily_history.csv" \
       --report "20261023 2026-W43 Buy-Sell Analysis.md" --brief brief.txt
   ```
   This rewrites `data.json`, `chart.png` and `index.html` (from `template.html`).
3. `git commit -am "Update YYYY-MM-DD" && git push`

Anything the script can't parse shows as "unavailable" — figures are never invented.
Don't commit source CSV/MD files or anything from `api-keys.env`.
