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
    """Day / rolling-week / rolling-month % moves for each date in the last `days` days.
    Uses the last row per date. change = total - anchor_total - contributions after the anchor;
    pct = change / anchor_total (same as the brief). 24h anchor = previous dated row;
    1wk anchor = latest row dated <= date-7d; 1mo anchor = latest row dated <= date-30d.
    A series is skipped (None) when no anchor exists. Only recorded values are used."""
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
        return round(ch / num(a["total"]) * 100, 2), anchor_date
    def anchor(i, lag):
        lim = D(dates[i]) - datetime.timedelta(days=lag)
        c = [d for d in dates[:i] if D(d) <= lim]
        return c[-1] if c else None
    last = D(dates[-1]); out = []
    for i, d in enumerate(dates):
        if D(d) < last - datetime.timedelta(days=days) or i == 0: continue
        p = {"date": d}
        for k, a in (("day", dates[i-1]), ("week", anchor(i, 7)), ("month", anchor(i, 30))):
            p[k], p[k + "_anchor"] = move(i, a) if a else (None, None)
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
        if c: span += f" · adjusted for the £{c:,.2f} {'L&G transfer' if c > 50000 else 'contribution'}; % is on the £{at:,.2f} pot before it"
        out.append({"label": label, "span": span, "change": ch, "pct": round(ch / at * 100, 2)})
    return out

def commentary(note):
    m = re.search(r"Day: [^.]*\.\s+(.*?)(?:\s+Diary:|\s+Email|$)", note)
    return m.group(1).strip() if m and m.group(1).strip() else None

def write_chart(series, png):
    import matplotlib; matplotlib.use("Agg"); import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(8, 3.6), dpi=110)
    for k, lab, col in (("day", "Day (24h)", "#1f4fd1"), ("week", "Rolling 1 week", "#0a7d3b"), ("month", "Rolling 1 month", "#b42318")):
        pts = [(datetime.date.fromisoformat(p["date"]), p[k]) for p in series if p[k] is not None]
        if pts: ax.plot(*zip(*pts), marker="o", ms=3, lw=1.8, label=lab, color=col)
    ax.axhline(0, color="#999", lw=0.8); ax.set_ylabel("% move"); ax.legend(fontsize=8, frameon=False)
    ax.grid(alpha=.3); ax.set_title(f"SIPP moves, {series[0]['date']} to {series[-1]['date']} (contribution/transfer-adjusted)", fontsize=10)
    import matplotlib.dates as mdates; ax.xaxis.set_major_formatter(mdates.DateFormatter("%d %b")); fig.autofmt_xdate()
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
        c = [x.strip() for x in line.strip().strip("|").split("|")]
        if hdr is None: hdr = [x.lower() for x in c]; continue
        row = dict(zip(hdr, c))
        act = row.get("action", "").replace("**", "").upper()
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

def brief_commentary(path):
    t = open(path, encoding="utf-8").read().split("Diary - what's coming")[0]
    paras = [p.strip() for p in re.split(r"\n\s*\n", t) if p.strip()]
    paras = [p for p in paras if not re.match(r"(Chart|Dashboard|SIPP brief|Pot:|24 hours|Cash )", p) and "http" not in p]
    return " ".join(paras[-1].split()) if paras else None

def parse_brief(path):
    t = open(path, encoding="utf-8").read()
    m = re.search(r"Diary - what's coming\n((?:- .*\n?)+)", t)
    return [l[2:].strip() for l in m.group(1).splitlines()] if m else []

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", required=True); ap.add_argument("--report", required=True)
    ap.add_argument("--brief"); ap.add_argument("--out", default="data.json")
    a = ap.parse_args()
    h = parse_history(a.history)
    diary = parse_brief(a.brief) if a.brief else []
    data = {"as_of": h["date"], "generated": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "source": {"history": a.history.split("/")[-1], "report": a.report.split("/")[-1],
                       "holdings": "20261010 Portfolio.md (via history note)",
                       "prices": "Yahoo Finance closing prices as recorded in the history note"},
            "pot": {"total": h["total"], "invested": h["invested"], "cash": h["cash"], "fx_gbpusd": h["fx"]},
            "changes": h["changes"], "holdings": h["holdings"], "holdings_check": h["holdings_check"],
            "sleeves": h["sleeves"], "diary": diary or h["diary_from_history"],
            "diary_source": "latest SIPP brief email" if diary else "history note",
            "advice": parse_report(a.report), "series": build_series(a.history),
            "commentary": (brief_commentary(a.brief) if a.brief else None) or commentary(h["note"])}
    data["changes"] = headline_changes(data["series"], a.history)
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
