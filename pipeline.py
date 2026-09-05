"""Run scrapers and merge matching addresses across listing sources."""

import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent

LGBS_SCRIPT = ROOT / "scrape_lgbs.py"
LGBS_CSV = ROOT / "lgbs_harris_county_listings.csv"
HCTAX_SCRIPT = ROOT / "scrape_hctax.py"
HCTAX_CSV = ROOT / "hctax_tax_sale_listings.csv"


def normalize_address(address: str) -> str:
    if not address or not isinstance(address, str):
        return ""
    text = address.upper().strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[^\w\s#]", "", text)
    return text


def run_scraper(script: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        stderr = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError(f"{script.name} failed: {stderr}")


def run_lgbs() -> pd.DataFrame:
    run_scraper(LGBS_SCRIPT)
    return pd.read_csv(LGBS_CSV)


def run_hctax() -> pd.DataFrame:
    run_scraper(HCTAX_SCRIPT)
    return pd.read_csv(HCTAX_CSV)


def lgbs_to_standard(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(
        {
            "source": "lgbs",
            "address": df.get("prop_address_one", "").fillna("").astype(str),
            "city": df.get("prop_city", "HOUSTON").fillna("HOUSTON").astype(str),
            "state": df.get("prop_state", "TX").fillna("TX").astype(str),
            "zip": df.get("prop_zipcode", "")
            .fillna("")
            .astype(str)
            .str.replace(r"\.0$", "", regex=True),
            "account_number": df.get("account_nbr", "").fillna("").astype(str),
            "cause_number": df.get("cause_nbr", "").fillna("").astype(str),
            "minimum_bid": df.get("minimum_bid", "").fillna("").astype(str),
            "adjudged_value": df.get("value", "").fillna("").astype(str),
            "status": df.get("status", "").fillna("").astype(str),
            "sale_type": df.get("sale_type", "").fillna("").astype(str),
            "precinct": df.get("precinct", "").fillna("").astype(str),
            "sale_number": df.get("sale_nbr", "").fillna("").astype(str),
            "legal_description": "",
        }
    )
    out["normalized_address"] = out["address"].map(normalize_address)
    return out


def hctax_to_standard(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(
        {
            "source": "hctax",
            "address": df.get("address", "").fillna("").astype(str),
            "city": "HOUSTON",
            "state": "TX",
            "zip": "",
            "account_number": df.get("account_number", "").fillna("").astype(str),
            "cause_number": df.get("cause_number", "").fillna("").astype(str),
            "minimum_bid": df.get("minimum_bid", "").fillna("").astype(str),
            "adjudged_value": df.get("adjudged_value", "").fillna("").astype(str),
            "status": df.get("status", "").fillna("").astype(str),
            "sale_type": df.get("sale_type", "").fillna("").astype(str),
            "precinct": df.get("precinct", "").fillna("").astype(str),
            "sale_number": df.get("sale_number", "").fillna("").astype(str),
            "legal_description": df.get("legal_description", "").fillna("").astype(str),
        }
    )
    out["normalized_address"] = out["address"].map(normalize_address)
    return out


def scrape_lgbs_only() -> pd.DataFrame:
    return lgbs_to_standard(run_lgbs()).drop(columns=["normalized_address"])


def scrape_hctax_only() -> pd.DataFrame:
    return hctax_to_standard(run_hctax()).drop(columns=["normalized_address"])


def scrape_all_combined() -> pd.DataFrame:
    from merge_matching import merge_matching_addresses

    with ThreadPoolExecutor(max_workers=2) as pool:
        lgbs_future = pool.submit(run_lgbs)
        hctax_future = pool.submit(run_hctax)
        lgbs_df = lgbs_future.result()
        hctax_df = hctax_future.result()

    lgbs_count = len(lgbs_df)
    hctax_count = len(hctax_df)

    if lgbs_count > 0 and hctax_count > 0:
        result = merge_matching_addresses(
            hctax_df=hctax_df,
            lgbs_df=lgbs_df,
            output_csv=None,
        )
        unique = result.drop_duplicates(subset=["address", "city", "state", "zip"])
        result.attrs["mode"] = "matching"
        result.attrs["matching_rows"] = len(result)
        result.attrs["unique_matching_addresses"] = len(unique)
        if len(result) == 0:
            result.attrs["notice"] = (
                "Both sources returned listings, but no matching addresses were found."
            )
        return result

    if lgbs_count > 0:
        result = lgbs_to_standard(lgbs_df).drop(columns=["normalized_address"])
        result.attrs["mode"] = "fallback"
        result.attrs["fallback_source"] = "LGBS"
        result.attrs["notice"] = (
            "HCTax returned no listings, so matching was skipped. "
            "Returning LGBS data only."
        )
        return result

    if hctax_count > 0:
        result = hctax_to_standard(hctax_df).drop(columns=["normalized_address"])
        result.attrs["mode"] = "fallback"
        result.attrs["fallback_source"] = "HCTax"
        result.attrs["notice"] = (
            "LGBS returned no listings, so matching was skipped. "
            "Returning HCTax data only."
        )
        return result

    raise RuntimeError("Both LGBS and HCTax returned no listings.")


def save_csv(df: pd.DataFrame, path: Path) -> Path:
    df.to_csv(path, index=False)
    return path
