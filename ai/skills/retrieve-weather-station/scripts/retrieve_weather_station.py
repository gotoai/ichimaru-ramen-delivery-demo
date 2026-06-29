#!/usr/bin/env python3
"""Retrieve the JMA AMeDAS station master and reduce it to active stations.

Downloads the AMeDAS station-history master file (amdmaster.index4), keeps only
the records that are currently active (観測終了年月日 / End Date == '9999-99-99'),
and writes a UTF-8 TSV with a reduced column set to
``DATA/s02_intermediate/weather-station.tsv``.

Only the Python standard library is used.

Source CSV:  https://www.data.jma.go.jp/stats/data/mdrr/chiten/meta/amdmaster.index4
Format spec: https://www.data.jma.go.jp/stats/data/mdrr/man/amdmasterindex4_format.pdf

The source is Shift_JIS (CP932), comma-separated, with two header rows. Each data
record has 33 fields; per the spec, the currently-valid record for a station is
the one whose End Date is '9999-99-99'.
"""
from __future__ import annotations

import argparse
import csv
import io
import sys
import urllib.request
from pathlib import Path

SOURCE_URL = "https://www.data.jma.go.jp/stats/data/mdrr/chiten/meta/amdmaster.index4"
SOURCE_ENCODING = "cp932"
USER_AGENT = "Mozilla/5.0 (compatible; ichimaru-demo/1.0; +retrieve-weather-station skill)"

END_DATE_COL = 24          # source column index of End Date (観測終了年月日)
ACTIVE_END_DATE = "9999-99-99"

# Output column name -> source column index (0-based) in amdmaster.index4.
OUTPUT_COLUMNS = [
    ("Station Number", 0),
    ("Station Name (Kanji)", 1),
    ("Latitude_Precipitation", 7),
    ("Longitude_Precipitation", 8),
    ("Altitude_Precipitation", 9),
    ("Height of Anemometer", 10),
    ("Precipitation", 16),
    ("Wind Speed", 17),
    ("Temperature", 18),
    ("Sunshine Duration", 19),
    ("Depth of Snow Cover", 20),
    ("Humidity", 22),
    ("Start Date", 23),
    ("End Date", END_DATE_COL),
]


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def to_active_tsv_rows(csv_bytes: bytes) -> list[list[str]]:
    """Parse the master CSV and return active-station rows in OUTPUT_COLUMNS order.

    The two header rows are skipped; only records with End Date == '9999-99-99'
    are kept. Fields are stripped of surrounding whitespace (incl. the full-width
    spaces used for padding, which Python's str.strip() removes).
    """
    rows = list(csv.reader(io.StringIO(csv_bytes.decode(SOURCE_ENCODING, "replace"))))
    indices = [i for _, i in OUTPUT_COLUMNS]
    out: list[list[str]] = []
    for r in rows[2:]:
        if len(r) <= END_DATE_COL or r[END_DATE_COL].strip() != ACTIVE_END_DATE:
            continue
        out.append([r[i].strip() for i in indices])
    return out


def find_repo_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "docs" / "profiles" / "Locations.md").exists():
            return p
    raise SystemExit("Could not locate repo root (docs/profiles/Locations.md not found).")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", type=Path, default=None)
    args = ap.parse_args()

    repo_root = args.repo_root or find_repo_root(Path(__file__).resolve())
    raw_dir = repo_root / "DATA" / "s01_raw"
    out_path = repo_root / "DATA" / "s02_intermediate" / "weather-station.tsv"
    raw_dir.mkdir(parents=True, exist_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    print("Fetching AMeDAS station master (amdmaster.index4) ...")
    body = fetch(SOURCE_URL)
    raw_path = raw_dir / "weather-station.csv"
    raw_path.write_bytes(body)                          # original CP932, unmodified

    rows = to_active_tsv_rows(body)
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow([name for name, _ in OUTPUT_COLUMNS])
        w.writerows(rows)

    print(f"  [OK] {raw_path.relative_to(repo_root)} ({len(body):,} bytes)")
    print(f"  [OK] {out_path.relative_to(repo_root)} "
          f"({len(rows):,} active stations, {len(OUTPUT_COLUMNS)} columns)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
