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

**Had I not sold:** a card next to the pot compares the actual pot with what it would be worth if holdings that were sold had been kept. The formula is: actual pot, minus the net proceeds of each sale in `sold_ledger.json`, plus the current value of those units at the latest Yahoo close on or before the pot's date (same conventions as elsewhere: LSE prices in pence except VWRP and IGLS in pounds, RELX is `REL.L`, USD converted at that day's GBP/USD close). Each sale also shows its own difference in £ and %. Green means selling has helped (actual pot is ahead); red means it has cost. A missing price is reported and skipped, never guessed. Daily snapshots are stored in `data.json` (`had_i_not_sold` and `had_i_not_sold_history`) and in `sold_comparison_history.json`.

### Recording a sale
Add one holding at a time, or several on the same day (for example if everything is sold):

```
python3 add_sale.py --date 2026-11-23 --ticker AAPL --units 10 --price 230.1 \
    --currency USD --fx 1.31 --proceeds 1745.2 --estimated
```

Several sales on one day, from a JSON list of the same fields:

```
python3 add_sale.py --date 2026-11-23 --batch monday_sales.json
```

Use `--confirmed` instead of `--estimated` when the figures come from a contract note. Running the same date, ticker and units again updates that row (the usual way to replace an estimate). Then re-run `update.py`. Nothing here places a trade; proceeds and prices are recorded exactly as given.

**Chart:** `update.py` also writes `chart.png` (three panels: day / rolling 1-week / rolling 1-month % moves,
the same three lines in £, then the daily pot total in £, for the last 30 days) to the repo root and embeds it in the page.
Move panels are contribution-adjusted. Contribution or transfer-in days are marked on the pot panel when any fall in the window, and the latest pot value is labelled.
The nightly brief email embeds this same `chart.png`, so all three panels go out automatically:
`<img src="https://ianbanner.github.io/sipp-dashboard/chart.png?d=YYYYMMDD">`

## Picks vs VWRP

A card under "Had I not sold" checks how the picks sleeve (every individual share, i.e. everything except VWRP, IGLS, SGLN and cash) has done against putting the same money into VWRP.

- **Picks value on a date:** units from `picks_lots.json` less sales in `sold_ledger.json` (both on or before that date), at the Yahoo close on or before it, plus the net proceeds of picks already sold (e.g. NVDA). USD names use that day's GBP/USD close.
- **VWRP line:** the picks' value at the start of a period bought into VWRP at that day's close, plus each later picks buy (all-in £ from `picks_lots.json`) bought into VWRP at its own buy-day close. Sale proceeds stay as cash on the picks side; the VWRP line keeps holding.
- **Periods:** rolling 1 month (same day last month to the pot's date), each calendar month since the baseline (7 Oct 2026, the last close before the pension transfer was invested), and since the baseline.
- **Gap:** picks minus VWRP in £, and as a % of the VWRP figure. The card counts how many completed calendar months in a row the picks have been behind, and shows whether the current month is behind so far.
- **Missing prices:** a ticker with no price is left out of both sides and listed; nothing is guessed. If the lots file and the holdings table disagree, the card says so.
- **Proposed rule (not yet adopted):** consider trimming the picks sleeve if behind VWRP 3 months running. This is shown as a suggestion only; it is not part of the trading rules until Ian approves it.
- **History:** nightly rows are kept in `picks_vs_vwrp_history.json`; the latest figures are in `data.json` under `picks_vs_vwrp`.
- **New picks buy:** add a lot to `picks_lots.json` (date, ticker, units, all-in `cost_gbp`). Picks sales go in `sold_ledger.json` via `add_sale.py` as before.

## Sources (read-only)
- Newest `YYYYMMDD daily_history.csv` in Drive `2026 Investment Club/08 Analysis Scripts`
- Newest `YYYYMMDD 2026-Wnn Buy-Sell Analysis.md` in `05 Weekly Reports` (ignore Archive)
- Holdings: newest `YYYYMMDD Portfolio.md` (quantities are recorded in the history note)
- Diary: latest `SIPP:` brief email in Ian's Sent mail
- Sold holdings: `sold_ledger.json` in this repo (current prices from the Yahoo Finance chart API at run time)

## Update
1. Download the newest history CSV and Buy-Sell report; optionally save the latest brief body to `brief.txt`.
2. Run:
   ```
   python3 update.py --history "20261026 daily_history.csv" \
       --report "20261023 2026-W43 Buy-Sell Analysis.md" --brief brief.txt
   ```
   This rewrites `data.json`, `chart.png` and `index.html` (from `template.html`), and appends that day's
   'had I not sold' figures to `sold_comparison_history.json` and the picks-vs-VWRP figures to
   `picks_vs_vwrp_history.json`.
3. `git commit -am "Update YYYY-MM-DD" && git push`

Anything the script can't parse shows as "unavailable" — figures are never invented.
Don't commit source CSV/MD files or anything from `api-keys.env`.
