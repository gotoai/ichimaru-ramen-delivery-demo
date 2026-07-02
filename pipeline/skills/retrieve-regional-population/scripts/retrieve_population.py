#!/usr/bin/env python3
"""Retrieve regional (prefecture) population CSVs from the e-stat portal.

For every prefecture referenced in ``docs/pipeline/profiles/Locations.md`` this script
downloads the 2020 Population Census small-area-aggregation table:

    表番号 (table no.) = 2
    統計表 (table)     = 男女別人口，外国人人口及び世帯数－町丁・字等

and stores the original CSV as ``DATA/s01_raw/population_<original_filename>``.

The relevant rows of every downloaded CSV (Shift_JIS) are then extracted and
appended, as UTF-8 tab-separated values, to a single combined file
``DATA/s02_intermediate/regional_population.tsv``. That file is truncated (and a
header written) on the first append of a run, so re-running never duplicates rows.

Only the Python standard library is used so the script runs with no extra
dependencies in the project's .venv.

Data source: 国勢調査 令和2年国勢調査 小地域集計 (e-stat).
    https://www.e-stat.go.jp/stat-search/files?toukei=00200521&tstat=000001136464&tclass1=000001136472
"""
from __future__ import annotations

import argparse
import csv
import html
import io
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

# --- e-stat catalogue coordinates (2020 Census, small-area aggregation) --------
BASE = "https://www.e-stat.go.jp"
LISTING_URL = (
    BASE + "/stat-search/files?page=1"
    "&toukei=00200521"          # 国勢調査
    "&tstat=000001136464"       # 令和2年国勢調査
    "&tclass1=000001136472"     # 小地域集計
)
TARGET_TABLE_NO = "2"
TARGET_TABLE_NAME = "男女別人口，外国人人口及び世帯数－町丁・字等"

USER_AGENT = "Mozilla/5.0 (compatible; ichimaru-demo/1.0; +regional-population skill)"
REQUEST_PAUSE_SEC = 1.0  # be polite between requests

# --- Combined intermediate output ----------------------------------------------
# Each downloaded census CSV is Shift_JIS (CP932). Its data rows are mapped to the
# columns below and appended, UTF-8 tab-separated, to a single combined file.
INTERMEDIATE_TSV = Path("DATA") / "s02_intermediate" / "regional_population.tsv"
SRC_ENCODING = "cp932"
OUTPUT_COLUMNS = [
    "市区町村コード", "町丁字コード", "地域階層レベル", "都道府県名", "市区町村名",
    "大字・町名", "字・丁目名", "総数", "男", "女", "外国人人口", "世帯数",
]
# Source field indices (the source CSV has a leading sequential-number column, so
# field 0 is that index; the municipal code is field 1, etc.). 秘匿処理/秘匿先情報/
# 合算地域 (fields 4-6) are intentionally dropped.
SRC_FIELD_INDICES = [1, 2, 3, 7, 8, 9, 10, 11, 12, 13, 14, 15]

# --- English prefecture name -> Japanese name (as shown on e-stat) -------------
# Locations.md uses English names; the portal facets use Japanese.
EN_TO_JA = {
    "hokkaido": "北海道", "aomori": "青森県", "iwate": "岩手県", "miyagi": "宮城県",
    "akita": "秋田県", "yamagata": "山形県", "fukushima": "福島県", "ibaraki": "茨城県",
    "tochigi": "栃木県", "gunma": "群馬県", "saitama": "埼玉県", "chiba": "千葉県",
    "tokyo": "東京都", "kanagawa": "神奈川県", "niigata": "新潟県", "toyama": "富山県",
    "ishikawa": "石川県", "fukui": "福井県", "yamanashi": "山梨県", "nagano": "長野県",
    "gifu": "岐阜県", "shizuoka": "静岡県", "aichi": "愛知県", "mie": "三重県",
    "shiga": "滋賀県", "kyoto": "京都府", "osaka": "大阪府", "hyogo": "兵庫県",
    "nara": "奈良県", "wakayama": "和歌山県", "tottori": "鳥取県", "shimane": "島根県",
    "okayama": "岡山県", "hiroshima": "広島県", "yamaguchi": "山口県", "tokushima": "徳島県",
    "kagawa": "香川県", "ehime": "愛媛県", "kochi": "高知県", "fukuoka": "福岡県",
    "saga": "佐賀県", "nagasaki": "長崎県", "kumamoto": "熊本県", "oita": "大分県",
    "miyazaki": "宮崎県", "kagoshima": "鹿児島県", "okinawa": "沖縄県",
}


