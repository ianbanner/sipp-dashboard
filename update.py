#!/usr/bin/env python3
"""Regenerate data.json for the SIPP dashboard.

Usage:
  python3 update.py --history "20261022 daily_history.csv" --report "20261016 2026-W42 Buy-Sell Analysis.md" \
      [--brief brief.txt] [--out data.json]

--history : newest daily_history.csv from Drive 08 Analysis Scripts
--report  : newest Buy-Sell Analysis .md from Drive 05 Weekly Reports (ignore Archive)
--brief   : optional plain-text body of the latest 'SIPP:' brief email (used for the diary)
Holdings quantities are read from the note in the latest history row ("Holdings = ... (VWRP 122, ...)").
SELL fills in executed.json dated on or before that row are applied, so a sale drops out of the
holdings table and the sell-to-cash card without a hardcoded ticker list.
Each run also values sold_ledger.json at the latest Yahoo close and writes a
'had I not sold' comparison to data.json (and sold_comparison_history.json).
It also compares the picks sleeve (picks_lots.json + sold_ledger.json) with the same money in VWRP,
over a rolling month, each calendar month and since the baseline (picks_vs_vwrp_history.json).
Add sales with add_sale.py. Anything that cannot be parsed or priced is written
as null and shown as "unavailable". Nothing is invented.
"""
import argparse, csv, json, re, datetime, os, time, urllib.request, urllib.error

NAMES = {"VWRP": "Vanguard FTSE All-World (VWRP)", "SGLN": "iShares Physical Gold (SGLN)",
         "IGLS": "iShares UK Gilts 0-5yr (IGLS)", "GOOG": "Alphabet C", "HSBA": "HSBC", "SHEL": "Shell",
         "RELX": "RELX", "NVDA": "NVIDIA", "AMZN": "Amazon", "MSFT": "Microsoft", "META": "Meta"}
US = {"NVDA", "AMZN", "MSFT", "META", "GOOG"}
SLEEVE = {"VWRP": "tracker", "IGLS": "gilts", "SGLN": "gold"}
TARGETS = {"tracker": 50, "gilts": 5, "gold": 5, "picks": 40}

def num(s): return float(s.replace(",", ""))

def parse_history(path):
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    r = rows[-1]; note = r["note"]
    out = {"date": r["date"], "total": num(r["total"]), "invested": num(r["invested"]), "cash": num(r["cash"]), "note": note}
    m = re.search(r"FX GBP/USD ([\d.]+)", note); fx = float(m.group(1)) if m else None
    out["fx"] = fx
    hm = re.search(r"Holdings = [^()]*\(([^)]*)\)", note)
    qty = {}
    if hm:
        for t, q in re.findall(r"([A-Z]{3,5}) (\d+)", hm.group(1)): qty[t] = int(q)
    prices = {}
    for t in qty:
        key = "REL" if t == "RELX" else t
        m = re.search(r"\b%s (\d[\d,]*\.?\d*)(p?)" % ("RELX" if t == "RELX" else t), note)
        if m:
            v = num(m.group(1)); prices[t] = (v, "p" if m.group(2) else ("$" if t in US else "£"))
    day = {}
    dm = re.search(r"Day: (.*?)\.(?: [A-Z]|$)", note)
    if dm:
        for t, v in re.findall(r"([A-Z]{3,5}) ([+-]\d[\d.]*)", dm.group(1)): day[t] = float(v)
    hold = []
    for t, q in qty.items():
        p = prices.get(t); val = None; disp = None
        if p:
            v, u = p
            if u == "p": val = q * v / 100; disp = f"{v:,.2f}p"
            elif u == "$": val = q * v / fx if fx else None; disp = f"${v:,.2f}"
            else: val = q * v; disp = f"£{v:,.2f}"
        hold.append({"ticker": t, "name": NAMES.get(t, t), "qty": q, "price": disp,
                     "value": round(val, 2) if val is not None else None, "day_change": day.get(t),
                     "sleeve": SLEEVE.get(t, "picks")})
    for h in hold:
        h["weight"] = round(h["value"] / out["total"] * 100, 2) if h["value"] is not None else None
    out["holdings"] = sorted(hold, key=lambda h: -(h["value"] or 0))
    calc = sum(h["value"] for h in hold if h["value"] is not None)
    out["holdings_check"] = {"sum_of_values": round(calc, 2), "invested_in_history": out["invested"]}
    def span(label, rx):
        m = re.search(rx, note)
        if not m: return {"label": label, "span": None, "change": None, "pct": None, "adjusted": None}
        return {"label": label, "span": m.group(1), "change": float(m.group(2)), "pct": float(m.group(3)),
                "adjusted": (m.group(4) or "").strip() or None}
    out["changes"] = [
        span("24 hours", r"24h anchor ([^:]*?): ([+-][\d.]+) / ([+-][\d.]+)pct()"),
        span("1 week", r"1wk anchor ([^:]*?): ([+-][\d.]+) / ([+-][\d.]+)pct()"),
        span("1 month", r"1mo anchor ([^:]*?\)) ?(?:transfer-adjusted[^:]*)?: ([+-][\d.]+) / ([+-][\d.]+)pct( of pre-transfer pot)?"),
    ]
    m = re.search(r"1mo anchor [^:]*?(transfer-adjusted for [^:]*?):", note)
    if m: out["changes"][2]["adjusted"] = m.group(1) + " (% is on the pot before the transfer)"
    sl = {k: 0.0 for k in TARGETS}
    for h in hold:
        if h["value"] is not None: sl[h["sleeve"]] += h["value"]
    out["sleeves"] = [{"sleeve": k, "target_pct": TARGETS[k], "value": round(v, 2),
                       "actual_pct": round(v / out["total"] * 100, 2)} for k, v in sl.items()]
    out["sleeves"].append({"sleeve": "cash", "target_pct": None, "value": out["cash"],
                           "actual_pct": round(out["cash"] / out["total"] * 100, 2)})
    m = re.search(r"Diary: (.*?)\. Email", note)
    out["diary_from_history"] = [d.strip() for d in m.group(1).split(";")] if m else []
    return out

def build_series(path, days=30):
    """Day / rolling-week / rolling-month % and £ moves for each date in the last `days` days.
    Uses the last row per date. change = total - anchor_total - contributions after the anchor;
    pct = change / (anchor_total + contributions in the window). 24h anchor = previous dated row;
    1wk anchor = latest row dated <= date-7d; 1mo anchor = latest row dated <= date-30d.
    A series is skipped (None) when no anchor exists. Only recorded values are used.
    The £ series uses the same contribution-adjusted change as the % series.
    Each point also carries the day's pot total and any contribution/transfer in."""
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    D = lambda s: datetime.date.fromisoformat(s)
    byd = {}
    for r in rows: byd[r["date"]] = r              # last row per date wins
    contrib = {}
    for r in rows: contrib[r["date"]] = contrib.get(r["date"], 0) + num(r["contribution"] or "0")
    dates = sorted(byd)
    def move(i, anchor_date):
        a = byd[anchor_date]; t = byd[dates[i]]
        c = sum(v for d, v in contrib.items() if anchor_date < d <= dates[i])
        ch = num(t["total"]) - num(a["total"]) - c
        # % base = start pot + net external cash flows in the window (transfers/contributions), so
        # money paid in is neither counted as growth nor shrinks the % (v5 fix, 30 Oct 2026).
        return round(ch, 2), round(ch / (num(a["total"]) + c) * 100, 2), anchor_date
    def anchor(i, lag):
        lim = D(dates[i]) - datetime.timedelta(days=lag)
        c = [d for d in dates[:i] if D(d) <= lim]
        return c[-1] if c else None
    last = D(dates[-1]); out = []
    for i, d in enumerate(dates):
        if D(d) < last - datetime.timedelta(days=days) or i == 0: continue
        p = {"date": d, "total": round(num(byd[d]["total"]), 2),
             "contribution": round(contrib.get(d, 0), 2)}
        for k, a in (("day", dates[i-1]), ("week", anchor(i, 7)), ("month", anchor(i, 30))):
            if a:
                gbp, pct, anc = move(i, a)
                p[k], p[k + "_gbp"], p[k + "_anchor"] = pct, gbp, anc
            else:
                p[k] = p[k + "_gbp"] = p[k + "_anchor"] = None
        out.append(p)
    return out

