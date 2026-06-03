"""
BSE Corporate Bond OTC Scraper — Selenium headless browser version
Bypasses BSE's bot detection by rendering the page like a real browser.
"""

import os
import time
import json
import requests
from datetime import datetime
from zoneinfo import ZoneInfo

# ── Supabase config ───────────────────────────────────────────────────────────
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
    "ES": ["EARLYSALARY", "EARLY SALARY", "EARLYSAL"],
    "KB": ["KRAZYBEE", "KRAZY BEE"],
    "AK": ["AKARA CAPITAL", "AKARA CAP"],
    "NF": ["NAVI FINSERV"],
    "OX": ["OXYZO"],
    "BH": ["BHANIX"],
    "LK": ["LENDINGKART", "LENDING KART"],
}

def match_competitor(issuer: str) -> str | None:
    u = issuer.upper()
    for comp_id, kws in COMPETITORS.items():
        if any(k in u for k in kws):
            return comp_id
    return None


# ── Selenium fetch ────────────────────────────────────────────────────────────
def fetch_bse_data() -> list[dict]:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC

    print("[Selenium] Launching headless Chrome...")

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    options.add_argument(
        "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )

    from webdriver_manager.chrome import ChromeDriverManager
    from selenium.webdriver.chrome.service import Service as ChromeService
    driver = webdriver.Chrome(
        service=ChromeService(ChromeDriverManager().install()),
        options=options
    )
    driver.execute_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

    rows = []
    try:
        url = "https://www.bseindia.com/markets/debt/debtsearch.aspx"
        print(f"[Selenium] Loading {url}")
        driver.get(url)

        # Wait for the table to appear — up to 20 seconds
        wait = WebDriverWait(driver, 20)
        try:
            wait.until(EC.presence_of_element_located((By.TAG_NAME, "table")))
            print("[Selenium] Table found on page")
        except Exception:
            print("[Selenium] Table not found via wait — trying anyway")

        time.sleep(3)  # let JS finish rendering

        # Try to intercept the API call BSE makes internally
        # by reading the page source and looking for JSON data
        page_source = driver.page_source

        # Extract table rows from rendered HTML
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(page_source, "html.parser")

        # Find all tables and pick the one with bond data
        tables = soup.find_all("table")
        print(f"[Selenium] Found {len(tables)} tables on page")

        for table in tables:
            headers = [th.get_text(strip=True) for th in table.find_all("th")]
            header_text = " ".join(headers).upper()
            if not any(k in header_text for k in ["ISIN", "ISSUER", "COUPON", "YIELD"]):
                continue

            print(f"[Selenium] Matched table with headers: {headers[:5]}")
            col_map = map_columns(headers)

            for tr in table.find_all("tr")[1:]:
                cells = [td.get_text(strip=True) for td in tr.find_all("td")]
                if len(cells) < 5:
                    continue
                row = extract_row(cells, col_map)
                if row:
                    rows.append(row)
            break

        # Fallback: try BSE's internal API endpoint that the page calls via XHR
        if not rows:
            print("[Selenium] Table parse found 0 rows — trying XHR endpoint...")
            rows = try_xhr_endpoint(driver)

    finally:
        driver.quit()

    print(f"[Selenium] Total rows extracted: {len(rows)}")
    return rows


def try_xhr_endpoint(driver) -> list[dict]:
    """Execute fetch() inside the browser context to call BSE's internal API."""
    try:
        result = driver.execute_script("""
            const response = await fetch(
                'https://api.bseindia.com/BseIndiaAPI/api/DebentureOTCDSE/w',
                {
                    method: 'GET',
                    headers: {
                        'Accept': 'application/json, text/plain, */*',
                        'Referer': 'https://www.bseindia.com/',
                        'Origin': 'https://www.bseindia.com'
                    }
                }
            );
            const text = await response.text();
            return text;
        """)
        if result:
            data = json.loads(result)
            if isinstance(data, list):
                return data
            for key in ("Table", "data", "Data", "records"):
                if key in data and isinstance(data[key], list):
                    return data[key]
    except Exception as e:
        print(f"[XHR] Failed: {e}")
    return []


def map_columns(headers: list[str]) -> dict:
    """Map column names to indices."""
    col_map = {}
    for i, h in enumerate(headers):
        u = h.upper()
        if "ISIN" in u and "NO" in u:       col_map["isin"] = i
        elif "ISIN" in u:                    col_map["isin"] = i
        elif "ISSUER" in u:                  col_map["issuer"] = i
        elif "SECURITY" in u and "CODE" in u:col_map["secCode"] = i
        elif "COUPON" in u:                  col_map["coupon"] = i
        elif "MATURITY" in u:                col_map["maturity"] = i
        elif "LTP" in u:                     col_map["ltp"] = i
        elif "YIELD" in u:                   col_map["waYield"] = i
        elif "PRICE" in u:                   col_map["waPrice"] = i
        elif "TURNOVER" in u:                col_map["turnover"] = i
        elif "TRADE" in u:                   col_map["trades"] = i
    return col_map


def extract_row(cells: list[str], col_map: dict) -> dict | None:
    def g(k): return cells[col_map[k]] if k in col_map and col_map[k] < len(cells) else ""
    def pf(v):
        try: return float(str(v).replace(",", "").strip())
        except: return None
    def pi(v):
        try: return int(str(v).replace(",", "").strip())
        except: return None

    issuer = g("issuer")
    isin   = g("isin")
    if not issuer and not isin:
        return None

    return {
        "isin":     isin or None,
        "issuer":   issuer or None,
        "secCode":  g("secCode"),
        "coupon":   pf(g("coupon")),
        "maturity": g("maturity"),
        "ltp":      pf(g("ltp")),
        "waPrice":  pf(g("waPrice")),
        "waYield":  pf(g("waYield")),
        "turnover": pf(g("turnover")),
        "trades":   pi(g("trades")),
    }


# ── Filter competitors ────────────────────────────────────────────────────────
def filter_competitors(raw_rows: list[dict], trade_date: str) -> list[dict]:
    result = []
    for r in raw_rows:
        issuer = str(r.get("issuer") or r.get("Issuer") or r.get("IssuerName") or "")
        isin   = str(r.get("isin")   or r.get("ISIN")   or r.get("ISINNo")     or "")
        comp   = match_competitor(issuer) or match_competitor(isin)
        if not comp:
            continue
        result.append({
            "trade_date": trade_date,
            "isin":       isin or None,
            "issuer":     issuer or None,
            "sec_code":   str(r.get("secCode") or r.get("SecurityCode") or ""),
            "coupon":     r.get("coupon")   or r.get("CouponRate"),
            "maturity":   str(r.get("maturity") or r.get("MaturityDate") or ""),
            "ltp":        r.get("ltp")      or r.get("LTP"),
            "wa_price":   r.get("waPrice")  or r.get("WAPrice"),
            "wa_yield":   r.get("waYield")  or r.get("WAYield"),
            "turnover":   r.get("turnover") or r.get("Turnover"),
            "trades":     r.get("trades")   or r.get("NoOfTrades"),
        })
    return result


# ── Supabase write ────────────────────────────────────────────────────────────
def push_to_supabase(rows: list[dict], trade_date: str):
    if not rows:
        print(f"[Supabase] No competitor rows to push for {trade_date}")
        return

    # Delete existing rows for this date
    try:
        r = requests.delete(
            f"{SUPABASE_URL}/rest/v1/otc_trades?trade_date=eq.{trade_date}",
            headers=SB_HEADERS, timeout=15
        )
        print(f"[Supabase] Delete → {r.status_code}")
    except Exception as e:
        print(f"[Supabase] Delete warning: {e}")

    # Insert in chunks
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
            print(f"[Supabase] Chunk {i//200+1} → {len(chunk)} rows inserted")
        else:
            print(f"[Supabase] Insert error {r.status_code}: {r.text[:300]}")

    print(f"[Supabase] Done — {inserted} rows saved for {trade_date}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    ist     = ZoneInfo("Asia/Kolkata")
    today   = datetime.now(ist).date()
    trade_date = today.isoformat()

    if today.weekday() >= 5:
        print(f"[Scraper] Weekend — skipping.")
        return

    print(f"[Scraper] Running for {trade_date}")

    raw  = fetch_bse_data()
    comp = filter_competitors(raw, trade_date)
    print(f"[Scraper] {len(comp)} competitor rows from {len(raw)} total")
    push_to_supabase(comp, trade_date)
    print("[Scraper] Complete.")


if __name__ == "__main__":
    main()
