"""
Scrapes the Harris County delinquent tax sale property listing page and
writes every property's details to a CSV.

Why no browser automation / clicking is needed:
The site renders every property's "detail panel" (account #, cause #,
judgment date, tax years, min bid, adjudged value, legal description,
status) directly into the page HTML when it loads - the "View Details"
click just expands a hidden <div> that's already there. So we just
download the page once and parse all of it.

Usage:
    pip install requests beautifulsoup4
    python scrape_hctax.py
    -> writes hctax_tax_sale_listings.csv in the current folder
"""

import csv
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

URL = "https://www.hctax.net/Property/listings/taxsalelisting"
OUTPUT_CSV = "hctax_tax_sale_listings.csv"

HEADERS = {
    # A normal browser User-Agent avoids some basic bot-blocking.
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

FIELDS = [
    "address",
    "precinct",
    "sale_number",
    "sale_type",
    "status",
    "account_number",
    "cause_number",
    "judgment_date",
    "tax_years",
    "minimum_bid",
    "adjudged_value",
    "sold_with_account",
    "legal_description",
    "google_maps_url",
]


def fetch_page(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text


def flatten_text(html: str) -> str:
    """Turn the page into one long whitespace-normalized text blob,
    the same way the labels appear visually on the page. This lets us
    regex out fields by their label text instead of depending on
    fragile/likely-obfuscated CSS class names."""
    soup = BeautifulSoup(html, "html.parser")
    # Remove script/style tags so their content doesn't pollute the text
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(separator=" ")
    text = re.sub(r"\s+", " ", text)
    return text


def split_into_property_blocks(flat_text: str):
    """Each property's info is announced by the words 'View Details' and
    closed by the word 'Close'. Split the flattened text on that basis."""
    # Only start capturing after the header/instructions section, which
    # ends right around 'Sort by Zip Code'
    start_marker = "Sort by Zip Code"
    idx = flat_text.find(start_marker)
    if idx != -1:
        flat_text = flat_text[idx + len(start_marker):]

    chunks = flat_text.split("View Details")
    blocks = []
    for chunk in chunks:
        if "Account#:" not in chunk:
            continue
        # Trim each block at its own "Close" marker so we don't bleed
        # into the next property.
        close_idx = chunk.find(" Close ")
        if close_idx != -1:
            chunk = chunk[: close_idx + len(" Close")]
        blocks.append(chunk.strip())
    return blocks


def _search(pattern, text, group=1, default=""):
    m = re.search(pattern, text)
    return m.group(group).strip() if m else default


def parse_block(block: str) -> dict:
    data = dict.fromkeys(FIELDS, "")

    # Address: the text right before "Precinct <n> / Type:"
    addr_match = re.search(r"^(.*?)\s+Precinct\s+(\d+)\s*/\s*Type:\s*(\S+)\s+(\d+)", block)
    if addr_match:
        data["address"] = addr_match.group(1).strip()
        data["precinct"] = addr_match.group(2).strip()
        data["sale_type"] = addr_match.group(3).strip()
        data["sale_number"] = addr_match.group(4).strip()

    # Status: the word right after the sale number, before "Account#:"
    status_match = re.search(r"Type:\s*\S+\s+\d+\s+(For Sale|Cancelled|Sold)", block)
    if status_match:
        data["status"] = status_match.group(1).strip()

    data["account_number"] = _search(r"Account#:\s*([\w-]+)", block)
    data["cause_number"] = _search(r"Cause#:\s*([\w-]+)", block)
    data["adjudged_value"] = _search(r"Adjudged Value:\s*(\$[\d,.]+)", block)
    data["minimum_bid"] = _search(r"Minimum Bid:\s*(\$[\d,.]+)", block)
    data["judgment_date"] = _search(r"Judgment:\s*([\d/]+)", block)
    data["tax_years"] = _search(r"Tax Years in Judgement:\s*([\w\s\-]+?)\s+Minimum Bid", block)
    data["sold_with_account"] = _search(r"Sold with Account#:?\s*([\w-]*)", block)

    # Legal description: everything between "Description" and "Close"
    desc_match = re.search(r"\bDescription\s+(.*?)\s+Close\b", block)
    if desc_match:
        data["legal_description"] = desc_match.group(1).strip()

    if data["address"]:
        query = requests.utils.quote(data["address"])
        data["google_maps_url"] = f"https://www.google.com/maps/search/?api=1&query={query}"

    return data


def main():
    print(f"Fetching {URL} ...")
    try:
        html = fetch_page(URL)
    except requests.RequestException as e:
        print(f"Failed to fetch page: {e}", file=sys.stderr)
        sys.exit(1)

    flat = flatten_text(html)
    blocks = split_into_property_blocks(flat)
    print(f"Found {len(blocks)} property listings.")

    rows = [parse_block(b) for b in blocks]

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows to {OUTPUT_CSV}")


if __name__ == "__main__":
    main()