def headline_changes(series, history_path):
    """24h / 1wk / 1mo rows for the latest date, from the same series as the chart (brief method)."""
    rows = list(csv.DictReader(open(history_path, encoding="utf-8")))
    byd = {r["date"]: r for r in rows}
    contrib = {}
    for r in rows: contrib[r["date"]] = contrib.get(r["date"], 0) + num(r["contribution"] or "0")
    p = series[-1]; t = datetime.date.fromisoformat(p["date"]); out = []
    fmt = lambda d: datetime.date.fromisoformat(d).strftime("%a %d %b").replace(" 0", " ")
    for k, label in (("day", "24 hours"), ("week", "1 week"), ("month", "1 month")):
        a = p.get(k + "_anchor")
        if not a: out.append({"label": label, "span": None, "change": None, "pct": None}); continue
        at = num(byd[a]["total"]); c = sum(v for d, v in contrib.items() if a < d <= p["date"])
        ch = round(num(byd[p["date"]]["total"]) - at - c, 2)
        days = (t - datetime.date.fromisoformat(a)).days
        span = f"since {fmt(a)} close" if k == "day" else f"{days} days, since {fmt(a)}"
        if c: span += f" · excludes the £{c:,.2f} {'L&G transfer' if c > 50000 else 'contribution'}; % is on £{at + c:,.2f} (start pot + money paid in)"
        out.append({"label": label, "span": span, "change": ch, "pct": round(ch / (at + c) * 100, 2)})
    return out

def commentary(note):
    m = re.search(r"Day: [^.]*\.\s+(.*?)(?:\s+Diary:|\s+Email|$)", note)
    return m.group(1).strip() if m and m.group(1).strip() else None

def write_chart(series, png):
    """Three-panel chart.png: % moves, the same moves in £, then the daily pot total.
    The nightly brief email embeds this file, so all three panels go out automatically."""
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    import matplotlib.ticker as mticker
    fig, axes = plt.subplots(3, 1, figsize=(8, 10.6), dpi=110, sharex=True)
    specs = (("day", "Day (24h)", "#1f4fd1"), ("week", "Rolling 1 week", "#0a7d3b"), ("month", "Rolling 1 month", "#b42318"))
    span = f"{series[0]['date']} to {series[-1]['date']} (contribution/transfer-adjusted)"
    gbp_fmt = mticker.FuncFormatter(lambda v, _: f"£{v:,.0f}")
    ax = axes[0]
    for k, lab, col in specs:
        pts = [(datetime.date.fromisoformat(p["date"]), p[k]) for p in series if p[k] is not None]
        if pts: ax.plot(*zip(*pts), marker="o", ms=3, lw=1.8, label=lab, color=col)
    ax.axhline(0, color="#999", lw=0.8); ax.set_ylabel("% move"); ax.legend(fontsize=8, frameon=False)
    ax.grid(alpha=.3); ax.set_title(f"SIPP moves, {span}", fontsize=10)
    ax = axes[1]
    for k, lab, col in specs:
        pts = [(datetime.date.fromisoformat(p["date"]), p[k + "_gbp"]) for p in series if p.get(k + "_gbp") is not None]
        if pts: ax.plot(*zip(*pts), marker="o", ms=3, lw=1.8, label=lab, color=col)
    ax.axhline(0, color="#999", lw=0.8); ax.set_ylabel("£ move"); ax.legend(fontsize=8, frameon=False)
    ax.grid(alpha=.3); ax.set_title(f"SIPP moves in £, {span}", fontsize=10)
    ax.yaxis.set_major_formatter(gbp_fmt)
    ax = axes[2]
    pot = [(datetime.date.fromisoformat(p["date"]), p["total"]) for p in series if p.get("total") is not None]
    if pot:
        ax.plot(*zip(*pot), marker="o", ms=3, lw=1.8, color="#1d2330", label="Pot total")
    marked = False
    for p in series:
        c = p.get("contribution") or 0
        if not c or p.get("total") is None: continue
        d = datetime.date.fromisoformat(p["date"])
        ax.axvline(d, color="#b45309", ls="--", lw=0.9, alpha=0.7)
        ax.scatter([d], [p["total"]], s=36, color="#b45309", zorder=5,
                   label="Contribution / transfer in" if not marked else None)
        marked = True
        kind = "Transfer" if c > 50000 else "Contribution"
        ax.annotate(f"{kind} +£{c:,.0f}", (d, p["total"]), xytext=(6, 10),
                    textcoords="offset points", fontsize=7, color="#b45309")
    if pot:
        d, v = pot[-1]
        ax.annotate(f"£{v:,.2f}", (d, v), xytext=(0, 10), textcoords="offset points",
                    ha="center", va="bottom", fontsize=8, fontweight="bold", color="#1d2330",
                    clip_on=False, annotation_clip=False)
        ax.margins(y=0.14)
    ax.set_ylabel("Pot (£)"); ax.grid(alpha=.3)
    ax.set_title(f"SIPP pot total, {series[0]['date']} to {series[-1]['date']}", fontsize=10)
    ax.yaxis.set_major_formatter(gbp_fmt)
    if marked: ax.legend(fontsize=8, frameon=False)
    axes[2].xaxis.set_major_formatter(mdates.DateFormatter("%d %b")); fig.autofmt_xdate()
    fig.tight_layout(); fig.savefig(png); plt.close(fig)

def section(md, start_rx):
    m = re.search(start_rx, md, re.M)
    if not m: return None
    rest = md[m.end():]
    e = re.search(r"^---\s*$|^## ", rest, re.M)
    return rest[: e.start()].strip() if e else rest.strip()

