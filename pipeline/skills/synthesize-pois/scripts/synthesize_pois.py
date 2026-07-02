#!/usr/bin/env python3
"""Synthesize Ichimaru POIs (competitors and home buildings).

Both POI layers reuse the **Location sampling algorithm** of the
``synthesize-stores`` skill: per prefecture, sample ooaza (大字・町) with
probability proportional to 2020 Census population. Unlike stores (which sit on the
ooaza centroid), each POI is placed at a **uniform random point inside the sampled
ooaza polygon**. Also unlike stores, POIs are sampled **with replacement** (the
same ooaza may host several POIs); every row is still given a unique name via a
zero-padded sequence suffix.

Driven by ``pipeline/config/config.yaml``:
  * ``synthetics/random_seed``
  * ``synthetics/competitors/{numbers,weekday_sales_baseline,
    weekend_sales_baseline,business_duration}``
  * ``synthetics/home_buildings/{numbers,unit_range}``

Time windows follow the prose rules in ``time_horizon`` (computed here in JST):
  * competitor / home-building open dates: Jan 1 of (end_year - 5) .. cutoff
  * cutoff = the date two days before today (JST)

Outputs (overwritten each run), under ``DATA/s03_primary/``:
  * ``competitor.tsv``  : prefecture, competitor_name, latitude, longitude,
    weekday_sale_baseline, weekend_sale_baseline, open_date, end_date
  * ``home_building.tsv``: prefecture, home_building_name, latitude, longitude,
    unit, open_date, end_date

Competitor ``end_date`` = open_date + business_duration days, or ``9999-99-99``
(still open) when that falls after the cutoff. Home-building ``end_date`` is always
``9999-99-99``.

Inputs (intermediate data):
  * ``DATA/s02_intermediate/regional_population.tsv``
  * ``DATA/s02_intermediate/geoshape_<NN>/r2ka<NN>.shp`` (+ .dbf)

Dependencies: pyshp (shapefile reading) and PyYAML (config) -- see
``requirements.txt``.
"""
from __future__ import annotations

import argparse
import bisect
import csv
import math
import random
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import shapefile  # pyshp
import yaml

# --- sampling constants --------------------------------------------------------
NORMALISED_TOTAL = 10_000_000  # "big number N": weights are normalised to sum to N
OOAZA_LEVEL = "3"              # 地域階層レベル of a 大字・町 row in the population TSV
JST = timezone(timedelta(hours=9))
HISTORY_START_YEARS_BEFORE = 5  # competitor/home history starts Jan 1, end_year - 5
CUTOFF_DAYS_BEFORE_TODAY = 2    # "2 days before current system date in JST"
ENDLESS = "9999-99-99"          # never-closing sentinel

# --- Japanese prefecture name -> (English display name, 2-digit JIS code) -------
# config keys the prefectures by Japanese name and the geoshape dirs use the code.
# The output `prefecture` column uses the Japanese (Kanji) name; the English name
# is retained only for reference.
JA_TO_EN_CODE = {
    "北海道": ("Hokkaido", "01"), "青森県": ("Aomori", "02"), "岩手県": ("Iwate", "03"),
    "宮城県": ("Miyagi", "04"), "秋田県": ("Akita", "05"), "山形県": ("Yamagata", "06"),
    "福島県": ("Fukushima", "07"), "茨城県": ("Ibaraki", "08"), "栃木県": ("Tochigi", "09"),
    "群馬県": ("Gunma", "10"), "埼玉県": ("Saitama", "11"), "千葉県": ("Chiba", "12"),
    "東京都": ("Tokyo", "13"), "神奈川県": ("Kanagawa", "14"), "新潟県": ("Niigata", "15"),
    "富山県": ("Toyama", "16"), "石川県": ("Ishikawa", "17"), "福井県": ("Fukui", "18"),
    "山梨県": ("Yamanashi", "19"), "長野県": ("Nagano", "20"), "岐阜県": ("Gifu", "21"),
    "静岡県": ("Shizuoka", "22"), "愛知県": ("Aichi", "23"), "三重県": ("Mie", "24"),
    "滋賀県": ("Shiga", "25"), "京都府": ("Kyoto", "26"), "大阪府": ("Osaka", "27"),
    "兵庫県": ("Hyogo", "28"), "奈良県": ("Nara", "29"), "和歌山県": ("Wakayama", "30"),
    "鳥取県": ("Tottori", "31"), "島根県": ("Shimane", "32"), "岡山県": ("Okayama", "33"),
    "広島県": ("Hiroshima", "34"), "山口県": ("Yamaguchi", "35"), "徳島県": ("Tokushima", "36"),
    "香川県": ("Kagawa", "37"), "愛媛県": ("Ehime", "38"), "高知県": ("Kochi", "39"),
    "福岡県": ("Fukuoka", "40"), "佐賀県": ("Saga", "41"), "長崎県": ("Nagasaki", "42"),
    "熊本県": ("Kumamoto", "43"), "大分県": ("Oita", "44"), "宮崎県": ("Miyazaki", "45"),
    "鹿児島県": ("Kagoshima", "46"), "沖縄県": ("Okinawa", "47"),
}


