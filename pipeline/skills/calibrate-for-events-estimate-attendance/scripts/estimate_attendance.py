#!/usr/bin/env python3
"""Calibrate for events — step 4/4: estimate attendance & per-store demand (LLM; live/GPU).

Implements docs/pipeline/calibration/Calibrate-for-events-estimate-attendance.md.

  DATA/s09_calibration/mapped_events.json
    -> POST {agent-service}/v1/estimate-attendance (distinct events, chunks of 8)
    -> attendance × baseline_demand_probability × distance_loss, capped
    -> DATA/s09_calibration/estimated_events.json

Reduces the (store, event) pairs to distinct events by `event_name`, estimates each event's
attendance once, joins the estimate back to every pair, and converts attendance into an
expected extra demand in bowls per pair via the rules in
`pipeline/config/config.yaml` under `calibration/events/`.

Live / not reproducible; spends GPU time. The agent-service web API must be running
(`make serve` in agent-service/, wait for /readyz). Reads GOTOAI_AGENT_API_KEY (bearer)
from the environment or agent-service/.env; never logs it. Last of four independent
event-calibration skills:

  extract -> geo-code -> map-match -> estimate-attendance (this)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from pathlib import Path

# Demand-rule defaults, used only if config.yaml lacks calibration/events/*.
DEFAULT_PROB = {"fireworks": 0.01, "festival": 0.01, "sports": 0.005, "other": 0.001}
DEFAULT_LOSS = {100: 0.10, 200: 0.05, 500: 0.01, "other": 0.001}
DEFAULT_MAX_ADDED_DEMAND = 20

ESTIMATE_CHUNK = 8              # events per estimate-attendance request
API_TIMEOUT_DEFAULT = 300.0     # first request triggers a lazy model load on the server


# ---------------------------------------------------------------- repo / env / config
def find_repo_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "pipeline" / "config" / "config.yaml").exists():
            return p
    raise SystemExit("Could not locate repo root "
                     "(pipeline/config/config.yaml not found).")


def read_env_value(env_path: Path, key: str, default: str = "") -> str:
    """`key` from the environment, else parsed from `env_path` (.env); never logged."""
    v = os.environ.get(key, "").strip()
    if v:
        return v
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, val = line.split("=", 1)
            if k.strip() == key:
                return val.strip().strip('"').strip("'")
    return default


def load_events_config(repo: Path, config_path: Path | None):
    import yaml  # PyYAML is a pipeline dependency

    path = config_path or (repo / "pipeline" / "config" / "config.yaml")
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    ev = ((cfg.get("calibration") or {}).get("events")) or {}
    prob = ev.get("baseline_demand_probability") or DEFAULT_PROB
    loss = ev.get("distance_loss") or DEFAULT_LOSS
    max_added_demand = ev.get("max_added_demand", DEFAULT_MAX_ADDED_DEMAND)
    return prob, loss, max_added_demand


# --------------------------------------------------------------------- HTTP to the API
class ApiError(Exception):
    """Retryable API failure (non-2xx other than auth, or a transport hiccup)."""


def api_post(base_url: str, path: str, body: dict, *, api_key: str, timeout: float) -> dict:
    url = base_url.rstrip("/") + path
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        if exc.code == 401:
            raise SystemExit("401 from agent-service — set GOTOAI_AGENT_API_KEY to match "
                             "the server (agent-service/.env).")
        raise ApiError(f"HTTP {exc.code}: {detail}")
    except urllib.error.URLError as exc:
        if isinstance(exc.reason, ConnectionRefusedError):
            raise SystemExit(f"Cannot reach agent-service at {base_url} — start it with "
                             "`make serve` in agent-service/ and wait for /readyz.")
        raise ApiError(str(exc))


def api_post_retry(base_url, path, body, *, api_key, timeout, label, retries=1):
    """POST with one retry; on final failure log and return None (retry-and-skip)."""
    for attempt in range(retries + 1):
        try:
            return api_post(base_url, path, body, api_key=api_key, timeout=timeout)
        except ApiError as exc:
            if attempt >= retries:
                print(f"  [skip] {label}: {exc}", file=sys.stderr)
                return None
            time.sleep(2 * (attempt + 1))


# ----------------------------------------------------------------------------- helpers
def read_json_list(path: Path) -> list:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit(f"{path} is not a JSON list.")
    return data


def write_json_list(path: Path, rows: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)
        f.write("\n")


def to_number(value):
    """A cell as a number, or None if blank / non-numeric ("" is how the model omits)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    try:
        s = str(value).strip()
        return float(s) if s else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------- demand rules
def _attendance_used(att: dict):
    """The number the demand formula consumes: point, else low/high midpoint, else None."""
    point = to_number(att.get("point"))
    if point is not None:
        return point
    low, high = to_number(att.get("low")), to_number(att.get("high"))
    if low is not None and high is not None:
        return (low + high) / 2
    return None