def parse_actions(md):
    """Headline trades from the H) Trade Tickets block: one dict per BUY/SELL row, sells first."""
    h = section(md, r"^## H\) .*Trade Tickets.*$") or ""
    acts, timing, hdr = [], None, None
    for line in h.splitlines():
        m = re.match(r"^### (.*)", line)
        if m:
            t = m.group(1).strip(); hdr = None
            timing = None if re.search(r"NONE|Market-hours|Notes", t, re.I) else re.sub(r"^(Right now|Next session)\s*[—-]\s*", "", t)
            if re.match(r"Right now", t) and not re.search(r"NONE", t, re.I): timing = "Right now"
            continue
        if not line.startswith("|") or re.match(r"^\|\s*-*:?-", line): continue
        c = [x.strip().replace("**", "") for x in line.strip().strip("|").split("|")]
        if hdr is None: hdr = [x.lower() for x in c]; continue
        row = dict(zip(hdr, c))
        act = row.get("action", "").upper()
        if act not in ("BUY", "SELL"): continue
        g = lambda k: next((v for kk, v in row.items() if kk.startswith(k)), None)
        acts.append({"action": act, "ticker": g("ticker"), "name": g("search"), "sleeve": g("sleeve"),
                     "qty": g("qty"), "price": g("approx price"), "cost": g("approx cost"),
                     "limit": g("order type"), "timing": timing})
    acts.sort(key=lambda a: a["action"] != "SELL")
    return acts

def parse_report(path):
    md = open(path, encoding="utf-8").read()
    title = md.splitlines()[0].lstrip("# ").strip()
    rep = {"file": path.split("/")[-1], "title": title}
    m = re.match(r"(\d{8})", rep["file"]); rep["date"] = datetime.datetime.strptime(m.group(1), "%Y%m%d").strftime("%a %d %b %Y") if m else None
    rep["tldr"] = section(md, r"^## A\) TL;DR.*$")
    rep["tickets"] = section(md, r"^## H\) .*Trade Tickets.*$")
    rep["next_actions"] = section(md, r"^## G\) .*$")
    rep["what_changed"] = None
    m = re.search(r"((?:^>.*\n)+)", md, re.M)
    if m: rep["what_changed"] = "\n".join(l[1:].lstrip() for l in m.group(1).splitlines())
    b = section(md, r"^## B\) .*$") or ""
    verdicts = []
    for line in b.splitlines():
        if line.startswith("| **") and "Total" not in line:
            c = [x.strip() for x in line.strip("|").split("|")]
            if len(c) >= 9:
                verdicts.append({"holding": c[0].replace("**", ""), "verdict": c[7].replace("**", ""), "reasoning": c[8].replace("**", "")})
    a1 = section(md, r"^## A1\) .*$") or ""
    for line in a1.splitlines():
        if line.startswith("| ") and any(k in line for k in ("VWRP", "IGLS", "SGLN")):
            c = [x.strip() for x in line.strip("|").split("|")]
            verdicts.append({"holding": c[0], "verdict": "Sleeve", "reasoning": c[-1].replace("**", "")})
    rep["verdicts"] = verdicts
    rep["actions"] = parse_actions(md)
    rep["sells"] = next((l.strip().replace("**", "") for l in md.splitlines() if l.startswith("**Sells to consider")), None)
    return rep

def apply_executed(rep, path=None):
    """Mark report tickets that were already placed as FILLED (durable: kept in executed.json in the repo)."""
    import os
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "executed.json")
    if not os.path.exists(path): return
    ex = json.load(open(path, encoding="utf-8"))
    wk = (re.search(r"\d{4}-W\d{2}", rep.get("title") or "") or [None])[0]
    hit = []
    for x in rep.get("actions", []):
        for f in ex.get("fills", []):
            tks = [t.upper() for t in [f["ticker"]] + f.get("alt_tickers", [])]
            ticker = re.sub(r"[*_`]", "", x["ticker"] or "").upper()
            if f.get("report_week") == wk and f["action"] == x["action"] and ticker in tks \
               and str(f["qty"]) == re.sub(r"\D", "", x["qty"] or ""):
                x.update({"status": "FILLED", "price": f["price"], "cost": f["total"],
                          "limit": "filled " + f["filled"], "timing": "settles " + f["settles"]})
                hit.append(f)
    if not hit: return
    rep["all_filled"] = all(x.get("status") == "FILLED" for x in rep["actions"])
    done = "; ".join(f"{f['action']} {f['ticker']} {f['qty']} @ {f['price']} ({f['total']}) filled {f['filled']}, settles {f['settles']}" for f in hit)
    banner = f"**✅ DONE — {done}.**" + (" " + ex.get("after_fills_note", "") if rep["all_filled"] else "") + " *Wording below is the original Friday report.*"
    rep["filled_banner"] = banner
    for k in ("tldr", "tickets", "next_actions"):
        if rep.get(k): rep[k] = banner + "\n\n" + rep[k]
    for v in rep.get("verdicts", []):
        for f in hit:
            tks = [f["ticker"]] + f.get("alt_tickers", [])
            if any(re.search(rf"\b{t}\b", v["holding"]) for t in tks) or re.search(rf"BUY {f['qty']}\b", v["reasoning"]) and any(t in v["reasoning"] or t in v["holding"].upper() for t in tks + ["VANGUARD", "TRACKER"]):
                v["holding"] = re.sub(r"(\d+) units", lambda m: f"{m.group(1)} units at report, +{f['qty']} bought", v["holding"])
                v["reasoning"] = f"DONE: bought {f['qty']} @ {f['price']} {f['filled']} (settles {f['settles']}). Was: " + v["reasoning"]

_MONTHS = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}

def parse_filled_date(text, as_of=None):
    """Best-effort date from an executed.json 'filled' string, or None."""
    if not text:
        return None
    mon = "Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec"
    m = re.search(rf"(\d{{1,2}})\s+({mon})[a-z]*\s+(\d{{4}})", text, re.I)
    if m:
        return datetime.date(int(m.group(3)), _MONTHS[m.group(2)[:3].title()], int(m.group(1)))
    m = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if m:
        return datetime.date.fromisoformat(m.group(1))
    m = re.search(rf"(\d{{1,2}})\s+({mon})[a-z]*", text, re.I)
    if not m or not as_of:
        return None
    as_of_d = as_of if isinstance(as_of, datetime.date) else datetime.date.fromisoformat(str(as_of)[:10])
    d = datetime.date(as_of_d.year, _MONTHS[m.group(2)[:3].title()], int(m.group(1)))
    if d > as_of_d + datetime.timedelta(days=60):
        d = datetime.date(as_of_d.year - 1, d.month, d.day)
    return d

def _fill_tickers(f):
    return {t.upper() for t in [f["ticker"]] + f.get("alt_tickers", [])}

def _fill_qty(f):
    return int(re.sub(r"\D", "", str(f.get("qty") or "0")) or 0)

def apply_executed_sales(holdings, path=None, as_of=None, pot_total=None):
    """Drop or reduce holdings that have a SELL fill in executed.json on or before as_of.

    The history note is the primary source of quantities. This covers sales recorded
    in executed.json before the history note has caught up, so sell-to-cash and the
    holdings table follow trades without a hardcoded ticker list. BUY fills are
    ignored here (history already carries current quantities).
    """
    import os
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "executed.json")
    if not holdings or not os.path.exists(path):
        return holdings
    ex = json.load(open(path, encoding="utf-8"))
    as_of_d = None
    if as_of:
        as_of_d = as_of if isinstance(as_of, datetime.date) else datetime.date.fromisoformat(str(as_of)[:10])
    sold = {}
    for f in ex.get("fills", []):
        if str(f.get("action") or "").upper() != "SELL":
            continue
        fd = parse_filled_date(f.get("filled") or "", as_of_d)
        if as_of_d and fd and fd > as_of_d:
            continue
        qty = _fill_qty(f)
        if qty <= 0:
            continue
        for t in _fill_tickers(f):
            sold[t] = sold.get(t, 0) + qty
    if not sold:
        return holdings
    out, changed = [], False
    for h in holdings:
        t = (h.get("ticker") or "").upper()
        q_sold = sold.get(t, 0)
        q = int(h.get("qty") or 0)
        if q_sold <= 0:
            out.append(h)
            continue
        remain = q - q_sold
        changed = True
        if remain <= 0:
            continue
        nh = dict(h)
        nh["qty"] = remain
        if h.get("value") is not None and q:
            nh["value"] = round(float(h["value"]) * remain / q, 2)
        out.append(nh)
    if not changed:
        return holdings
    if pot_total:
        for h in out:
            h["weight"] = round(h["value"] / pot_total * 100, 2) if h.get("value") is not None else None
    return out

