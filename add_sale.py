#!/usr/bin/env python3
"""Add a sale to sold_ledger.json for the 'had I not sold' comparison.

One holding:
  python3 add_sale.py --date 2026-11-23 --ticker AAPL --units 10 --price 230.1 \
      --currency USD --fx 1.31 --proceeds 1745.2 --estimated

Several sales on the same day (for example selling everything):
  python3 add_sale.py --batch monday_sales.json

A batch file is a JSON list of objects with the same fields as the flags
(date, ticker, units, price, currency, proceeds, and optionally yahoo_symbol,
fx, estimated/confirmed, filled, note). date can also be passed on the command
line and applied to every row that omits it.

Replacing an estimate with the contract-note figures (same date, ticker, units):
  python3 add_sale.py --date 2026-11-18 --ticker NVDA --units 27 --price 151.20 \
      --currency USD --fx 1.312 --proceeds 3071.40 --confirmed

Nothing here places a trade. Proceeds and prices are recorded exactly as given;
they are not invented or adjusted for charges.
"""
import argparse, json, os, re, sys

US = {"NVDA", "AMZN", "MSFT", "META", "GOOG", "AAPL", "AVGO", "AMD", "TSLA"}
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_LEDGER = os.path.join(HERE, "sold_ledger.json")


def yahoo_symbol_for(ticker, currency=None, explicit=None):
    if explicit:
        return explicit
    t = (ticker or "").upper()
    if t == "RELX":
        return "REL.L"
    if (currency or "").upper() == "USD" or t in US:
        return t
    return t + ".L"


def load_ledger(path):
    if not os.path.exists(path):
        return {"_note": "Sales used for the dashboard's 'had I not sold' comparison.", "sales": []}
    data = json.load(open(path, encoding="utf-8"))
    if "sales" not in data or not isinstance(data["sales"], list):
        data["sales"] = []
    return data


def money(v):
    return round(float(v) + 0.0, 2)


def parse_sale(raw, default_date=None, default_status=None):
    """Build one ledger sale from a dict of flag-style fields. Raises ValueError."""
    date = raw.get("date") or default_date
    ticker = (raw.get("ticker") or "").upper()
    if not date or not re.match(r"\d{4}-\d{2}-\d{2}$", str(date)):
        raise ValueError("a sale needs --date YYYY-MM-DD")
    if not ticker:
        raise ValueError("a sale needs --ticker")
    try:
        units = int(raw["units"])
    except (KeyError, TypeError, ValueError):
        raise ValueError(f"{ticker or 'sale'} needs --units (a whole number)")
    if units <= 0:
        raise ValueError(f"{ticker} --units must be positive")
    if raw.get("price") is None:
        raise ValueError(f"{ticker} needs --price (the fill price, not invented)")
    if raw.get("proceeds") is None and raw.get("net_proceeds_gbp") is None:
        raise ValueError(f"{ticker} needs --proceeds (net GBP; not invented)")
    raw_cur = (raw.get("currency") or "").strip()
    u = raw_cur.upper()
    if not u:
        currency = "USD" if ticker in US else "GBP"
    elif u in ("GBX", "GBPENCE", "PENCE", "P") or raw_cur in ("GBp", "GBx"):
        currency = "GBp"
    else:
        currency = u
    fx = raw.get("fx") if raw.get("fx") is not None else raw.get("fx_gbpusd")
    if currency == "USD" and fx is None:
        raise ValueError(f"{ticker} is USD so it needs --fx (GBP/USD at the sale)")
    status = raw.get("status")
    if status is None:
        if raw.get("confirmed") or default_status == "confirmed":
            status = "confirmed"
        else:
            status = "estimated"
    status = "confirmed" if str(status).lower() in ("confirmed", "confirm", "contract") else "estimated"
    sale = {
        "date": str(date),
        "ticker": ticker,
        "yahoo_symbol": yahoo_symbol_for(ticker, currency, raw.get("yahoo_symbol") or raw.get("yahoo")),
        "units": units,
        "sale_price": float(raw["price"] if raw.get("price") is not None else raw["sale_price"]),
        "currency": currency,
        "net_proceeds_gbp": money(raw.get("proceeds") if raw.get("proceeds") is not None else raw["net_proceeds_gbp"]),
        "status": status,
    }
    if fx is not None:
        sale["fx_gbpusd"] = float(fx)
    if raw.get("filled"):
        sale["filled"] = raw["filled"]
    if raw.get("note"):
        sale["note"] = raw["note"]
    return sale


