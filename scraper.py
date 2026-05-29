"""
BSE Corporate Bond OTC Trade Scraper
Runs daily at EOD, filters 7 NBFC competitors, pushes to Supabase.
"""

import os
import json
import time
import requests
from datetime import datetime, date
from zoneinfo import ZoneInfo

# ── Supabase config (injected via GitHub Secrets) ────────────────────────────
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_KEY"]
SB_HEADERS = {
    "Content-Type": "application/json",
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Prefer": "return=minimal",
}

# ── Competitor keywords ───────────────────────────────────────────────────────
COMPETITORS = {
    "ES": ["EARLYSALARY", "EARLY SALARY", "EARLYSAL", "01YL"],
    "KB": ["KRAZYBEE", "KRAZY BEE", "07HK"],
    "AK": ["AKARA CAPITAL", "AKARA CAP", "ACAPL", "08XP"],
    "NF": ["NAVI FINSERV", "NAVI FIN", "342T"],
    "OX": ["OXYZO", "04VS"],
    "BH": ["BHANIX", "08X5"],
    "LK": ["LENDINGKART", "LENDING KART"],
}

def match_competitor(issuer: str, isin: str = "") -> str | None:
    text = (issuer + " " + isin).upper()
    for comp_id, keywords in COMPETITORS.items():
        if any(k in text for k in keywords):
            return comp_id
    return None


# ── BSE fetch ─────────────────────────────────────────────────────────────────
BSE_URL = "https://api.bseindia.com/BseIndiaAPI/api/DebentureOTCDSE/w"

# BSE requires these headers to accept requests
BSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.bseindia.com/",
    "Origin": "https://www.bseindia.com",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

# Fallback: scrape the HTML page directly
BSE_HTML_URL = "https://www.bseindia.com/markets/debt/debtsearch.aspx"

def fetch_bse_data() -> list[dict]:
    """
    Try BSE JSON API first. If that fails, fall back to HTML scraping.
    Returns list of raw row dicts.
    """
    rows = _fetch_via_api()
    if rows:
        print(f"[BSE API] Fetched {len(rows)} total rows")
        return rows

    print("[BSE API] Failed — trying HTML scrape...")
    rows = _fetch_via_html()
    print(f"[BSE HTML] Fetched {len(rows)} total rows")
    return rows


def _fetch_via_api() -> list[dict]:
    """Hit BSE's internal JSON endpoint."""
    try:
        session = requests.Session()
        # First hit the main page to get cookies
        session.get("https://www.bseindia.com/markets/debt/debtsearch.aspx",
                    headers=BSE_HEADERS, timeout=15)
        time.sleep(1)

        resp = session.get(BSE_URL, headers=BSE_HEADERS, timeout=20)
        resp.raise_for_status()
        data = resp.json()

        # BSE API returns list directly or wrapped in a key
        if isinstance(data, list):
            return data
        for key in ("Table", "data", "Data", "records"):
            if key in data and isinstance(data[key], list):
                return data[key]
        return []
    except Exception as e:
        print(f"[BSE API] Error: {e}")
        return []