def brief_commentary(path):
    t = open(path, encoding="utf-8").read().split("Diary - what's coming")[0]
    paras = [p.strip() for p in re.split(r"\n\s*\n", t) if p.strip()]
    paras = [p for p in paras if not re.match(r"(Chart|Dashboard|SIPP brief|Pot:|24 hours|Cash )", p) and "http" not in p]
    return " ".join(paras[-1].split()) if paras else None

def parse_brief(path):
    t = open(path, encoding="utf-8").read()
    m = re.search(r"Diary - what's coming\n((?:- .*\n?)+)", t)
    return [l[2:].strip() for l in m.group(1).splitlines()] if m else []


def money(v):
    return None if v is None else round(float(v) + 0.0, 2)

def fx_charge(sterling, bands):
    """Blended FX charge on one deal's sterling consideration, using the published bands."""
    remaining, prev, charge = float(sterling), 0.0, 0.0
    for b in bands:
        cap, rate = b.get("up_to_gbp"), float(b["rate"])
        slice_amt = remaining if cap is None else min(remaining, float(cap) - prev)
        if slice_amt > 0:
            charge += slice_amt * rate
            remaining -= slice_amt
        if cap is not None:
            prev = float(cap)
        if remaining <= 1e-12:
            break
    return money(charge)

def sell_to_cash(holdings, charges_path):
    """Estimate the cost of selling holdings to cash from AJ Bell's published price list.
    One online deal per holding. No stamp duty on sales. No spread or price-move estimate.
    PTM levy is applied only to configured UK-company tickers when the deal is over the threshold.
    """
    import os
    if not charges_path or not os.path.exists(charges_path):
        return None
    ch = json.load(open(charges_path, encoding="utf-8"))
    deal = ch["frequent_share_dealing_gbp"] if ch.get("frequent_dealing") else ch["online_share_dealing_gbp"]
    deal = float(deal)
    ptm_tickers = set(ch.get("ptm_tickers") or [])
    ptm_levy = float(ch.get("ptm_levy_gbp") or 0)
    ptm_threshold = float(ch.get("ptm_threshold_gbp") or 0)
    bands = ch.get("fx_bands") or []
    # Short labels for the card subtitle; unknown names fall back to ticker or the holdings name.
    short = {"GOOG": "Alphabet", "AMZN": "Amazon", "META": "Meta", "MSFT": "Microsoft",
             "NVDA": "NVIDIA", "HSBA": "HSBC", "RELX": "RELX", "SHEL": "Shell",
             "SGLN": "SGLN", "IGLS": "IGLS", "VWRP": "VWRP"}

    def is_us(h):
        t = h.get("ticker") or ""
        if t in US:
            return True
        p = h.get("price") or ""
        return isinstance(p, str) and p.startswith("$")

    def display_name(h):
        t = h.get("ticker") or ""
        if t in short:
            return short[t]
        n = h.get("name") or NAMES.get(t, t)
        return t if re.search(r"\([A-Z]{3,5}\)$", n or "") else n

    def line(h):
        t = h.get("ticker") or ""
        if h.get("value") is None:
            return {"ticker": t, "name": h.get("name") or NAMES.get(t, t), "gross": None,
                    "dealing": None, "fx": None, "ptm": None, "costs": None, "net": None,
                    "missing": True}
        val = float(h["value"])
        d = deal
        f = fx_charge(val, bands) if is_us(h) else 0.0
        p = ptm_levy if (t in ptm_tickers and val > ptm_threshold) else 0.0
        costs = money(d + f + p)
        return {"ticker": t, "name": h.get("name") or NAMES.get(t, t), "gross": money(val),
                "dealing": money(d), "fx": money(f), "ptm": money(p), "costs": costs,
                "net": money(val - costs), "missing": False}

    # Groups follow the holdings actually held (after any executed.json sales),
    # so a sale such as NVDA drops off on its own and a new name is included.
    group_specs = (
        ("us", "All US shares", [h for h in holdings if is_us(h)]),
        ("uk", "All UK-listed holdings", [h for h in holdings if not is_us(h)]),
    )
    out_groups = []
    complete = True
    for gid, label, raw in group_specs:
        items, gross, dealing, fx, ptm = [], 0.0, 0.0, 0.0, 0.0
        for h in raw:
            item = line(h)
            items.append(item)
            if item["missing"]:
                complete = False
                continue
            gross += item["gross"]; dealing += item["dealing"]; fx += item["fx"]; ptm += item["ptm"]
        missing = any(i["missing"] for i in items)
        names = ", ".join(display_name(h) for h in raw) or "none currently held"
        costs = money(dealing + fx + ptm)
        out_groups.append({
            "id": gid, "label": label, "names": names,
            "holdings": items, "deals": sum(1 for i in items if not i["missing"]),
            "gross": money(gross) if not missing else None,
            "dealing": money(dealing), "fx": money(fx), "ptm": money(ptm),
            "costs": costs if not missing else None,
            "net": money(gross - dealing - fx - ptm) if not missing else None,
        })
    src = ch.get("source") or {}
    return {
        "estimate": True,
        "title": "Estimated cost of selling to cash",
        "disclaimer": "Estimate from AJ Bell's published price list. One online deal per holding. "
                      "No stamp duty on sales. Spreads and price moves are not included, so the cash "
                      "that actually lands may differ.",
        "dealing_rate_gbp": deal,
        "frequent_dealing": bool(ch.get("frequent_dealing")),
        "frequent_rate_gbp": ch.get("frequent_share_dealing_gbp"),
        "fx_bands": bands,
        "ptm_levy_gbp": ptm_levy,
        "ptm_threshold_gbp": ptm_threshold,
        "source_url": src.get("sipp_charges_page"),
        "international_url": src.get("international_page"),
        "pdf_url": src.get("sipp_charges_pdf"),
        "checked": src.get("checked"),
        "effective_from": src.get("effective_from"),
        "complete": complete,
        "groups": out_groups,
    }


LSE_POUNDS = {"VWRP", "IGLS"}  # Yahoo .L closes in pounds; other LSE names are in pence
FX_SYMBOL = "GBPUSD=X"
HISTORY_FILE = "sold_comparison_history.json"


def yahoo_symbol_for(ticker, currency=None, explicit=None):
    if explicit:
        return explicit
    t = (ticker or "").upper()
    if t == "RELX":
        return "REL.L"
    if (currency or "").upper() == "USD" or t in US:
        return t
    return t + ".L"


