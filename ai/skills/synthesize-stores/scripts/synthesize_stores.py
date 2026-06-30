#!/usr/bin/env python3
"""Synthesize Ichimaru store locations by population-weighted sampling.

For every prefecture referenced in ``docs/profiles/Locations.md`` (with its store
count) this script samples that many distinct 大字・町 (ooaza) with probability
proportional to their 2020 Census population, places one store at each sampled
ooaza's polygon centroid, draws weekday / weekend sales baselines, and writes the
combined table to ``DATA/s03_primary/store.tsv``.

Inputs
  * ``docs/profiles/Locations.md``            - prefectures and store counts
  * ``config/config.yaml``                    - ``synthetics/random_seed``
  * ``DATA/s02_intermediate/regional_population.tsv``
  * ``DATA/s02_intermediate/geoshape_<NN>/r2ka<NN>.shp`` (+ .dbf)

Output (overwritten each run)
  * ``DATA/s03_primary/store.tsv`` with columns:
    prefecture, store_name, latitude, longitude,
    weekday_sale_baseline, weekend_sale_baseline

Dependencies: pyshp (shapefile reading) and PyYAML (config) -- see
``requirements.txt``.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import random
import re
import sys
from pathlib import Path

import shapefile  # pyshp
import yaml

# --- sampling constants --------------------------------------------------------
NORMALISED_TOTAL = 10_000_000  # "big number N": weights are normalised to sum to N
OOAZA_LEVEL = "3"              # 地域階層レベル of a 大字・町 row in the population TSV
DEFAULT_PREFIX = ""
DEFAULT_SUFFIX = "店"
DEFAULT_MAG1_RANGE = (80.0, 300.0)   # weekday ramen sales baseline
DEFAULT_MAG2_RANGE = (50.0, 350.0)   # weekend ramen sales baseline

# --- English prefecture name -> (Japanese name, 2-digit JIS code) --------------
# Locations.md uses English names; the population TSV and geoshape dirs use the
# Japanese name and the 2-digit code respectively.
EN_TO_JA_CODE = {
    "hokkaido": ("北海道", "01"), "aomori": ("青森県", "02"), "iwate": ("岩手県", "03"),
    "miyagi": ("宮城県", "04"), "akita": ("秋田県", "05"), "yamagata": ("山形県", "06"),
    "fukushima": ("福島県", "07"), "ibaraki": ("茨城県", "08"), "tochigi": ("栃木県", "09"),
    "gunma": ("群馬県", "10"), "saitama": ("埼玉県", "11"), "chiba": ("千葉県", "12"),
    "tokyo": ("東京都", "13"), "kanagawa": ("神奈川県", "14"), "niigata": ("新潟県", "15"),
    "toyama": ("富山県", "16"), "ishikawa": ("石川県", "17"), "fukui": ("福井県", "18"),
    "yamanashi": ("山梨県", "19"), "nagano": ("長野県", "20"), "gifu": ("岐阜県", "21"),
    "shizuoka": ("静岡県", "22"), "aichi": ("愛知県", "23"), "mie": ("三重県", "24"),
    "shiga": ("滋賀県", "25"), "kyoto": ("京都府", "26"), "osaka": ("大阪府", "27"),
    "hyogo": ("兵庫県", "28"), "nara": ("奈良県", "29"), "wakayama": ("和歌山県", "30"),
    "tottori": ("鳥取県", "31"), "shimane": ("島根県", "32"), "okayama": ("岡山県", "33"),
    "hiroshima": ("広島県", "34"), "yamaguchi": ("山口県", "35"), "tokushima": ("徳島県", "36"),
    "kagawa": ("香川県", "37"), "ehime": ("愛媛県", "38"), "kochi": ("高知県", "39"),
    "fukuoka": ("福岡県", "40"), "saga": ("佐賀県", "41"), "nagasaki": ("長崎県", "42"),
    "kumamoto": ("熊本県", "43"), "oita": ("大分県", "44"), "miyazaki": ("宮崎県", "45"),
    "kagoshima": ("鹿児島県", "46"), "okinawa": ("沖縄県", "47"),
}


# -------------------------------------------------------------------------------
def find_repo_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "docs" / "profiles" / "Locations.md").exists():
            return p
    raise SystemExit("Could not locate repo root (docs/profiles/Locations.md not found).")


def read_random_seed(config_path: Path) -> int:
    """Read synthetics/random_seed from config/config.yaml."""
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    try:
        return int(cfg["synthetics"]["random_seed"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SystemExit(f"{config_path}: missing/invalid 'synthetics/random_seed' ({exc})")


def read_store_plan(locations_md: Path) -> list[tuple[str, str, str, int]]:
    """Parse 'Tokyo: 30 stores' lines into (en, ja, code, n) tuples (ordered)."""
    plan: list[tuple[str, str, str, int]] = []
    for line in locations_md.read_text(encoding="utf-8").splitlines():
        m = re.match(r"\s*[-*]\s*([A-Za-z]+)\s*[:：]\s*(\d+)\s*stores?\b", line)
        if not m:
            continue
        en = m.group(1).lower()
        if en not in EN_TO_JA_CODE:
            continue
        ja, code = EN_TO_JA_CODE[en]
        plan.append((m.group(1), ja, code, int(m.group(2))))
    if not plan:
        raise SystemExit(f"No '<Prefecture>: <n> stores' lines found in {locations_md}")
    return plan


def parse_population(value: str) -> int:
    """Census 総数: 'X' (suppressed) / '-' (none) -> 0; otherwise the integer."""
    value = value.strip().replace(",", "")
    return int(value) if value.lstrip("-").isdigit() and value not in ("-",) else 0


def load_ooaza(pop_tsv: Path, pref_code: str) -> list[dict]:
    """Level-3 (大字・町) rows for one prefecture.

    Returns dicts with: key=(CITY, 町丁字コード), muni=市区町村名 (city/ward/town/
    village), name=大字・町名, pop=総数.
    """
    out: list[dict] = []
    with pop_tsv.open(encoding="utf-8") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            muni_code = row["市区町村コード"]
            if not muni_code.startswith(pref_code) or row["地域階層レベル"] != OOAZA_LEVEL:
                continue
            out.append({
                "key": (muni_code[2:5], row["町丁字コード"]),  # (CITY, KIHON1)
                "muni": row["市区町村名"].strip(),
                "name": row["大字・町名"].strip(),
                "pop": parse_population(row["総数"]),
            })
    return out


def _ring_centroid(points: list[tuple[float, float]]) -> tuple[float, float, float]:
    """Signed-area shoelace centroid of one ring -> (cx, cy, signed_area)."""
    area = cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        cross = x0 * y1 - x1 * y0
        area += cross
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
    area *= 0.5
    if area == 0.0:  # degenerate ring: fall back to vertex mean
        n = len(points) or 1
        return sum(p[0] for p in points) / n, sum(p[1] for p in points) / n, 0.0
    return cx / (6 * area), cy / (6 * area), area


def _polygon_centroid(shape) -> tuple[float, float, float]:
    """Area-weighted centroid of one (possibly multi-part) polygon record.

    Outer rings and holes share consistent winding within a record, so the signed
    areas combine correctly (holes subtract). Returns (lon, lat, net_signed_area).
    """
    bounds = list(shape.parts) + [len(shape.points)]
    sx = sy = sa = 0.0
    for i in range(len(bounds) - 1):
        cx, cy, a = _ring_centroid(shape.points[bounds[i]:bounds[i + 1]])
        sx += cx * a
        sy += cy * a
        sa += a
    if sa == 0.0:
        return shape.points[0][0], shape.points[0][1], 0.0
    return sx / sa, sy / sa, sa


def build_centroids(shp_path: Path) -> dict[tuple[str, str], tuple[float, float]]:
    """Map (CITY, KIHON1) -> ooaza centroid (lon, lat).

    Polygons exist only at the 字・丁目 level, so an ooaza centroid is the
    area-weighted combination of its constituent 字・丁目 polygon centroids.
    """
    acc: dict[tuple[str, str], list[float]] = {}  # key -> [sum lon*a, sum lat*a, sum a]
    reader = shapefile.Reader(str(shp_path), encoding="cp932")
    for sr in reader.iterShapeRecords():
        if not sr.shape.points:
            continue
        key = (sr.record["CITY"], sr.record["KIHON1"])
        lon, lat, area = _polygon_centroid(sr.shape)
        bucket = acc.setdefault(key, [0.0, 0.0, 0.0])
        bucket[0] += lon * area
        bucket[1] += lat * area
        bucket[2] += area
    return {k: (sx / sa, sy / sa) for k, (sx, sy, sa) in acc.items() if sa != 0.0}


def sample_locations(ooaza: list[dict], prefecture: str,
                     centroids: dict[tuple[str, str], tuple[float, float]],
                     n: int, prefix: str, suffix: str,
                     mag1_range: tuple[float, float],
                     mag2_range: tuple[float, float] | None,
                     rng: random.Random) -> list[tuple]:
    """Population-weighted sampling of n distinct ooaza -> store tuples.

    Each tuple is (name, latitude, longitude, magnitude1, magnitude2); magnitude2
    is None when mag2_range is None. The store name is
    <prefix><prefecture><city/ward/town/village><ooaza><suffix>.
    """
    # Eligible = positive population AND a geometry to place the store at.
    pool = [o for o in ooaza if o["pop"] > 0 and o["key"] in centroids]
    if len(pool) < n:
        raise SystemExit(
            f"Only {len(pool)} eligible ooaza for {n} requested stores.")

    total_pop = sum(o["pop"] for o in pool)
    # Normalise populations to weights summing to N, sorted descending, and build
    # the cumulative partition of N used to locate a uniformly sampled u.
    pool.sort(key=lambda o: o["pop"], reverse=True)
    cumulative: list[float] = []
    running = 0.0
    for o in pool:
        running += o["pop"] / total_pop * NORMALISED_TOTAL
        cumulative.append(running)
    upper = cumulative[-1]  # ~= NORMALISED_TOTAL

    chosen_idx: set[int] = set()
    results: list[tuple] = []
    while len(results) < n:
        u = rng.uniform(0.0, upper)
        idx = bisect.bisect_right(cumulative, u)
        if idx >= len(pool) or idx in chosen_idx:  # duplicate / boundary -> redo
            continue
        chosen_idx.add(idx)
        o = pool[idx]
        lon, lat = centroids[o["key"]]
        name = f"{prefix}{prefecture}{o['muni']}{o['name']}{suffix}"
        mag1 = rng.uniform(*mag1_range)
        mag2 = rng.uniform(*mag2_range) if mag2_range else None
        results.append((name, lat, lon, mag1, mag2))
    return results


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo-root", type=Path, default=None,
                    help="Repo root (auto-detected if omitted).")
    ap.add_argument("--config", type=Path, default=None,
                    help="Path to config.yaml (default <repo-root>/config/config.yaml).")
    args = ap.parse_args()

    repo_root = args.repo_root or find_repo_root(Path(__file__).resolve())
    locations_md = repo_root / "docs" / "profiles" / "Locations.md"
    config_path = args.config or (repo_root / "config" / "config.yaml")
    pop_tsv = repo_root / "DATA" / "s02_intermediate" / "regional_population.tsv"
    geoshape_dir = repo_root / "DATA" / "s02_intermediate"
    out_path = repo_root / "DATA" / "s03_primary" / "store.tsv"

    seed = read_random_seed(config_path)
    plan = read_store_plan(locations_md)
    rng = random.Random(seed)
    print(f"random_seed={seed}; prefectures: "
          + ", ".join(f"{en}={n}" for en, _, _, n in plan))

    rows: list[tuple] = []
    for en, ja, code, n in plan:
        shp = geoshape_dir / f"geoshape_{code}" / f"r2ka{code}.shp"
        if not shp.exists():
            raise SystemExit(f"Missing geoshape for {en} ({ja}): {shp}")
        ooaza = load_ooaza(pop_tsv, code)
        centroids = build_centroids(shp)
        stores = sample_locations(
            ooaza, ja, centroids, n, DEFAULT_PREFIX, DEFAULT_SUFFIX,
            DEFAULT_MAG1_RANGE, DEFAULT_MAG2_RANGE, rng)
        for name, lat, lon, mag1, mag2 in stores:
            rows.append((en, name, lat, lon, mag1, mag2))
        print(f"  [OK] {en} ({ja}, {code}): {len(stores)} stores "
              f"from {sum(1 for o in ooaza if o['pop'] > 0 and o['key'] in centroids)} eligible ooaza")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["prefecture", "store_name", "latitude", "longitude",
                    "weekday_sale_baseline", "weekend_sale_baseline"])
        for en, name, lat, lon, mag1, mag2 in rows:
            w.writerow([en, name, f"{lat:.6f}", f"{lon:.6f}",
                        f"{mag1:.1f}", "" if mag2 is None else f"{mag2:.1f}"])

    print(f"Wrote {len(rows)} stores -> {out_path.relative_to(repo_root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
