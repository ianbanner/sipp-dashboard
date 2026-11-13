#!/usr/bin/env python3
"""Regenerate data.json for the SIPP dashboard.

Usage:
  python3 update.py --history "20261022 daily_history.csv" --report "20261016 2026-W42 Buy-Sell Analysis.md" \
      [--brief brief.txt] [--out data.json]

--history : newest daily_history.csv from Drive 08 Analysis Scripts
--report  : newest Buy-Sell Analysis .md from Drive 05 Weekly Reports (ignore Archive)
--brief   : optional plain-text body of the latest 'SIPP:' brief email (used for the diary)
Holdings quantities are read from the note in the latest history row ("Holdings = ... (VWRP 122, ...)").
Anything that cannot be parsed is written as null and shown as "unavailable". Nothing is invented.
"""
import argparse, csv, json, re, datetime

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
    The £ series uses the same contribution-adjusted change as the % series."""
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
        p = {"date": d}
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
    """Two-panel chart.png: % moves on top, the same moves in £ below.
    The nightly brief email embeds this file, so both panels go out automatically."""
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    import matplotlib.dates as mdates
    import matplotlib.ticker as mticker
    fig, axes = plt.subplots(2, 1, figsize=(8, 7.2), dpi=110, sharex=True)
    specs = (("day", "Day (24h)", "#1f4fd1"), ("week", "Rolling 1 week", "#0a7d3b"), ("month", "Rolling 1 month", "#b42318"))
    span = f"{series[0]['date']} to {series[-1]['date']} (contribution/transfer-adjusted)"
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
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"£{v:,.0f}"))
    axes[1].xaxis.set_major_formatter(mdates.DateFormatter("%d %b")); fig.autofmt_xdate()
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
    groups = {
        "us": {"id": "us", "label": "All US shares",
               "tickers": ["GOOG", "AMZN", "META", "MSFT", "NVDA"],
               "names": "Alphabet, Amazon, Meta, Microsoft, NVIDIA"},
        "uk": {"id": "uk", "label": "All UK-listed holdings",
               "tickers": ["HSBA", "RELX", "SHEL", "SGLN", "IGLS", "VWRP"],
               "names": "HSBC, RELX, Shell, SGLN, IGLS, VWRP"},
    }
    by = {h["ticker"]: h for h in holdings}
    out_groups = []
    complete = True
    for g in groups.values():
        items, gross, dealing, fx, ptm = [], 0.0, 0.0, 0.0, 0.0
        for t in g["tickers"]:
            h = by.get(t)
            if not h or h.get("value") is None:
                complete = False
                items.append({"ticker": t, "name": NAMES.get(t, t), "gross": None, "dealing": None,
                              "fx": None, "ptm": None, "costs": None, "net": None, "missing": True})
                continue
            val = float(h["value"])
            d = deal
            f = fx_charge(val, bands) if t in US else 0.0
            p = ptm_levy if (t in ptm_tickers and val > ptm_threshold) else 0.0
            costs = money(d + f + p)
            items.append({"ticker": t, "name": h.get("name") or NAMES.get(t, t), "gross": money(val),
                          "dealing": money(d), "fx": money(f), "ptm": money(p), "costs": costs,
                          "net": money(val - costs), "missing": False})
            gross += val; dealing += d; fx += f; ptm += p
        costs = money(dealing + fx + ptm)
        out_groups.append({
            "id": g["id"], "label": g["label"], "names": g["names"],
            "holdings": items, "deals": sum(1 for i in items if not i["missing"]),
            "gross": money(gross) if not any(i["missing"] for i in items) else None,
            "dealing": money(dealing), "fx": money(fx), "ptm": money(ptm),
            "costs": costs if not any(i["missing"] for i in items) else None,
            "net": money(gross - dealing - fx - ptm) if not any(i["missing"] for i in items) else None,
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

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", required=True); ap.add_argument("--report", required=True)
    ap.add_argument("--brief"); ap.add_argument("--out", default="data.json")
    ap.add_argument("--executed", help="JSON of trades already placed (default: executed.json next to update.py)")
    ap.add_argument("--charges", help="AJ Bell published charges JSON (default: ajbell_charges.json next to update.py)")
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
    data["changes"] = headline_changes(data["series"], a.history)
    import os
    charges = a.charges or os.path.join(os.path.dirname(os.path.abspath(__file__)), "ajbell_charges.json")
    data["sell_to_cash"] = sell_to_cash(data["holdings"], charges)
    if data["sell_to_cash"] and data["sell_to_cash"].get("checked"):
        data["source"]["charges"] = "AJ Bell published charges, checked " + data["sell_to_cash"]["checked"]
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