def _fetch_via_html() -> list[dict]:
    """Scrape the BSE HTML page and parse the table."""
    try:
        from html.parser import HTMLParser

        session = requests.Session()
        resp = session.get(BSE_HTML_URL, headers=BSE_HEADERS, timeout=20)
        resp.raise_for_status()
        html = resp.text

        # Find the data table
        rows = []
        in_table = False
        current_row = []
        current_cell = ""
        is_header = False
        headers = []

        class TableParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.in_table = False
                self.in_row = False
                self.in_cell = False
                self.is_th = False
                self.rows = []
                self.headers = []
                self.current_row = []
                self.current_cell = ""

            def handle_starttag(self, tag, attrs):
                attrs_dict = dict(attrs)
                # Look for the OTC trades table
                if tag == "table":
                    cls = attrs_dict.get("class", "") + attrs_dict.get("id", "")
                    if any(k in cls.lower() for k in ["debt", "otc", "bond", "trade"]):
                        self.in_table = True
                if not self.in_table:
                    return
                if tag == "tr":
                    self.in_row = True
                    self.current_row = []
                if tag in ("td", "th"):
                    self.in_cell = True
                    self.is_th = tag == "th"
                    self.current_cell = ""

            def handle_endtag(self, tag):
                if not self.in_table:
                    return
                if tag in ("td", "th") and self.in_cell:
                    self.current_row.append(self.current_cell.strip())
                    self.in_cell = False
                if tag == "tr" and self.in_row:
                    if self.current_row:
                        if self.is_th or (not self.headers and any(
                            h in " ".join(self.current_row).upper()
                            for h in ["ISIN", "ISSUER", "COUPON"]
                        )):
                            self.headers = self.current_row
                        else:
                            self.rows.append(self.current_row)
                    self.in_row = False
                if tag == "table":
                    self.in_table = False

            def handle_data(self, data):
                if self.in_cell:
                    self.current_cell += data

        parser = TableParser()
        parser.feed(html)

        if not parser.headers or not parser.rows:
            # Try a simpler approach: find all table rows with ISIN pattern
            import re
            isin_pattern = re.compile(r'INE[A-Z0-9]{10}')
            rows_raw = re.findall(r'<tr[^>]*>(.*?)</tr>', html, re.DOTALL)
            result = []
            for row_html in rows_raw:
                cells = re.findall(r'<t[dh][^>]*>(.*?)</t[dh]>', row_html, re.DOTALL)
                cells_text = [re.sub(r'<[^>]+>', '', c).strip() for c in cells]
                if len(cells_text) >= 8 and any(isin_pattern.match(c) for c in cells_text):
                    result.append(cells_text)
            return _normalize_html_rows(result, [])

        return _normalize_html_rows(parser.rows, parser.headers)

    except Exception as e:
        print(f"[BSE HTML] Error: {e}")
        return []


def _normalize_html_rows(rows: list, headers: list) -> list[dict]:
    """Convert raw table rows into dict format matching BSE API structure."""
    # Try to map columns by header name
    col_map = {}
    for i, h in enumerate(headers):
        h_up = h.upper()
        if "ISIN" in h_up:             col_map["isin"] = i
        elif "ISSUER" in h_up:         col_map["issuer"] = i
        elif "SECURITY" in h_up:       col_map["secCode"] = i
        elif "COUPON" in h_up:         col_map["coupon"] = i
        elif "MATURITY" in h_up:       col_map["maturity"] = i
        elif "LTP" in h_up:            col_map["ltp"] = i
        elif "YIELD" in h_up:          col_map["waYield"] = i
        elif "PRICE" in h_up:          col_map["waPrice"] = i
        elif "TURNOVER" in h_up:       col_map["turnover"] = i
        elif "TRADE" in h_up:          col_map["trades"] = i

    result = []
    for row in rows:
        if len(row) < 4:
            continue
        # If no headers detected, assume BSE standard column order:
        # SecCode, ISIN, Issuer, Coupon, Maturity, LTP, WA_Price, WA_Yield, Turnover, Trades
        if not col_map:
            if len(row) >= 10:
                result.append({
                    "SecurityCode": row[0],
                    "ISIN":         row[1],
                    "Issuer":       row[2],
                    "CouponRate":   row[3],
                    "MaturityDate": row[4],
                    "LTP":          row[5],
                    "WAPrice":      row[6],
                    "WAYield":      row[7],
                    "Turnover":     row[8],
                    "NoOfTrades":   row[9],
                })
        else:
            def g(k): return row[col_map[k]] if k in col_map and col_map[k] < len(row) else ""
            result.append({
                "SecurityCode": g("secCode"),
                "ISIN":         g("isin"),
                "Issuer":       g("issuer"),
                "CouponRate":   g("coupon"),
                "MaturityDate": g("maturity"),
                "LTP":          g("ltp"),
                "WAPrice":      g("waPrice"),
                "WAYield":      g("waYield"),
                "Turnover":     g("turnover"),
                "NoOfTrades":   g("trades"),
            })
    return result