def _chart_rows(result):
    """Daily (date, close) pairs from a Yahoo chart result, exchange-local dates, no nulls."""
    ts = result.get("timestamp") or []
    closes = (result.get("indicators") or {}).get("quote", [{}])[0].get("close") or []
    offset = (result.get("meta") or {}).get("gmtoffset") or 0
    rows = []
    for t, c in zip(ts, closes):
        if t is None or c is None:
            continue
        d = datetime.datetime.fromtimestamp(t + offset, datetime.timezone.utc).date()
        rows.append((d, float(c)))
    return rows


def fetch_yahoo_chart(symbol, range_="15d"):
    """Latest Yahoo daily closes for one symbol. Returns {"rows": [...], "error": None} or error text. Never guesses."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range_}&interval=1d"
    last_err = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            payload = json.load(urllib.request.urlopen(req, timeout=20))
            err = (payload.get("chart") or {}).get("error")
            results = (payload.get("chart") or {}).get("result") or []
            if err or not results:
                return {"rows": [], "error": (err or f"Yahoo returned no result for {symbol}")}
            rows = _chart_rows(results[0])
            if not rows:
                return {"rows": [], "error": f"Yahoo returned no closes for {symbol}"}
            return {"rows": rows, "error": None}
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            last_err = str(e)
            time.sleep(2)
    return {"rows": [], "error": last_err or f"Yahoo request failed for {symbol}"}


def close_on_or_before(rows, as_of):
    """The latest (date, close) on or before as_of, or the latest row if as_of is None."""
    if not rows:
        return None
    if as_of is None:
        return rows[-1]
    as_of_d = as_of if isinstance(as_of, datetime.date) else datetime.date.fromisoformat(str(as_of)[:10])
    ok = [r for r in rows if r[0] <= as_of_d]
    return ok[-1] if ok else None


def format_px(ticker, price, currency=None, yahoo=False):
    """Format a price. yahoo=True uses the LSE/US Yahoo conventions, ignoring sale currency."""
    t = (ticker or "").upper()
    if yahoo:
        if t in US:
            return f"${price:,.2f}"
        if t in LSE_POUNDS:
            return f"£{price:,.2f}"
        return f"{price:,.2f}p"
    raw = currency or ""
    cur = raw.upper()
    if cur == "USD" or (not cur and t in US):
        return f"${price:,.2f}"
    if raw in ("GBp", "GBx") or cur in ("GBX", "GBPENCE", "P"):
        return f"{price:,.2f}p"
    if cur == "GBP" or (not cur and t in LSE_POUNDS):
        return f"£{price:,.2f}"
    return f"{price:,.2f}p"


def value_at_yahoo_close(ticker, units, price, fx):
    """GBP value of units at a Yahoo close, using ticker conventions (not the sale currency). None if FX is missing for a USD name."""
    t = (ticker or "").upper()
    if t in US:
        if not fx:
            return None
        return units * price / float(fx)
    if t in LSE_POUNDS:
        return units * price
    return units * price / 100  # other LSE names are quoted in pence


def load_sold_ledger(path):
    if not path or not os.path.exists(path):
        return []
    data = json.load(open(path, encoding="utf-8"))
    sales = data.get("sales") if isinstance(data, dict) else data
    return list(sales or [])


def had_i_not_sold(actual_pot, as_of, ledger_path, history_path=None):
    """Compare the actual pot with what it would be worth if sold units had been kept.

    Formula: actual pot − sum of net proceeds of priced sales + sum of current value of those units.
    Each sale is valued at the Yahoo close on the pot's as_of date (or the latest close on or
    before that date). USD names use that day's GBP/USD close. A sale with a missing price, FX
    or proceeds is skipped and flagged; its figures are never guessed.
    """
    as_of_d = as_of if isinstance(as_of, datetime.date) else datetime.date.fromisoformat(str(as_of)[:10])
    sales = load_sold_ledger(ledger_path)
    included = [s for s in sales if s.get("date") and datetime.date.fromisoformat(str(s["date"])[:10]) <= as_of_d]
    out = {
        "as_of": as_of_d.isoformat(),
        "actual_pot": money(actual_pot) if actual_pot is not None else None,
        "had_i_not_sold": None,
        "gap_gbp": None,
        "gap_pct": None,
        "complete": True,
        "fx_gbpusd": None,
        "fx_date": None,
        "sales": [],
        "totals": {"proceeds_gbp": None, "current_value_gbp": None},
        "missing": [],
        "note": None,
    }
    if not included:
        out["note"] = "No sales in the ledger on or before this date."
        out["had_i_not_sold"] = money(actual_pot) if actual_pot is not None else None
        out["gap_gbp"] = 0.0 if actual_pot is not None else None
        out["gap_pct"] = 0.0 if actual_pot is not None else None
        return out

    symbols = []
    for s in included:
        symbols.append(s.get("yahoo_symbol") or yahoo_symbol_for(s.get("ticker"), s.get("currency")))
    need_fx = any((s.get("currency") or "").upper() == "USD" or (s.get("ticker") or "").upper() in US for s in included)
    if need_fx:
        symbols.append(FX_SYMBOL)

    charts = {}
    for sym in dict.fromkeys(symbols):
        charts[sym] = fetch_yahoo_chart(sym)

    fx_px = close_on_or_before(charts.get(FX_SYMBOL, {}).get("rows") or [], as_of_d) if need_fx else None
    if need_fx:
        if fx_px:
            out["fx_gbpusd"] = round(fx_px[1], 4)
            out["fx_date"] = fx_px[0].isoformat()
        else:
            err = (charts.get(FX_SYMBOL) or {}).get("error") or f"no GBP/USD close on or before {as_of_d}"
            out["missing"].append({"ticker": "GBPUSD=X", "reason": err})

    proceeds_sum = 0.0
    current_sum = 0.0
    priced = 0
    any_estimated = False
    for s in included:
        ticker = (s.get("ticker") or "").upper()
        units = int(s.get("units") or 0)
        proceeds = s.get("net_proceeds_gbp")
        currency = s.get("currency") or ("USD" if ticker in US else "GBP")
        symbol = s.get("yahoo_symbol") or yahoo_symbol_for(ticker, currency)
        status = "confirmed" if str(s.get("status") or "").lower() == "confirmed" else "estimated"
        if status == "estimated":
            any_estimated = True
        row = {
            "date": s.get("date"),
            "ticker": ticker,
            "name": NAMES.get(ticker, ticker),
            "yahoo_symbol": symbol,
            "units": units,
            "sale_price": s.get("sale_price"),
            "sale_price_display": format_px(ticker, s["sale_price"], currency) if s.get("sale_price") is not None else None,
            "currency": currency,
            "sale_fx_gbpusd": s.get("fx_gbpusd"),
            "net_proceeds_gbp": money(proceeds) if proceeds is not None else None,
            "status": status,
            "current_price": None,
            "current_price_display": None,
            "current_price_date": None,
            "current_fx_gbpusd": out["fx_gbpusd"] if (currency.upper() == "USD" or ticker in US) else None,
            "current_value_gbp": None,
            "difference_gbp": None,
            "difference_pct": None,
            "skipped": False,
            "skip_reason": None,
        }
        reason = None
        if units <= 0:
            reason = "units missing"
        elif proceeds is None:
            reason = "net proceeds missing"
        px = close_on_or_before(charts.get(symbol, {}).get("rows") or [], as_of_d)
        if not reason and not px:
            reason = (charts.get(symbol) or {}).get("error") or f"no {symbol} close on or before {as_of_d}"
        fx = out["fx_gbpusd"] if (currency.upper() == "USD" or ticker in US) else None
        if not reason and (currency.upper() == "USD" or ticker in US) and not fx:
            reason = "GBP/USD close unavailable"
        if reason:
            row["skipped"] = True
            row["skip_reason"] = reason
            out["missing"].append({"ticker": ticker, "reason": reason})
            out["complete"] = False
            out["sales"].append(row)
            continue
        px_close = round(px[1], 2)
        value = value_at_yahoo_close(ticker, units, px_close, fx)
        if value is None:
            row["skipped"] = True
            row["skip_reason"] = "could not convert the close into GBP"
            out["missing"].append({"ticker": ticker, "reason": row["skip_reason"]})
            out["complete"] = False
            out["sales"].append(row)
            continue
        row["current_price"] = px_close
        row["current_price_display"] = format_px(ticker, px_close, yahoo=True)
        row["current_price_date"] = px[0].isoformat()
        row["current_value_gbp"] = money(value)
        row["difference_gbp"] = money(value - float(proceeds))
        row["difference_pct"] = round((value - float(proceeds)) / float(proceeds) * 100, 2) if float(proceeds) else None
        proceeds_sum += float(proceeds)
        current_sum += value
        priced += 1
        out["sales"].append(row)

    if priced and actual_pot is not None:
        out["totals"] = {"proceeds_gbp": money(proceeds_sum), "current_value_gbp": money(current_sum)}
        # Had I not sold = actual pot, take out the cash that the sales brought in, put the units back at today's close.
        counterfactual = float(actual_pot) - proceeds_sum + current_sum
        out["had_i_not_sold"] = money(counterfactual)
        out["gap_gbp"] = money(float(actual_pot) - counterfactual)  # positive = selling has helped
        out["gap_pct"] = round((float(actual_pot) - counterfactual) / counterfactual * 100, 2) if counterfactual else None
        bits = []
        if not out["complete"]:
            skipped = ", ".join(f"{m['ticker']} ({m['reason']})" for m in out["missing"] if m["ticker"] != "GBPUSD=X")
            bits.append("Excludes sales that could not be priced: " + (skipped or "see missing") + ".")
        if any_estimated:
            bits.append("Proceeds marked estimated are awaiting a contract note.")
        if out["fx_date"] and out["fx_date"] != as_of_d.isoformat():
            bits.append(f"GBP/USD close used is {out['fx_gbpusd']} on {out['fx_date']}.")
        out["note"] = " ".join(bits) or None
    elif actual_pot is not None and not priced:
        out["complete"] = False
        out["note"] = "No sale could be priced, so the comparison is unavailable rather than guessed."
    record_comparison_history(history_path, out)
    return out


def record_comparison_history(path, snapshot):
    """Write or replace the row for this as_of date in sold_comparison_history.json."""
    if not path:
        return
    hist = {"_note": "Daily 'had I not sold' snapshots written by update.py. Re-running the same as_of date replaces that row.", "rows": []}
    if os.path.exists(path):
        try:
            loaded = json.load(open(path, encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("rows"), list):
                hist = loaded
        except (json.JSONDecodeError, OSError):
            pass
    row = {k: snapshot[k] for k in ("as_of", "actual_pot", "had_i_not_sold", "gap_gbp", "gap_pct", "complete", "fx_gbpusd", "fx_date", "totals", "missing") if k in snapshot}
    row["sales"] = [
        {"date": s["date"], "ticker": s["ticker"], "units": s["units"],
         "net_proceeds_gbp": s["net_proceeds_gbp"], "current_value_gbp": s["current_value_gbp"],
         "current_price": s["current_price"], "current_price_date": s["current_price_date"],
         "difference_gbp": s["difference_gbp"], "difference_pct": s["difference_pct"],
         "status": s["status"], "skipped": s["skipped"], "skip_reason": s.get("skip_reason")}
        for s in snapshot.get("sales") or []
    ]
    rows = [r for r in hist.get("rows") or [] if r.get("as_of") != snapshot.get("as_of")]
    rows.append(row)
    rows.sort(key=lambda r: r.get("as_of") or "")
    hist["rows"] = rows
    json.dump(hist, open(path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    open(path, "a", encoding="utf-8").write("\n")



# ---------------------------------------------------------------- Picks vs VWRP
PICKS_LOTS_FILE = "picks_lots.json"
PICKS_HISTORY_FILE = "picks_vs_vwrp_history.json"
NOT_PICKS = {"VWRP", "IGLS", "SGLN"}
PROPOSED_RULE = "Proposed rule (not yet adopted): consider trimming the picks sleeve if behind VWRP 3 months running."


def _d(x):
    return x if isinstance(x, datetime.date) else datetime.date.fromisoformat(str(x)[:10])


def _month_end(d):
    nxt = (d.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
    return nxt - datetime.timedelta(days=1)


def _one_month_before(d):
    y, m = (d.year, d.month - 1) if d.month > 1 else (d.year - 1, 12)
    return datetime.date(y, m, min(d.day, _month_end(datetime.date(y, m, 1)).day))


def picks_vs_vwrp(as_of, lots_path, ledger_path, history_path=None, held=None):
    """Compare the picks sleeve with putting the same money into VWRP.

    Picks value on a date = units held (picks_lots.json buys less sold_ledger.json sales on or before
    that date) at Yahoo closes on or before it, plus net proceeds of picks sold on or before it.
    VWRP mirror over a window = the picks value at the start bought into VWRP at that day's close,
    plus each later picks buy (all-in £) bought into VWRP at its own buy-day close. Sale proceeds stay
    as cash in the picks figure; the mirror keeps holding VWRP. A ticker with a missing price is left
    out of BOTH sides and reported; nothing is guessed.
    """
    as_of_d = _d(as_of)
    out = {"as_of": as_of_d.isoformat(), "baseline_date": None, "complete": True, "missing": [],
           "rolling_1m": None, "since_baseline": None, "months": [], "consecutive_months_behind": None,
           "current_month_behind": None, "proposed_rule": PROPOSED_RULE, "proposed_rule_adopted": False,
           "holdings_check": [], "note": None}
    if not lots_path or not os.path.exists(lots_path):
        out["complete"] = False
        out["note"] = "picks_lots.json not found, so the check is unavailable rather than guessed."
        return out
    cfg = json.load(open(lots_path, encoding="utf-8"))
    base = _d(cfg["baseline_date"])
    out["baseline_date"] = base.isoformat()
    lots = [l for l in cfg.get("lots") or [] if (l.get("ticker") or "").upper() not in NOT_PICKS]
    sales = [s for s in load_sold_ledger(ledger_path) if (s.get("ticker") or "").upper() not in NOT_PICKS]
    tickers = sorted({(x.get("ticker") or "").upper() for x in lots + sales})
    days = (datetime.date.today() - base).days
    rng = "6mo" if days < 170 else ("1y" if days < 350 else ("2y" if days < 700 else "5y"))
    charts = {}
    for sym in dict.fromkeys([yahoo_symbol_for(t) for t in tickers] + ["VWRP.L", FX_SYMBOL]):
        charts[sym] = fetch_yahoo_chart(sym, rng)
    missing = {}

    def px(sym, d):
        r = close_on_or_before(charts.get(sym, {}).get("rows") or [], d)
        if not r:
            missing.setdefault(sym, (charts.get(sym) or {}).get("error") or f"no {sym} close on or before {d}")
        return r

    def units(t, d):
        u = sum(int(l["units"]) for l in lots if l["ticker"].upper() == t and _d(l["date"]) <= d)
        return u - sum(int(s.get("units") or 0) for s in sales if s["ticker"].upper() == t and _d(s["date"]) <= d)

    def proceeds(t, d):
        tot = 0.0
        for s in sales:
            if s["ticker"].upper() == t and _d(s["date"]) <= d:
                if s.get("net_proceeds_gbp") is None:
                    return None
                tot += float(s["net_proceeds_gbp"])
        return tot

    def tvalue(t, d):
        """£ value of ticker t's picks money on date d (shares + sale cash), or None if unpriced."""
        u = units(t, d)
        p = proceeds(t, d)
        if p is None:
            return None
        if u == 0:
            return p
        r = px(yahoo_symbol_for(t), d)
        if not r:
            return None
        fx = None
        if t in US:
            f = px(FX_SYMBOL, d)
            if not f:
                return None
            fx = f[1]
        v = value_at_yahoo_close(t, u, round(r[1], 2), fx)
        return None if v is None else v + p

    def window(start, end, label):
        start, end = _d(start), _d(end)
        vs, ve = px("VWRP.L", start), px("VWRP.L", end)
        res = {"label": label, "start": start.isoformat(), "end": end.isoformat(),
               "picks_start": None, "buys_in_window": None, "money_in": None, "picks_end": None, "vwrp_end": None,
               "picks_return_pct": None, "vwrp_return_pct": None, "gap_gbp": None, "gap_pct": None,
               "behind": None, "excluded": [], "complete": True}
        if not vs or not ve:
            res["complete"] = False
            res["excluded"].append("VWRP.L")
            return res
        p_start = p_end = flows = mirror_units = 0.0
        for t in tickers:
            s_v, e_v = tvalue(t, start), tvalue(t, end)
            fl = [(l, px("VWRP.L", _d(l["date"]))) for l in lots
                  if l["ticker"].upper() == t and start < _d(l["date"]) <= end]
            bad = s_v is None or e_v is None or any(l.get("cost_gbp") is None or not r for l, r in fl)
            if bad:
                res["excluded"].append(t)
                res["complete"] = False
                continue
            p_start += s_v
            p_end += e_v
            mirror_units += s_v / vs[1]
            for l, r in fl:
                flows += float(l["cost_gbp"])
                mirror_units += float(l["cost_gbp"]) / r[1]
        money_in = p_start + flows
        mirror_end = mirror_units * ve[1]
        res.update({"picks_start": money(p_start), "buys_in_window": money(flows), "money_in": money(money_in),
                    "picks_end": money(p_end), "vwrp_end": money(mirror_end),
                    "picks_return_pct": round((p_end / money_in - 1) * 100, 2) if money_in else None,
                    "vwrp_return_pct": round((mirror_end / money_in - 1) * 100, 2) if money_in else None,
                    "gap_gbp": money(p_end - mirror_end),
                    "gap_pct": round((p_end - mirror_end) / mirror_end * 100, 2) if mirror_end else None,
                    "behind": (p_end - mirror_end) < 0,
                    "vwrp_start_close": round(vs[1], 2), "vwrp_end_close": round(ve[1], 2),
                    "vwrp_start_date": vs[0].isoformat(), "vwrp_end_date": ve[0].isoformat()})
        return res

    if as_of_d <= base:
        out["note"] = "As-of date is on or before the baseline, so there is nothing to compare yet."
        return out
    out["since_baseline"] = window(base, as_of_d, f"Since {base.strftime('%-d %b %Y')}")
    m_start = max(base, _one_month_before(as_of_d))
    out["rolling_1m"] = window(m_start, as_of_d, "Rolling 1 month")
    m = base.replace(day=1)
    while m <= as_of_d:
        s = max(base, m - datetime.timedelta(days=1))
        e = min(_month_end(m), as_of_d)
        w = window(s, e, m.strftime("%b %Y"))
        w["month"] = m.strftime("%Y-%m")
        w["month_complete"] = _month_end(m) < as_of_d or (_month_end(m) == as_of_d)
        w["partial_start"] = s == base and base.day > 1
        out["months"].append(w)
        m = _month_end(m) + datetime.timedelta(days=1)
    run = 0
    for w in reversed([w for w in out["months"] if w["month_complete"]]):
        if w["behind"]:
            run += 1
        else:
            break
    out["consecutive_months_behind"] = run
    cur = [w for w in out["months"] if not w["month_complete"]]
    out["current_month_behind"] = cur[-1]["behind"] if cur else None
    # Cross-check lots against the holdings table so a missed buy shows up rather than skewing the figures.
    if held:
        for t in tickers:
            have = next((h.get("qty") for h in held if (h.get("ticker") or "").upper().replace(".L", "") in (t, "REL" if t == "RELX" else t)), 0)
            try:
                have = int(float(str(have).replace(",", ""))) if have is not None else 0
            except ValueError:
                have = None
            if have is not None and have != units(t, as_of_d):
                out["holdings_check"].append({"ticker": t, "lots_units": units(t, as_of_d), "holdings_units": have})
    for sym, why in missing.items():
        out["missing"].append({"ticker": sym, "reason": why})
    out["complete"] = not out["missing"] and all((w or {}).get("complete", True) for w in [out["since_baseline"], out["rolling_1m"]] + out["months"])
    bits = []
    if out["missing"]:
        bits.append("Left out of both sides because a price was missing: " + ", ".join(m["ticker"] for m in out["missing"]) + ".")
    if any(str(s.get("status") or "").lower() != "confirmed" for s in sales if _d(s["date"]) <= as_of_d):
        bits.append("Sale proceeds marked estimated are awaiting a contract note.")
    if out["holdings_check"]:
        bits.append("Lots file and holdings table disagree for " + ", ".join(f"{c['ticker']} ({c['lots_units']} vs {c['holdings_units']})" for c in out["holdings_check"]) + "; add the missing buy or sale.")
    out["note"] = " ".join(bits) or None
    record_picks_history(history_path, out)
    return out