def same_sale(a, b):
    return a["date"] == b["date"] and a["ticker"] == b["ticker"] and int(a["units"]) == int(b["units"])


def upsert(sales, sale):
    for i, existing in enumerate(sales):
        if same_sale(existing, sale):
            sales[i] = sale
            return "updated"
    sales.append(sale)
    return "added"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", help="Sale date YYYY-MM-DD (UK trade date)")
    ap.add_argument("--ticker")
    ap.add_argument("--units", type=int)
    ap.add_argument("--price", type=float, help="Fill price in the sale currency (do not invent)")
    ap.add_argument("--currency", help="USD, GBP or GBp (pence)")
    ap.add_argument("--fx", type=float, help="GBP/USD at the sale, required for USD")
    ap.add_argument("--proceeds", type=float, help="Net proceeds in GBP after charges (do not invent)")
    ap.add_argument("--yahoo", dest="yahoo_symbol", help="Yahoo symbol if it is not the usual default")
    ap.add_argument("--filled", help="Optional free-text fill time, as in executed.json")
    ap.add_argument("--note")
    ap.add_argument("--estimated", action="store_true", help="Mark as estimated (the default)")
    ap.add_argument("--confirmed", action="store_true", help="Mark as confirmed from a contract note")
    ap.add_argument("--batch", help="JSON file with a list of sales (sell-everything / several on one day)")
    ap.add_argument("--ledger", default=DEFAULT_LEDGER, help="Path to sold_ledger.json")
    a = ap.parse_args()

    if a.estimated and a.confirmed:
        sys.exit("use only one of --estimated or --confirmed")
    default_status = "confirmed" if a.confirmed else "estimated"

    rows = []
    if a.batch:
        payload = json.load(open(a.batch, encoding="utf-8"))
        if isinstance(payload, dict):
            payload = payload.get("sales") or payload.get("fills") or [payload]
        if not isinstance(payload, list) or not payload:
            sys.exit(f"{a.batch} must be a JSON list of sales")
        for raw in payload:
            raw = dict(raw)
            if a.ticker or a.units or a.price is not None or a.proceeds is not None:
                sys.exit("when using --batch, do not also pass a single sale's ticker/units/price/proceeds")
            try:
                rows.append(parse_sale(raw, default_date=a.date, default_status=default_status))
            except (ValueError, KeyError) as e:
                sys.exit(str(e))
    else:
        if not all([a.date, a.ticker, a.units, a.price is not None, a.proceeds is not None]):
            sys.exit("need --date --ticker --units --price --proceeds, or --batch")
        try:
            rows.append(parse_sale({
                "date": a.date, "ticker": a.ticker, "units": a.units, "price": a.price,
                "currency": a.currency, "fx": a.fx, "proceeds": a.proceeds,
                "yahoo_symbol": a.yahoo_symbol, "filled": a.filled, "note": a.note,
                "status": default_status,
            }))
        except ValueError as e:
            sys.exit(str(e))

    data = load_ledger(a.ledger)
    actions = []
    for sale in rows:
        actions.append((upsert(data["sales"], sale), sale))
    data["sales"].sort(key=lambda s: (s["date"], s["ticker"]))
    json.dump(data, open(a.ledger, "w", encoding="utf-8"), indent=2, ensure_ascii=False)
    open(a.ledger, "a", encoding="utf-8").write("\n")
    for action, sale in actions:
        fx = f" fx {sale['fx_gbpusd']}" if sale.get("fx_gbpusd") is not None else ""
        print(f"{action} {sale['date']} {sale['ticker']} {sale['units']} @ {sale['sale_price']} {sale['currency']}{fx} "
              f"net £{sale['net_proceeds_gbp']:,.2f} ({sale['status']}) -> {a.ledger}")
    print("Run update.py to refresh the dashboard comparison. Nothing here places a trade.")


if __name__ == "__main__":
    main()