# -------------------------------------------------------------------------------
def find_repo_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "docs" / "pipeline" / "profiles" / "Locations.md").exists():
            return p
    raise SystemExit("Could not locate repo root (docs/pipeline/profiles/Locations.md not found).")


def parse_population(value: str) -> int:
    """Census 総数: 'X' (suppressed) / '-' (none) -> 0; otherwise the integer."""
    value = value.strip().replace(",", "")
    return int(value) if value.lstrip("-").isdigit() and value not in ("-",) else 0


def load_ooaza(pop_tsv: Path, pref_code: str) -> list[dict]:
    """Level-3 (大字・町) rows for one prefecture: key, muni, name, pop."""
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


def build_ooaza_geom(shp_path: Path) -> dict[tuple[str, str], dict]:
    """Map (CITY, KIHON1) -> ooaza geometry aggregated from its 字・丁目 polygons.

    Each value is ``{"rings": [...], "bbox": (minx, miny, maxx, maxy),
    "centroid": (lon, lat)}``. POIs are placed at a uniform random point inside the
    rings; the area-weighted centroid is kept only as a rejection-sampling fallback.
    """
    acc: dict[tuple[str, str], dict] = {}
    reader = shapefile.Reader(str(shp_path), encoding="cp932")
    for sr in reader.iterShapeRecords():
        if not sr.shape.points:
            continue
        key = (sr.record["CITY"], sr.record["KIHON1"])
        g = acc.setdefault(key, {"rings": [], "sx": 0.0, "sy": 0.0, "sa": 0.0,
                                 "bbox": [math.inf, math.inf, -math.inf, -math.inf]})
        bounds = list(sr.shape.parts) + [len(sr.shape.points)]
        for i in range(len(bounds) - 1):
            ring = sr.shape.points[bounds[i]:bounds[i + 1]]
            g["rings"].append(ring)
            cx, cy, a = _ring_centroid(ring)
            g["sx"] += cx * a
            g["sy"] += cy * a
            g["sa"] += a
            bb = g["bbox"]
            for px, py in ring:
                bb[0], bb[1] = min(bb[0], px), min(bb[1], py)
                bb[2], bb[3] = max(bb[2], px), max(bb[3], py)
    out: dict[tuple[str, str], dict] = {}
    for key, g in acc.items():
        if not g["rings"]:
            continue
        centroid = ((g["sx"] / g["sa"], g["sy"] / g["sa"]) if g["sa"] != 0.0
                    else tuple(g["rings"][0][0]))
        out[key] = {"rings": g["rings"], "bbox": tuple(g["bbox"]), "centroid": centroid}
    return out