def record_picks_history(path, snap):
    """Write or replace this as_of row in picks_vs_vwrp_history.json."""
    if not path:
        return
    hist = {"_note": "Nightly 'Picks vs VWRP' snapshots written by update.py. Re-running the same as_of date replaces that row. " + PROPOSED_RULE, "rows": []}
    if os.path.exists(path):
        try:
            loaded = json.load(open(path, encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("rows"), list):
                hist = loaded
        except (json.JSONDecodeError, OSError):
            pass
    keep = ("label", "start", "end", "money_in", "picks_end", "vwrp_end", "picks_return_pct", "vwrp_return_pct", "gap_gbp", "gap_pct", "behind", "complete")
    slim = lambda w: {k: w.get(k) for k in keep} if w else None
    row = {"as_of": snap["as_of"], "complete": snap["complete"],
           "rolling_1m": slim(snap.get("rolling_1m")), "since_baseline": slim(snap.get("since_baseline")),
           "months": [dict(slim(w), month=w.get("month"), month_complete=w.get("month_complete")) for w in snap.get("months") or []],
           "consecutive_months_behind": snap.get("consecutive_months_behind"),
           "current_month_behind": snap.get("current_month_behind"), "missing": snap.get("missing")}
    rows = [r for r in hist.get("rows") or [] if r.get("as_of") != snap["as_of"]]
    rows.append(row)
    rows.sort(key=lambda r: r.get("as_of") or "")
    hist["rows"] = rows
    json.dump(hist, open(path, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    open(path, "a", encoding="utf-8").write("\n")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", required=True); ap.add_argument("--report", required=True)
    ap.add_argument("--brief"); ap.add_argument("--out", default="data.json")
    ap.add_argument("--executed", help="JSON of trades already placed (default: executed.json next to update.py)")
    ap.add_argument("--charges", help="AJ Bell published charges JSON (default: ajbell_charges.json next to update.py)")
    ap.add_argument("--ledger", help="sold-holdings ledger for the had-I-not-sold comparison (default: sold_ledger.json next to update.py)")
    a = ap.parse_args()
    h = parse_history(a.history)
    diary = parse_brief(a.brief) if a.brief else []
    data = {"as_of": h["date"], "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "source": {"history": a.history.split("/")[-1], "report": a.report.split("/")[-1],
                       "holdings": ((re.search(r"Holdings = ([^(]*?Portfolio\.md)", h["note"]) or [None,"latest Portfolio.md"])[1]) + " (via history note)",
                       "prices": "Yahoo Finance closing prices as recorded in the history note"},
            "pot": {"total": h["total"], "invested": h["invested"], "cash": h["cash"], "fx_gbpusd": h["fx"]},
            "changes": h["changes"], "holdings": h["holdings"], "holdings_check": h["holdings_check"],
            "sleeves": h["sleeves"], "diary": diary or h["diary_from_history"],
            "diary_source": "latest SIPP brief email" if diary else "history note",
            "advice": parse_report(a.report), "series": build_series(a.history),
            "commentary": (brief_commentary(a.brief) if a.brief else None) or commentary(h["note"])}
    apply_executed(data["advice"], a.executed)
    data["holdings"] = apply_executed_sales(data["holdings"], a.executed, data["as_of"], data["pot"]["total"])
    data["holdings_check"] = {
        "sum_of_values": round(sum(h["value"] for h in data["holdings"] if h["value"] is not None), 2),
        "invested_in_history": data["pot"]["invested"],
    }
    data["changes"] = headline_changes(data["series"], a.history)
    import os
    charges = a.charges or os.path.join(os.path.dirname(os.path.abspath(__file__)), "ajbell_charges.json")
    data["sell_to_cash"] = sell_to_cash(data["holdings"], charges)
    if data["sell_to_cash"] and data["sell_to_cash"].get("checked"):
        data["source"]["charges"] = "AJ Bell published charges, checked " + data["sell_to_cash"]["checked"]
    here = os.path.dirname(os.path.abspath(__file__))
    ledger = a.ledger or os.path.join(here, "sold_ledger.json")
    hist = os.path.join(os.path.dirname(os.path.abspath(a.out)) or here, HISTORY_FILE)
    data["had_i_not_sold"] = had_i_not_sold(data["pot"]["total"], data["as_of"], ledger, hist)
    data["source"]["sold_ledger"] = os.path.basename(ledger)
    if os.path.exists(hist):
        try:
            data["had_i_not_sold_history"] = json.load(open(hist, encoding="utf-8")).get("rows") or []
        except (json.JSONDecodeError, OSError):
            data["had_i_not_sold_history"] = []
    try:
        pv_hist = os.path.join(os.path.dirname(os.path.abspath(a.out)) or here, PICKS_HISTORY_FILE)
        data["picks_vs_vwrp"] = picks_vs_vwrp(data["as_of"], os.path.join(here, PICKS_LOTS_FILE), ledger, pv_hist, data["holdings"])
        data["source"]["picks_lots"] = PICKS_LOTS_FILE
        pv = data["picks_vs_vwrp"]
        for w in (pv.get("rolling_1m"), pv.get("since_baseline")):
            if w and w.get("gap_gbp") is not None:
                print(f"picks vs VWRP, {w['label']} ({w['start']} to {w['end']}): picks £{w['picks_end']:,.2f} vs VWRP £{w['vwrp_end']:,.2f} "
                      f"(gap {w['gap_gbp']:+,.2f} / {w['gap_pct']:+.2f}%)")
        print(f"picks behind VWRP {pv.get('consecutive_months_behind')} completed month(s) running; current month behind: {pv.get('current_month_behind')}")
    except Exception as e:  # never let this check break the nightly build
        data["picks_vs_vwrp"] = {"complete": False, "note": f"Picks vs VWRP unavailable this run: {e}", "proposed_rule": PROPOSED_RULE, "proposed_rule_adopted": False}
        print("picks vs VWRP: unavailable —", e)
    hins = data["had_i_not_sold"]
    if hins.get("had_i_not_sold") is not None and hins.get("actual_pot") is not None:
        gap = hins.get("gap_gbp")
        side = "helped by" if gap is not None and gap > 0 else ("cost" if gap is not None and gap < 0 else "unchanged at")
        amt = f"£{abs(gap):,.2f}" if gap else "£0.00"
        print(f"had I not sold: actual £{hins['actual_pot']:,.2f} vs £{hins['had_i_not_sold']:,.2f} (selling {side} {amt}"
              + (f" / {hins['gap_pct']:+.2f}%" if hins.get("gap_pct") is not None else "") + ")")
        for s in hins.get("sales") or []:
            if s.get("skipped"):
                print(f"  {s['ticker']}: skipped — {s.get('skip_reason')}")
            else:
                print(f"  {s['ticker']} {s['units']} now {s['current_price_display']} on {s['current_price_date']} = £{s['current_value_gbp']:,.2f} "
                      f"vs proceeds £{s['net_proceeds_gbp']:,.2f} ({s['difference_gbp']:+,.2f} / {s['difference_pct']:+.2f}%, {s['status']})")
    elif hins.get("missing"):
        print("had I not sold: unavailable — " + "; ".join(f"{m['ticker']}: {m['reason']}" for m in hins["missing"]))
    json.dump(data, open(a.out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    import os, base64
    scrub = lambda o: json.loads(re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "[email]", re.sub(r"\bACC[A-Z0-9]{4,}\b", "[account]", json.dumps(o, ensure_ascii=False))))
    data = scrub(data)
    root = os.path.dirname(os.path.abspath(a.out)); png = os.path.join(root, "chart.png")
    write_chart(data["series"], png)
    data["chart_png"] = "data:image/png;base64," + base64.b64encode(open(png, "rb").read()).decode()
    json.dump(data, open(a.out, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    tpl = os.path.join(os.path.dirname(os.path.abspath(__file__)), "template.html")
    html = open(tpl, encoding="utf-8").read().replace("/*DATA*/", json.dumps(data, ensure_ascii=False).replace("</", "<\\/"))
    open(os.path.join(os.path.dirname(os.path.abspath(a.out)), "index.html"), "w", encoding="utf-8").write(html)
    print("wrote", a.out, "as of", data["as_of"])
