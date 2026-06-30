#!/usr/bin/env python3
"""Synthesize Ichimaru events (people-gathering activities that move food demand).

Reuses the **Location sampling algorithm** of the ``synthesize-stores`` skill: per
prefecture, sample ooaza (大字・町) with probability proportional to 2020 Census
population. Like the POI layers, events are sampled **with replacement** and each
is dropped at a **uniform random point inside the sampled ooaza polygon** (not the
centroid); a per-file ``_<seq>`` suffix keeps names unique.

Each event also gets an ``event_date``: a random date over the ``sales_history``
window, with days in **April, August and October** weighted 5x relative to other
days (so events bunch into those months).

Driven by ``config/config.yaml``:
  * ``synthetics/random_seed``
  * ``synthetics/events/{numbers, people_range}``  (numbers: 都道府県名 -> count)

Time window follows the prose rule in ``time_horizon/sales_history`` (in JST):
  * end   = the date two days before today (cutoff)
  * start = Jan 1 of (cutoff_year - 2)

Output (overwritten each run): ``DATA/s03_primary/event.tsv`` with columns
  prefecture, event_name, latitude, longitude, people, event_date

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
CUTOFF_DAYS_BEFORE_TODAY = 2          # sales_history end = 2 days before today (JST)
SALES_HISTORY_START_YEARS_BEFORE = 2  # sales_history start = Jan 1, end_year - 2
BOOST_MONTHS = frozenset({4, 8, 10})  # April, August, October
BOOST_WEIGHT = 5                      # 5x the weight of an ordinary day

# --- Japanese prefecture name -> (English display name, 2-digit JIS code) -------
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
        if (p / "docs" / "profiles" / "Locations.md").exists():
            return p
    raise SystemExit("Could not locate repo root (docs/profiles/Locations.md not found).")


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
    "centroid": (lon, lat)}``. Events are placed at a uniform random point inside
    the rings; the centroid is kept only as a rejection-sampling fallback.
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
    """Uniform random (lon, lat) inside the ooaza polygon via rejection sampling."""
    minx, miny, maxx, maxy = geom["bbox"]
    rings = geom["rings"]
    for _ in range(max_tries):
        x = rng.uniform(minx, maxx)
        y = rng.uniform(miny, maxy)
        if _point_in_rings(x, y, rings):
            return x, y
    return geom["centroid"]


def build_pool(ooaza: list[dict], geom: dict[tuple[str, str], dict]):
    """Eligible ooaza (pop>0 and has geometry) + the cumulative weight partition."""
    pool = []
    for o in ooaza:
        if o["pop"] > 0 and o["key"] in geom:
            entry = dict(o)
            entry["geom"] = geom[o["key"]]
            pool.append(entry)
    if not pool:
        raise SystemExit("No eligible ooaza (positive population with geometry).")
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


def build_date_partition(start: date, end: date):
    """Weighted partition over [start, end]: BOOST_MONTHS days weigh BOOST_WEIGHT,

    all other days weigh 1. Returns (dates, cumulative, total)."""
    dates, cumulative, running = [], [], 0
    for i in range((end - start).days + 1):
        d = start + timedelta(days=i)
        running += BOOST_WEIGHT if d.month in BOOST_MONTHS else 1
        dates.append(d)
        cumulative.append(running)
    return dates, cumulative, running


def sample_date(dates, cumulative, total, rng: random.Random) -> date:
    """Weighted random date from a partition built by build_date_partition."""
    idx = bisect.bisect_right(cumulative, rng.uniform(0.0, total))
    return dates[min(idx, len(dates) - 1)]


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
    """Build one sampling pool per prefecture code."""
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo-root", type=Path, default=None,
                    help="Repo root (auto-detected if omitted).")
    ap.add_argument("--config", type=Path, default=None,
                    help="Path to config.yaml (default <repo-root>/config/config.yaml).")
    args = ap.parse_args()

    repo_root = args.repo_root or find_repo_root(Path(__file__).resolve())
    config_path = args.config or (repo_root / "config" / "config.yaml")
    out_dir = repo_root / "DATA" / "s03_primary"
    out_dir.mkdir(parents=True, exist_ok=True)

    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    seed = int(get_cfg(cfg, "synthetics", "random_seed"))
    rng = random.Random(seed)

    cutoff = datetime.now(JST).date() - timedelta(days=CUTOFF_DAYS_BEFORE_TODAY)
    start = date(cutoff.year - SALES_HISTORY_START_YEARS_BEFORE, 1, 1)
    dates, date_cum, date_total = build_date_partition(start, cutoff)
    print(f"random_seed={seed}; sales_history={start.isoformat()}..{cutoff.isoformat()}; "
          f"boost x{BOOST_WEIGHT} in months {sorted(BOOST_MONTHS)}")

    events_cfg = get_cfg(cfg, "synthetics", "events")
    numbers = events_cfg["numbers"]
    people_range = tuple(events_cfg["people_range"])
    pools = load_pools(repo_root, list(numbers))
    for ja in dict.fromkeys(numbers):
        code = JA_TO_EN_CODE[ja][1]
        print(f"  {ja} ({code}): {len(pools[code][0])} eligible ooaza")

    width = max(len(str(sum(int(n) for n in numbers.values()))), 1)
    rows: list[dict] = []
    seq = 0
    for ja, n in numbers.items():
        en, code = JA_TO_EN_CODE[ja]
        pool, cumulative, upper = pools[code]
        for _ in range(int(n)):
            seq += 1
            o = sample_one(pool, cumulative, upper, rng)
            lon, lat = random_point_in_ooaza(o["geom"], rng)
            people = round(rng.uniform(*people_range))
            event_date = sample_date(dates, date_cum, date_total, rng)
            name = f"イベント_{ja}{o['muni']}{o['name']}_{seq:0{width}d}"
            rows.append({"prefecture": en, "name": name, "lat": lat, "lon": lon,
                         "people": people, "event_date": event_date.isoformat()})

    out_path = out_dir / "event.tsv"
    with out_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t")
        w.writerow(["prefecture", "event_name", "latitude", "longitude",
                    "people", "event_date"])
        for r in rows:
            w.writerow([r["prefecture"], r["name"], f"{r['lat']:.6f}", f"{r['lon']:.6f}",
                        r["people"], r["event_date"]])

    boosted = sum(1 for r in rows if int(r["event_date"][5:7]) in BOOST_MONTHS)
    print(f"Wrote {len(rows)} events ({boosted} in boost months) "
          f"-> {out_path.relative_to(repo_root)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
