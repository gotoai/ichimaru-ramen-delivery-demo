"""Geocoding tool — resolve an event's venue/location string to coordinates.

A **deterministic enrichment** step, not an LLM task: it takes `extract-events`' events and
adds `latitude`/`longitude` (plus match quality) via the Google Geocoding API, so it
slots into the pipeline between `extract-events` and `estimate-attendance` and lets downstream spatial
logic (distance-to-store, mapping) treat web-found events like the synthetic ones in
`event.tsv`.

The model is deliberately NOT involved — LLMs hallucinate coordinates; a geocoder is
authoritative. Following the other task modules, the pure, network-free helpers
(`build_query`, `enrich_events`) stay unit-testable offline; the one HTTP call lives in
`geocode_query`. Uses stdlib `urllib` only (no new dependency).

Docs: https://developers.google.com/maps/documentation/geocoding
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PROMPT_VERSION = "geocode-locations/v1"  # named for parity with the LLM tasks; no prompt here

_ENDPOINT = "https://maps.googleapis.com/maps/api/geocode/json"

# location_type -> a 0..1 confidence, so downstream code can weight/filter matches the
# same way it weights extract's `confidence`. ROOFTOP is an exact address hit;
# APPROXIMATE is city/region-level only.
_LOCATION_TYPE_CONFIDENCE = {
    "ROOFTOP": 1.0,
    "RANGE_INTERPOLATED": 0.8,
    "GEOMETRIC_CENTER": 0.6,
    "APPROXIMATE": 0.4,
}

# Google statuses that will not improve on retry — surface them instead of looping.
_FATAL_STATUSES = {"REQUEST_DENIED", "INVALID_REQUEST"}


class GeocodeError(RuntimeError):
    """Non-retryable geocoding failure (bad key, malformed request, quota exhausted)."""


def build_query(event: dict) -> str:
    """Combine `venue` + `location` into one query string (venue first, most specific).

    Empty parts are dropped. `components=country:JP` (set in `geocode_query`) keeps the
    lookup inside Japan, so a bare venue like '岡本公園民家園' still resolves sensibly.
    """
    parts = [str(event.get("venue", "")).strip(), str(event.get("location", "")).strip()]
    return " ".join(p for p in parts if p)


def geocode_query(
    query: str,
    *,
    api_key: str,
    language: str = "ja",
    region: str = "jp",
    timeout: float = 10.0,
    max_retries: int = 3,
) -> dict | None:
    """Geocode one string via Google. Returns a result dict, or None if not found.

    Result dict: {latitude, longitude, formatted_address, location_type,
    geocode_confidence, place_id}. Retries transient errors (network, OVER_QUERY_LIMIT)
    with backoff; raises GeocodeError on fatal statuses.
    """
    if not query:
        return None
    if not api_key:
        raise GeocodeError("GOOGLE_GEOCODING_API_KEY is empty — set it in agent-service/.env")

    params = {
        "address": query,
        "key": api_key,
        "language": language,
        "region": region,
        "components": "country:JP",
    }
    url = f"{_ENDPOINT}?{urllib.parse.urlencode(params)}"

    last_err: Exception | None = None
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_err = exc
            time.sleep(0.5 * (attempt + 1))
            continue

        status = payload.get("status", "")
        if status == "OK":
            return _first_result(payload["results"])
        if status == "ZERO_RESULTS":
            return None
        if status == "OVER_QUERY_LIMIT":
            last_err = GeocodeError("OVER_QUERY_LIMIT")
            time.sleep(1.0 * (attempt + 1))  # rate-limited: back off harder
            continue
        if status in _FATAL_STATUSES:
            raise GeocodeError(f"{status}: {payload.get('error_message', '')}".strip())
        # Unknown status — treat as transient.
        last_err = GeocodeError(f"{status}: {payload.get('error_message', '')}".strip())
        time.sleep(0.5 * (attempt + 1))

    raise GeocodeError(f"geocoding failed after {max_retries} attempts: {last_err}")


def _first_result(results: list) -> dict:
    r = results[0]
    loc = r["geometry"]["location"]
    loc_type = r["geometry"].get("location_type", "")
    return {
        "latitude": loc["lat"],
        "longitude": loc["lng"],
        "formatted_address": r.get("formatted_address", ""),
        "location_type": loc_type,
        "geocode_confidence": _LOCATION_TYPE_CONFIDENCE.get(loc_type, 0.0),
        "place_id": r.get("place_id", ""),
    }


def _empty_geocode(status: str) -> dict:
    """The geocode fields for an unresolved event, so every row has the same shape."""
    return {
        "latitude": "", "longitude": "", "formatted_address": "",
        "location_type": "", "geocode_confidence": "", "place_id": "",
        "geocode_status": status,
    }


def enrich_events(
    events: list[dict],
    *,
    api_key: str,
    language: str = "ja",
    region: str = "jp",
    cache: dict | None = None,
) -> list[dict]:
    """Return copies of `events` each augmented with geocode fields.

    Failures are per-event and non-fatal (retry-and-skip, like `search-events`): a row
    that can't be resolved gets empty coordinates and a `geocode_status` note, and the
    rest continue. `cache` (query -> result|None) is read and populated in place so the
    same venue isn't billed twice within/across runs.
    """
    cache = cache if cache is not None else {}
    out: list[dict] = []
    for ev in events:
        result = dict(ev)
        query = build_query(ev)
        if not query:
            result.update(_empty_geocode("no-venue-or-location"))
            out.append(result)
            continue

        if query in cache:
            hit = cache[query]
        else:
            try:
                hit = geocode_query(query, api_key=api_key, language=language, region=region)
            except GeocodeError as exc:
                result.update(_empty_geocode(f"error: {exc}"))
                out.append(result)
                continue
            cache[query] = hit  # cache misses (None) too — don't re-query dead strings

        if hit is None:
            result.update(_empty_geocode("zero-results"))
        else:
            result.update(hit)
            result["geocode_status"] = "ok"
        out.append(result)
    return out


def load_cache(path: str | Path) -> dict:
    """Load the persistent query->result cache; {} if absent or unreadable."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_cache(path: str | Path, cache: dict) -> None:
    """Persist the cache (creating the parent dir)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
