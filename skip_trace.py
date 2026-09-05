"""Tracerfy batch skip trace integration."""

import io
import os
import time
from pathlib import Path

import pandas as pd
import requests

TRACERFY_BASE = "https://tracerfy.com/v1/api"
POLL_INTERVAL = 5
MAX_POLL_SECONDS = 600


class SkipTraceError(Exception):
    pass


def _headers() -> dict:
    api_key = os.environ.get("TRACERFY_API_KEY")
    if not api_key:
        raise SkipTraceError("TRACERFY_API_KEY is not set")
    return {"Authorization": f"Bearer {api_key}"}


def prepare_tracerfy_input(df: pd.DataFrame) -> pd.DataFrame:
    """Build the minimal CSV Tracerfy expects for advanced owner lookup."""
    out = df.copy()
    out["address"] = out.get("address", "").fillna("").astype(str)
    out["city"] = out.get("city", "HOUSTON").fillna("HOUSTON").astype(str)
    out["state"] = out.get("state", "TX").fillna("TX").astype(str)
    out["zip"] = out.get("zip", "").fillna("").astype(str)
    out["row_id"] = range(1, len(out) + 1)
    return out[["row_id", "address", "city", "state", "zip"]]


def submit_batch(csv_path: Path, trace_type: str = "advanced") -> dict:
    with csv_path.open("rb") as handle:
        response = requests.post(
            f"{TRACERFY_BASE}/trace/",
            headers=_headers(),
            files={"csv_file": (csv_path.name, handle, "text/csv")},
            data={
                "address_column": "address",
                "city_column": "city",
                "state_column": "state",
                "zip_column": "zip",
                "trace_type": trace_type,
            },
            timeout=60,
        )
    if response.status_code >= 400:
        raise SkipTraceError(f"Tracerfy submit failed ({response.status_code}): {response.text}")
    return response.json()


def _find_queue(queue_id: int) -> dict | None:
    response = requests.get(
        f"{TRACERFY_BASE}/queues/",
        headers=_headers(),
        timeout=30,
    )
    if response.status_code >= 400:
        raise SkipTraceError(f"Tracerfy queue poll failed ({response.status_code}): {response.text}")

    for queue in response.json():
        if queue.get("id") == queue_id:
            return queue
    return None


def wait_for_queue(queue_id: int, estimated_wait: int = 30) -> dict:
    deadline = time.time() + MAX_POLL_SECONDS
    sleep_for = max(POLL_INTERVAL, min(estimated_wait, 15))

    while time.time() < deadline:
        queue = _find_queue(queue_id)
        if queue and not queue.get("pending") and queue.get("download_url"):
            return queue
        time.sleep(sleep_for)

    raise SkipTraceError(f"Tracerfy queue {queue_id} did not complete within {MAX_POLL_SECONDS}s")


def download_results(download_url: str) -> pd.DataFrame:
    response = requests.get(download_url, timeout=120)
    if response.status_code >= 400:
        raise SkipTraceError(f"Failed to download Tracerfy results ({response.status_code})")
    return pd.read_csv(io.StringIO(response.text))


def skip_trace_dataframe(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    tracerfy_input = prepare_tracerfy_input(df)
    temp_path = Path("tracerfy_input.csv")
    tracerfy_input.to_csv(temp_path, index=False)

    submit_result = submit_batch(temp_path)
    queue_id = submit_result["queue_id"]
    estimated_wait = submit_result.get("estimated_wait_seconds", 30)

    queue = wait_for_queue(queue_id, estimated_wait=estimated_wait)
    traced_df = download_results(queue["download_url"])

    merged = df.copy()
    merged["row_id"] = range(1, len(merged) + 1)
    if "row_id" in traced_df.columns:
        merged = merged.merge(traced_df, on="row_id", how="left", suffixes=("", "_trace"))
    else:
        merged = merged.merge(traced_df, on=["address", "city", "state"], how="left", suffixes=("", "_trace"))
    merged = merged.drop(columns=["row_id"], errors="ignore")

    phone_col = next((c for c in merged.columns if c in ("primary_phone", "mobile_1", "phone_1")), None)
    hits = int((merged[phone_col].astype(str).str.strip() != "").sum()) if phone_col else 0
    stats = {
        "total_properties": len(df),
        "successfully_traced": int(hits),
        "credits_used": queue.get("credits_deducted", 0),
        "queue_id": queue_id,
    }
    return merged, stats