def _demand_probability(event_type, event_name, prob_map: dict):
    """Probability for an event by keyword-matching prob_map keys against its type/name.

    The extractor's `event_type` is an English enum (concert|festival|fireworks|...), so
    the Japanese config keys (花火・祭り・大会 …) only ever appear in the Japanese
    `event_name`; we therefore search both, joined. A key matches when it is a
    case-insensitive **substring** of that text (so `festival` hits "music festival" and
    `祭り` hits "音楽祭り", which an exact lookup misses). Keys are tried in config order,
    so put more specific keywords first; `other` is the fallback and is never matched as a
    substring.
    """
    hay = f"{event_type} {event_name}".upper()
    for key, prob in prob_map.items():
        if key == "other":
            continue
        if str(key).upper() in hay:
            return prob
    return prob_map.get("other")


def _distance_loss(distance_m, loss_map: dict):
    """The first band factor whose upper bound ≥ distance_m (ascending), else `other`."""
    d = to_number(distance_m)
    for bound in sorted(k for k in loss_map if isinstance(k, (int, float)) and not isinstance(k, bool)):
        if d is not None and d <= bound:
            return loss_map[bound]
    return loss_map.get("other")


# ------------------------------------------------------------------------- estimate
def estimate_attendance(repo, base_url, api_key, *, prob_map, loss_map, max_added_demand,
                        timeout, in_path, out_path):
    if not in_path.exists():
        raise SystemExit(f"Missing input: {in_path} — run the `map-match` skill first.")
    matches = read_json_list(in_path)
    if not matches:
        write_json_list(out_path, [])
        print(f"Wrote {out_path} (0 pairs — nothing to estimate).")
        return []

    # Distinct events by name (the same event near several stores is estimated once).
    distinct: "OrderedDict[str, dict]" = OrderedDict()
    for m in matches:
        name = m.get("event_name", "")
        if name and name not in distinct:
            distinct[name] = {
                "event_name": name, "event_type": m.get("event_type", ""),
                "start_date": m.get("start_date", ""), "venue": m.get("venue", ""),
                "location": m.get("location", ""),
            }
    events = list(distinct.values())

    est_by_name: dict[str, dict] = {}
    for i in range(0, len(events), ESTIMATE_CHUNK):
        chunk = events[i:i + ESTIMATE_CHUNK]
        data = api_post_retry(base_url, "/v1/estimate-attendance", {"events": chunk},
                              api_key=api_key, timeout=timeout,
                              label=f"estimate chunk {i // ESTIMATE_CHUNK + 1}")
        if data is None:
            continue
        for e in data.get("estimates", []):
            est_by_name[e.get("event_name", "")] = e

    out: list[dict] = []
    n_ok = n_capped = 0
    for m in matches:
        row = dict(m)
        est = est_by_name.get(m.get("event_name", ""))
        att = (est.get("expected_attendance") or {}) if est else {}
        used = _attendance_used(att)
        prob = _demand_probability(m.get("event_type", ""), m.get("event_name", ""), prob_map)
        loss = _distance_loss(m.get("distance_m"), loss_map)

        if used is None or prob is None or loss is None:
            demand, capped, status = 0, False, "no-estimate"
        else:
            uncapped = int(used * prob * loss + 0.5)   # round to nearest (non-negative)
            demand = min(uncapped, max_added_demand)
            capped = uncapped > max_added_demand
            status = "ok"
            n_ok += 1
            n_capped += int(capped)

        row.update({
            "expected_attendance": {"point": att.get("point", ""), "low": att.get("low", ""),
                                    "high": att.get("high", "")},
            "attendance_confidence": est.get("confidence", "") if est else "",
            "rationale": est.get("rationale", "") if est else "",
            "attendance_used": used if used is not None else "",
            "demand_probability": prob,
            "distance_loss": loss,
            "estimated_demand": demand,
            "demand_capped": capped,
            "estimate_status": status,
        })
        out.append(row)

    write_json_list(out_path, out)
    print(f"Wrote {out_path} ({len(out)} pairs; {n_ok} estimated, "
          f"{len(out) - n_ok} no-estimate, {n_capped} capped at {max_added_demand}).")
    return out


# ---------------------------------------------------------------------------- driver
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo-root", type=Path, default=None)
    ap.add_argument("--config", type=Path, default=None, help="config.yaml (default: pipeline/config/)")
    ap.add_argument("--base-url", default=None, help="agent-service base URL (default: from .env / :8000)")
    ap.add_argument("--timeout", type=float, default=API_TIMEOUT_DEFAULT, help="API request timeout (s)")
    args = ap.parse_args()

    repo = args.repo_root or find_repo_root(Path(__file__).resolve())
    env_path = repo / "agent-service" / ".env"

    base_url = args.base_url or (
        f"http://{read_env_value(env_path, 'API_HOST', '127.0.0.1')}"
        f":{read_env_value(env_path, 'API_PORT', '8000')}")
    api_key = read_env_value(env_path, "GOTOAI_AGENT_API_KEY")
    prob_map, loss_map, max_added_demand = load_events_config(repo, args.config)

    out_dir = repo / "DATA" / "s09_calibration"
    in_path = out_dir / "mapped_events.json"
    out_path = out_dir / "estimated_events.json"

    estimate_attendance(repo, base_url, api_key, prob_map=prob_map, loss_map=loss_map,
                        max_added_demand=max_added_demand, timeout=args.timeout,
                        in_path=in_path, out_path=out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