def _point_in_rings(x: float, y: float, rings) -> bool:
    """Even-odd (crossing-number) test across all rings — holes & multipart OK."""
    inside = False
    for ring in rings:
        j = len(ring) - 1
        for i in range(len(ring)):
            xi, yi = ring[i]
            xj, yj = ring[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
                inside = not inside
            j = i
    return inside


def random_point_in_ooaza(geom: dict, rng: random.Random, max_tries: int = 10000):
    """Uniform random (lon, lat) inside the ooaza polygon via rejection sampling.

    Falls back to the centroid if no interior point is found within max_tries.
    """
    minx, miny, maxx, maxy = geom["bbox"]
    rings = geom["rings"]
    for _ in range(max_tries):
        x = rng.uniform(minx, maxx)
        y = rng.uniform(miny, maxy)
        if _point_in_rings(x, y, rings):
            return x, y
    return geom["centroid"]


def build_pool(ooaza: list[dict], geom: dict[tuple[str, str], dict]):
    """Eligible ooaza (pop>0 and has geometry) + the cumulative weight partition.

    Returns (pool, cumulative, upper). Each pool entry gains a ``geom`` key.
    """
    pool = []
    for o in ooaza:
        if o["pop"] > 0 and o["key"] in geom:
            entry = dict(o)
            entry["geom"] = geom[o["key"]]
            pool.append(entry)
    if not pool:
        raise SystemExit("No eligible ooaza (positive population with a centroid).")
    pool.sort(key=lambda o: o["pop"], reverse=True)
    total = sum(o["pop"] for o in pool)
    cumulative, running = [], 0.0
    for o in pool:
        running += o["pop"] / total * NORMALISED_TOTAL
        cumulative.append(running)
    return pool, cumulative, cumulative[-1]


def sample_one(pool, cumulative, upper, rng: random.Random) -> dict:
    """Population-weighted draw of one ooaza (with replacement)."""
    idx = bisect.bisect_right(cumulative, rng.uniform(0.0, upper))
    return pool[min(idx, len(pool) - 1)]


def random_date(start: date, end: date, rng: random.Random) -> date:
    """Uniform random date in [start, end] (inclusive)."""
    return start + timedelta(days=rng.randint(0, (end - start).days))


# -------------------------------------------------------------------------------
def get_cfg(cfg: dict, *path):
    node = cfg
    for key in path:
        try:
            node = node[key]
        except (KeyError, TypeError):
            raise SystemExit(f"config: missing '{'/'.join(map(str, path))}'")
    return node


def load_pools(repo_root: Path, prefectures_ja) -> dict[str, tuple]:
    """Build one sampling pool per prefecture code (cached/reused across layers)."""
    pop_tsv = repo_root / "DATA" / "s02_intermediate" / "regional_population.tsv"
    geoshape_dir = repo_root / "DATA" / "s02_intermediate"
    pools: dict[str, tuple] = {}
    for ja in prefectures_ja:
        if ja not in JA_TO_EN_CODE:
            raise SystemExit(f"Unknown prefecture in config: {ja}")
        _, code = JA_TO_EN_CODE[ja]
        if code in pools:
            continue
        shp = geoshape_dir / f"geoshape_{code}" / f"r2ka{code}.shp"
        if not shp.exists():
            raise SystemExit(f"Missing geoshape for {ja}: {shp}")
        pools[code] = build_pool(load_ooaza(pop_tsv, code), build_ooaza_geom(shp))
    return pools


def synthesize(numbers: dict, pools: dict, prefix: str, suffix: str,
               mag1_range, mag2_range, history, business_duration,
               cutoff: date, rng: random.Random) -> list[dict]:
    """Sample POIs for every prefecture in ``numbers`` (with replacement).

    Each row: prefecture(EN), name, lat, lon, mag1, mag2|None, open_date, end_date.
    The name is <prefix><都道府県名><市区町村名><大字・町名><suffix>_<seq>; the
    coordinates are a uniform random point inside the sampled ooaza polygon.
    """
    start, end = history
    width = max(len(str(sum(numbers.values()))), 1)
    rows: list[dict] = []
    seq = 0
    for ja, n in numbers.items():
        _, code = JA_TO_EN_CODE[ja]
        pool, cumulative, upper = pools[code]
        for _ in range(int(n)):
            seq += 1
            o = sample_one(pool, cumulative, upper, rng)
            lon, lat = random_point_in_ooaza(o["geom"], rng)
            mag1 = rng.uniform(*mag1_range)
            mag2 = rng.uniform(*mag2_range) if mag2_range else None
            base = f"{prefix}{ja}{o['muni']}{o['name']}{suffix}"
            open_date = random_date(start, end, rng)
            if business_duration is not None:
                dur = rng.randint(int(business_duration[0]), int(business_duration[1]))
                ed = open_date + timedelta(days=dur)
                end_date = ENDLESS if ed > cutoff else ed.isoformat()
            else:
                end_date = ENDLESS
            rows.append({
                "prefecture": ja, "name": f"{base}_{seq:0{width}d}",
                "lat": lat, "lon": lon, "mag1": mag1, "mag2": mag2,
                "open_date": open_date.isoformat(), "end_date": end_date,
            })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo-root", type=Path, default=None,
                    help="Repo root (auto-detected if omitted).")
    ap.add_argument("--config", type=Path, default=None,
                    help="Path to config.yaml (default <repo-root>/pipeline/config/config.yaml).")
    args = ap.parse_args()

    repo_root = args.repo_root or find_repo_root(Path(__file__).resolve())
    config_path = args.config or (repo_root / "pipeline" / "config" / "config.yaml")
    out_dir = repo_root / "DATA" / "s03_primary"
    out_dir.mkdir(parents=True, exist_ok=True)

    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    seed = int(get_cfg(cfg, "synthetics", "random_seed"))
    rng = random.Random(seed)

    cutoff = datetime.now(JST).date() - timedelta(days=CUTOFF_DAYS_BEFORE_TODAY)
    history = (date(cutoff.year - HISTORY_START_YEARS_BEFORE, 1, 1), cutoff)
    print(f"random_seed={seed}; JST cutoff={cutoff.isoformat()}; "
          f"history={history[0].isoformat()}..{history[1].isoformat()}")

    comp = get_cfg(cfg, "synthetics", "competitors")
    home = get_cfg(cfg, "synthetics", "home_buildings")
    prefectures_ja = list(comp["numbers"]) + list(home["numbers"])
    pools = load_pools(repo_root, prefectures_ja)
    for ja, (_, code) in ((j, JA_TO_EN_CODE[j]) for j in dict.fromkeys(prefectures_ja)):
        print(f"  {ja} ({code}): {len(pools[code][0])} eligible ooaza")

    # --- Competitors ----------------------------------------------------------
    competitors = synthesize(
        comp["numbers"], pools, prefix="競合_", suffix="店",
        mag1_range=tuple(comp["weekday_sales_baseline"]),
        mag2_range=tuple(comp["weekend_sales_baseline"]),
        history=history, business_duration=comp["business_duration"],
        cutoff=cutoff, rng=rng)
    comp_path = out_dir / "competitor.tsv"
    with comp_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["prefecture", "competitor_name", "latitude", "longitude",
                    "weekday_sale_baseline", "weekend_sale_baseline",
                    "open_date", "end_date"])
        for r in competitors:
            w.writerow([r["prefecture"], r["name"], f"{r['lat']:.6f}", f"{r['lon']:.6f}",
                        f"{r['mag1']:.1f}", f"{r['mag2']:.1f}",
                        r["open_date"], r["end_date"]])
    open_now = sum(1 for r in competitors if r["end_date"] == ENDLESS)
    print(f"Wrote {len(competitors)} competitors ({open_now} still open) "
          f"-> {comp_path.relative_to(repo_root)}")

    # --- Home buildings -------------------------------------------------------
    homes = synthesize(
        home["numbers"], pools, prefix="住宅_", suffix="レジデンス",
        mag1_range=tuple(home["unit_range"]),
        mag2_range=None, history=history, business_duration=None,
        cutoff=cutoff, rng=rng)
    home_path = out_dir / "home_building.tsv"
    with home_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["prefecture", "home_building_name", "latitude", "longitude",
                    "unit", "open_date", "end_date"])
        for r in homes:
            w.writerow([r["prefecture"], r["name"], f"{r['lat']:.6f}", f"{r['lon']:.6f}",
                        round(r["mag1"]), r["open_date"], r["end_date"]])
    print(f"Wrote {len(homes)} home buildings -> {home_path.relative_to(repo_root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
