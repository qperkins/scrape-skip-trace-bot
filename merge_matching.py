"""Merge rows from both listing CSVs that share the same normalized address."""

import re
from pathlib import Path

import pandas as pd

from pipeline import HCTAX_CSV, LGBS_CSV, ROOT, normalize_address

OUTPUT_CSV = ROOT / "output" / "matching_addresses_skiptrace.csv"

# Redundant with the single Tracerfy address block at the front of the CSV.
DROP_AFTER_MERGE = {
    "hctax_address",
    "lgbs_prop_address_one",
    "lgbs_prop_address_two",
    "lgbs_prop_city",
    "lgbs_prop_state",
    "lgbs_prop_zipcode",
    "lgbs_street_name",
    "lgbs_full_address",
    "lgbs_state",
}


def lgbs_full_address(row: pd.Series) -> str:
    parts = [str(row.get("prop_address_one", "") or "").strip()]
    for col in ("prop_city", "prop_state", "prop_zipcode"):
        value = row.get(col, "")
        if pd.isna(value):
            continue
        text = str(value).strip().replace(".0", "")
        if text:
            parts.append(text)
    return " ".join(parts)


def _clean_zip(value) -> str:
    if pd.isna(value):
        return ""
    return str(value).strip().replace(".0", "")


def _street_from_hctax(full_address: str) -> str:
    """Pull the street line out of an HCTAX full-address string."""
    text = str(full_address or "").strip()
    match = re.match(r"^(.*?)\s+([A-Z .]+)\s+([A-Z]{2})\s+(\d{5}(?:-\d{4})?)$", text)
    if match:
        return match.group(1).strip()
    return text


def _format_tracerfy_address(merged: pd.DataFrame) -> pd.DataFrame:
    """One canonical address block for Tracerfy; LGBS components preferred."""
    out = merged.copy()

    street = out["lgbs_prop_address_one"].fillna("").astype(str).str.strip()
    missing_street = street == ""
    street = street.where(~missing_street, out["hctax_address"].map(_street_from_hctax))

    city = out["lgbs_prop_city"].fillna("").astype(str).str.strip()
    city = city.where(city != "", "HOUSTON")

    state = out["lgbs_prop_state"].fillna("").astype(str).str.strip()
    state = state.where(state != "", "TX")

    zipcode = out["lgbs_prop_zipcode"].map(_clean_zip)

    out.insert(0, "row_id", range(1, len(out) + 1))
    out.insert(1, "address", street)
    out.insert(2, "city", city)
    out.insert(3, "state", state)
    out.insert(4, "zip", zipcode)

    return out.drop(columns=[c for c in DROP_AFTER_MERGE if c in out.columns])


def merge_matching_addresses(
    hctax_df: pd.DataFrame | None = None,
    lgbs_df: pd.DataFrame | None = None,
    hctax_csv: Path = HCTAX_CSV,
    lgbs_csv: Path = LGBS_CSV,
    output_csv: Path | None = OUTPUT_CSV,
) -> pd.DataFrame:
    hctax = hctax_df.copy() if hctax_df is not None else pd.read_csv(hctax_csv).copy()
    lgbs = lgbs_df.copy() if lgbs_df is not None else pd.read_csv(lgbs_csv).copy()

    hctax["normalized_address"] = hctax["address"].map(normalize_address)
    lgbs["full_address"] = lgbs.apply(lgbs_full_address, axis=1)
    lgbs["normalized_address"] = lgbs["full_address"].map(normalize_address)

    hctax_prefixed = hctax.add_prefix("hctax_").rename(
        columns={"hctax_normalized_address": "normalized_address"}
    )
    lgbs_prefixed = lgbs.add_prefix("lgbs_").rename(
        columns={"lgbs_normalized_address": "normalized_address"}
    )

    merged = hctax_prefixed.merge(lgbs_prefixed, on="normalized_address", how="inner")
    merged = merged.drop(columns=["normalized_address"])
    merged = _format_tracerfy_address(merged)

    if output_csv is not None:
        output_csv.parent.mkdir(parents=True, exist_ok=True)
        merged.to_csv(output_csv, index=False)
    return merged


if __name__ == "__main__":
    result = merge_matching_addresses()
    unique = result.drop_duplicates(subset=["address", "city", "state", "zip"])
    print(f"Wrote {len(result)} rows ({len(unique)} unique addresses) to {OUTPUT_CSV}")