# ── Parse + filter ────────────────────────────────────────────────────────────
def parse_float(val) -> float | None:
    try:
        return float(str(val).replace(",", "").strip())
    except:
        return None

def parse_int(val) -> int | None:
    try:
        return int(str(val).replace(",", "").strip())
    except:
        return None

def filter_competitors(raw_rows: list[dict], trade_date: str) -> list[dict]:
    """Keep only rows matching our 7 competitors, normalise field names."""
    result = []
    for r in raw_rows:
        # Handle both API key variants
        issuer = str(r.get("Issuer") or r.get("IssuerName") or r.get("issuer") or "")
        isin   = str(r.get("ISIN")   or r.get("ISINNo")    or r.get("isin")   or "")

        comp = match_competitor(issuer, isin)
        if not comp:
            continue

        result.append({
            "trade_date": trade_date,
            "isin":       isin or None,
            "issuer":     issuer or None,
            "sec_code":   str(r.get("SecurityCode") or r.get("secCode") or ""),
            "coupon":     parse_float(r.get("CouponRate") or r.get("coupon")),
            "maturity":   str(r.get("MaturityDate") or r.get("maturity") or ""),
            "ltp":        parse_float(r.get("LTP") or r.get("ltp")),
            "wa_price":   parse_float(r.get("WAPrice") or r.get("waPrice") or r.get("wa_price")),
            "wa_yield":   parse_float(r.get("WAYield") or r.get("waYield") or r.get("wa_yield")),
            "turnover":   parse_float(r.get("Turnover") or r.get("turnover")),
            "trades":     parse_int(r.get("NoOfTrades") or r.get("trades")),
        })

    return result


# ── Supabase write ────────────────────────────────────────────────────────────
def push_to_supabase(rows: list[dict], trade_date: str):
    if not rows:
        print(f"[Supabase] No competitor rows to push for {trade_date}")
        return

    # Delete existing rows for this date first
    del_url = f"{SUPABASE_URL}/rest/v1/otc_trades?trade_date=eq.{trade_date}"
    try:
        r = requests.delete(del_url, headers=SB_HEADERS, timeout=15)
        print(f"[Supabase] Deleted existing rows for {trade_date} → {r.status_code}")
    except Exception as e:
        print(f"[Supabase] Delete warning (ok if first run): {e}")

    # Insert in chunks of 200
    inserted = 0
    for i in range(0, len(rows), 200):
        chunk = rows[i:i+200]
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/otc_trades",
            headers=SB_HEADERS,
            json=chunk,
            timeout=20,
        )
        if r.status_code in (200, 201, 204):
            inserted += len(chunk)
            print(f"[Supabase] Inserted chunk {i//200 + 1} → {len(chunk)} rows")
        else:
            print(f"[Supabase] Insert error {r.status_code}: {r.text[:300]}")

    print(f"[Supabase] Total inserted: {inserted} rows for {trade_date}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    ist = ZoneInfo("Asia/Kolkata")
    today = datetime.now(ist).date()
    trade_date = today.isoformat()  # "2026-05-18"

    # Skip weekends (BSE closed)
    if today.weekday() >= 5:
        print(f"[Scraper] {trade_date} is a weekend — skipping.")
        return

    print(f"[Scraper] Starting for {trade_date} (IST: {datetime.now(ist).strftime('%H:%M')})")

    raw = fetch_bse_data()
    if not raw:
        print("[Scraper] No data fetched from BSE — aborting.")
        return

    competitor_rows = filter_competitors(raw, trade_date)
    print(f"[Scraper] Found {len(competitor_rows)} competitor rows out of {len(raw)} total")

    push_to_supabase(competitor_rows, trade_date)
    print("[Scraper] Done.")


if __name__ == "__main__":
    main()