def fetch(url: str) -> bytes:
    """GET a URL with a browser-like User-Agent and return the raw body."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def fetch_with_headers(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read(), dict(resp.headers)


# -------------------------------------------------------------------------------
def read_target_prefectures(locations_md: Path) -> list[str]:
    """Extract the Japanese prefecture names referenced in Locations.md."""
    text = locations_md.read_text(encoding="utf-8")
    found: list[str] = []
    lower = text.lower()
    for en, ja in EN_TO_JA.items():
        # match the English name as a word, or the Japanese name directly.
        if re.search(rf"\b{en}\b", lower) or ja in text:
            if ja not in found:
                found.append(ja)
    if not found:
        raise SystemExit(f"No known prefecture names found in {locations_md}")
    return found


def build_prefecture_index(listing_html: str) -> dict[str, str]:
    """Map Japanese prefecture name -> tclass2 facet id from the listing page."""
    index: dict[str, str] = {}
    for m in re.finditer(r"tclass2=(\d+)[^>]*?>(.*?)</a>", listing_html, re.S):
        tid = m.group(1)
        label = html.unescape(re.sub(r"<[^>]+>", "", m.group(2)))
        # labels look like "13：東京都 [30件] ..." -> pull the prefecture name.
        name = re.search(r"[：:]\s*([^\s\[]+[都道府県])", label)
        if name:
            index.setdefault(name.group(1), tid)
    return index


def prefecture_page_url(tclass2: str) -> str:
    return (
        BASE + "/stat-search/files?page=1&layout=datalist&cycle=0"
        "&toukei=00200521&tstat=000001136464&tclass1=000001136472"
        f"&tclass2={tclass2}&cycle_facet=tclass1&tclass3val=0"
    )


def find_target_download(pref_html: str) -> str | None:
    """Return the absolute CSV download URL for the target table, or None.

    Each file is wrapped in <article class="stat-dataset_list-item">. We pick the
    article whose 表番号 == TARGET_TABLE_NO and whose 統計表 link text equals
    TARGET_TABLE_NAME, then take its CSV file-download href.
    """
    articles = re.split(r'<article class="stat-dataset_list-item">', pref_html)
    for art in articles:
        no_m = re.search(
            r'stat-sp">\s*表番号[^<]*</span>\s*<span>\s*(\d+)\s*</span>', art
        )
        if not no_m or no_m.group(1) != TARGET_TABLE_NO:
            continue
        name_m = re.search(
            r'class="stat-link_text[^"]*js-data"[^>]*>\s*(.*?)\s*</a>', art, re.S
        )
        name = html.unescape(re.sub(r"\s+", "", name_m.group(1))) if name_m else ""
        if TARGET_TABLE_NAME.replace("，", "") not in name.replace(",", "，").replace("，", ""):
            # tolerant comparison (full-width comma variations)
            if name != TARGET_TABLE_NAME:
                continue
        dl_m = re.search(
            r'href="(/stat-search/file-download\?statInfId=\d+&(?:amp;)?fileKind=1)"'
            r'[^>]*data-file_type="CSV"',
            art,
        )
        if dl_m:
            return BASE + html.unescape(dl_m.group(1))
    return None


def original_filename(headers: dict, fallback: str) -> str:
    """Extract the server-provided filename from Content-Disposition."""
    cd = headers.get("Content-Disposition", "") or headers.get("content-disposition", "")
    m = re.search(r"filename\*=UTF-8''([^;\s]+)", cd)
    if m:
        return urllib.parse.unquote(m.group(1))
    m = re.search(r'filename="?([^";]+)"?', cd)
    if m:
        return m.group(1).strip()
    return fallback


def extract_population_rows(csv_bytes: bytes) -> list[list[str]]:
    """Map the data rows of a census CSV to OUTPUT_COLUMNS.

    Data rows are those whose 市区町村コード (source field 1) is a 5-digit code;
    title/header rows are skipped. Values are carried verbatim, including the
    census placeholders 'X' (suppressed) and '-' (none/zero).
    """
    text = csv_bytes.decode(SRC_ENCODING, "replace")
    rows: list[list[str]] = []
    for fields in csv.reader(io.StringIO(text)):
        if len(fields) <= SRC_FIELD_INDICES[-1]:
            continue
        if not re.fullmatch(r"\d{5}", fields[1].strip()):
            continue
        rows.append([fields[i].strip() for i in SRC_FIELD_INDICES])
    return rows


def append_rows(tsv_path: Path, rows: list[list[str]], truncate: bool) -> None:
    """Write rows to the combined TSV. When ``truncate`` is True the file is
    recreated and the header is written first; otherwise rows are appended."""
    tsv_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "w" if truncate else "a"
    with open(tsv_path, mode, encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        if truncate:
            w.writerow(OUTPUT_COLUMNS)
        w.writerows(rows)


def find_repo_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "docs" / "pipeline" / "profiles" / "Locations.md").exists():
            return p
    raise SystemExit("Could not locate repo root (docs/pipeline/profiles/Locations.md not found).")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", type=Path, default=None,
                    help="Repo root (auto-detected if omitted).")
    ap.add_argument("--out-dir", type=Path, default=None,
                    help="Output dir (default: <repo-root>/DATA/s01_raw).")
    args = ap.parse_args()

    repo_root = args.repo_root or find_repo_root(Path(__file__).resolve())
    locations_md = repo_root / "docs" / "pipeline" / "profiles" / "Locations.md"
    out_dir = args.out_dir or (repo_root / "DATA" / "s01_raw")
    out_dir.mkdir(parents=True, exist_ok=True)

    prefectures = read_target_prefectures(locations_md)
    print(f"Target prefectures ({len(prefectures)}): {'、'.join(prefectures)}")

    print("Fetching e-stat listing page ...")
    listing = fetch(LISTING_URL).decode("utf-8", "replace")
    index = build_prefecture_index(listing)

    tsv_path = repo_root / INTERMEDIATE_TSV
    first_append = True  # truncate the combined TSV on the first append of this run

    rc = 0
    for ja in prefectures:
        tclass2 = index.get(ja)
        if not tclass2:
            print(f"  [SKIP] {ja}: not found in listing facets")
            rc = 1
            continue
        time.sleep(REQUEST_PAUSE_SEC)
        pref_html = fetch(prefecture_page_url(tclass2)).decode("utf-8", "replace")
        dl_url = find_target_download(pref_html)
        if not dl_url:
            print(f"  [SKIP] {ja}: target table (表番号={TARGET_TABLE_NO}) not found")
            rc = 1
            continue

        time.sleep(REQUEST_PAUSE_SEC)
        sid = re.search(r"statInfId=(\d+)", dl_url).group(1)
        body, headers = fetch_with_headers(dl_url)
        fname = original_filename(headers, fallback=f"{sid}.csv")
        dest = out_dir / f"population_{fname}"
        dest.write_bytes(body)

        rows = extract_population_rows(body)
        append_rows(tsv_path, rows, truncate=first_append)
        first_append = False
        print(
            f"  [OK]   {ja}: {dest.relative_to(repo_root)} ({len(body):,} bytes)"
            f" -> {len(rows):,} rows appended to {tsv_path.relative_to(repo_root)}"
        )

    return rc


if __name__ == "__main__":
    sys.exit(main())
