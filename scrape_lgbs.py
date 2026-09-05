"""
Scraper for taxsales.lgbs.com - Harris County tax sale properties.

Talks directly to the site's JSON API (found via browser DevTools):
    https://taxsales.lgbs.com/api/property_sales/

This bypasses the map page entirely, which also means the "agree to
terms" popup on the website is irrelevant here - that's purely a
front-end gate in the page's JavaScript, not something the API itself
enforces. A plain HTTP request with no cookies/session gets full data.

The one real obstacle: the API appears to throttle/block rapid repeat
requests (returns a bare 404 instead of a proper 429). So this script
paces itself with delays and retries with backoff instead of hammering
the endpoint.

Usage:
    pip install requests pandas
    python scrape_lgbs.py
    -> writes lgbs_harris_county_listings.csv
"""

import socket
import sys
import time

import requests
import pandas as pd

# Force IPv4 connections (fixes "Network is unreachable" on some cloud hosts)
_original_getaddrinfo = socket.getaddrinfo
def _ipv4_only_getaddrinfo(*args, **kwargs):
    responses = _original_getaddrinfo(*args, **kwargs)
    return [r for r in responses if r[0] == socket.AF_INET] or responses
socket.getaddrinfo = _ipv4_only_getaddrinfo

BASE = "https://taxsales.lgbs.com"
API_PATH = "/api/property_sales/"

# Bounding box from your original Harris County map URL (west, south, east, north).
# This box also slightly overlaps neighboring counties, so we filter to
# county == "HARRIS COUNTY" after fetching.
IN_BBOX = "-96.13910658743688,29.285296977727658,-94.72873671439001,30.380078608009892"

PARAMS = {
    "in_bbox": IN_BBOX,
    "ordering": "precinct,sale_nbr,uid",
    "sale_type": "SALE,RESALE,STRUCK OFF,FUTURE SALE",
    "offset": 0,
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": "https://taxsales.lgbs.com/map",
    "Origin": "https://taxsales.lgbs.com",
}

TARGET_COUNTY = "HARRIS COUNTY"
OUTPUT_CSV = "lgbs_harris_county_listings.csv"

# Pacing / retry settings - tune these up if you still get blocked.
DELAY_BETWEEN_PAGES = 2.5   # seconds
MAX_RETRIES = 5
RETRY_BACKOFF_BASE = 5      # seconds; doubles each retry


def get_with_retry(url, params=None):
    import socket
    
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, params=params, headers=HEADERS, timeout=20)
        except requests.exceptions.ConnectionError as e:
            print(f"  attempt {attempt}: connection error ({e})")
            # Try DNS resolution to help diagnose network issues
            try:
                hostname = url.split('/')[2]
                ip = socket.gethostbyname(hostname)
                print(f"  DNS resolved {hostname} to {ip}")
            except socket.gaierror as dns_err:
                print(f"  DNS resolution failed: {dns_err}")
        except requests.RequestException as e:
            print(f"  attempt {attempt}: request error ({e})")
        else:
            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError:
                    print(f"  attempt {attempt}: 200 but not valid JSON")
            else:
                print(f"  attempt {attempt}: HTTP {resp.status_code}")

        if attempt < MAX_RETRIES:
            wait = RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
            print(f"  retrying in {wait}s...")
            time.sleep(wait)

    return None


def fetch_all_harris_county():
    all_records = []
    url = BASE + API_PATH
    params = dict(PARAMS)

    print(f"Fetching page 1 ({url}) ...")
    payload = get_with_retry(url, params)
    if payload is None:
        print("Failed to fetch the first page after retries. Aborting.")
        sys.exit(1)

    total_count = payload.get("count")
    print(f"API reports {total_count} total records in this bounding box "
          f"(across all counties touching it).")

    page = 1
    while payload:
        results = payload.get("results", [])
        all_records.extend(results)
        print(f"Page {page}: got {len(results)} records "
              f"({len(all_records)} total so far)")

        next_url = payload.get("next")
        if not next_url:
            break

        time.sleep(DELAY_BETWEEN_PAGES)
        page += 1
        print(f"Fetching page {page} ...")
        payload = get_with_retry(next_url)
        if payload is None:
            print("Failed to fetch a later page after retries. "
                  "Stopping with what we have so far.")
            break

    return all_records


def main():
    records = fetch_all_harris_county()
    if not records:
        print("No records retrieved.")
        sys.exit(1)

    df = pd.json_normalize(records)

    # Split out lat/lon from the nested geometry.coordinates list, if present.
    if "geometry.coordinates" in df.columns:
        df["lon"] = df["geometry.coordinates"].apply(
            lambda c: c[0] if isinstance(c, list) and len(c) == 2 else None
        )
        df["lat"] = df["geometry.coordinates"].apply(
            lambda c: c[1] if isinstance(c, list) and len(c) == 2 else None
        )

    before = len(df)
    if "county" in df.columns:
        df = df[df["county"].str.upper() == TARGET_COUNTY]
    after = len(df)
    print(f"Filtered {before} bbox records down to {after} in {TARGET_COUNTY}.")

    df.to_csv(OUTPUT_CSV, index=False)
    print(f"\nWrote {len(df)} rows x {len(df.columns)} columns to {OUTPUT_CSV}")
    print("Columns:", list(df.columns))


if __name__ == "__main__":
    main()
