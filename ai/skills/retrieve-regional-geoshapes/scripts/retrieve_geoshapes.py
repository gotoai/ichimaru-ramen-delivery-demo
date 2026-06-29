#!/usr/bin/env python3
"""Retrieve regional (prefecture) boundary shapefiles from the e-stat GIS portal.

For every prefecture referenced in ``docs/profiles/Locations.md`` this script:

1. looks the prefecture up by name in the portal's 地域 (region) list,
2. downloads the 2020 Census small-area boundary ZIP (世界測地系緯度経度・Shape形式),
3. saves the original ZIP as ``DATA/s01_raw/geoshape_<original_zip_filename>``
   (overwriting), and
4. extracts it to ``DATA/s02_intermediate/geoshape_<NN>/`` where ``NN`` is the
   zero-padded 2-digit prefecture (JIS) code (overwriting).

Only the Python standard library is used so the script runs with no extra
dependencies in the project's .venv.

Data source: 統計GIS 国勢調査 2020年 小地域（境界データ） (e-stat).
    https://www.e-stat.go.jp/gis/statmap-search?type=2&toukeiCode=00200521&toukeiYear=2020&serveyId=A002005212020
"""
from __future__ import annotations

import argparse
import html
import io
import json
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

# --- e-stat GIS catalogue coordinates (2020 Census small-area boundaries) ------
BASE = "https://www.e-stat.go.jp"
SERVEY_ID = "A002005212020"   # 国勢調査 2020 boundary survey id
TOUKEI_CODE = "00200521"      # 国勢調査
TOUKEI_YEAR = "2020"
AGG_UNIT = "A"                # 小地域（町丁・字等）
COORD_SYS = "1"               # 緯度経度
DATUM = "2000"                # 世界測地系 (JGD2000)
FORMAT = "shape"
DOWNLOAD_TYPE = "5"           # Shape形式

# JSON endpoint that backs the portal's download list (the 地域 column).
LIST_URL = (
    BASE + "/gis/statmap-search/search_detail"
    f"?type=2&aggregateUnitForBoundary={AGG_UNIT}&toukeiCode={TOUKEI_CODE}"
    f"&toukeiYear={TOUKEI_YEAR}&serveyId={SERVEY_ID}&coordsys={COORD_SYS}"
    f"&format={FORMAT}&datum={DATUM}&download_disp_flg=1&page={{page}}"
)
# Direct file-download endpoint (returns A002005212020DDSWC<code>.zip).
DOWNLOAD_URL = (
    BASE + "/gis/statmap-search/data"
    f"?dlserveyId={SERVEY_ID}&code={{code}}&coordSys={COORD_SYS}"
    f"&format={FORMAT}&downloadType={DOWNLOAD_TYPE}&datum={DATUM}"
)
MAX_LIST_PAGES = 10           # safety cap (47 prefectures, 20 per page)

USER_AGENT = "Mozilla/5.0 (compatible; ichimaru-demo/1.0; +regional-geoshapes skill)"
REQUEST_PAUSE_SEC = 1.0       # be polite between requests

# --- English prefecture name -> Japanese name (as shown on e-stat) -------------
# Locations.md uses English names; the portal 地域 list uses Japanese.
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
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def fetch_with_headers(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=300) as resp:
        return resp.read(), dict(resp.headers)


# -------------------------------------------------------------------------------
def read_target_prefectures(locations_md: Path) -> list[str]:
    """Extract the Japanese prefecture names referenced in Locations.md."""
    text = locations_md.read_text(encoding="utf-8")
    lower = text.lower()
    found: list[str] = []
    for en, ja in EN_TO_JA.items():
        if (re.search(rf"\b{en}\b", lower) or ja in text) and ja not in found:
            found.append(ja)
    if not found:
        raise SystemExit(f"No known prefecture names found in {locations_md}")
    return found


def build_prefecture_index() -> dict[str, str]:
    """Map prefecture name -> 2-digit code from the portal's 地域 list.

    The list is paginated; rows look like::

        ...prefCode=12&...">  <li ...>12 千葉県</li>
    """
    index: dict[str, str] = {}
    for page in range(1, MAX_LIST_PAGES + 1):
        data = json.loads(fetch(LIST_URL.format(page=page)).decode("utf-8", "replace"))
        detail = html.unescape(str(data.get("detail", "")))
        rows = re.findall(
            r'prefCode=(\d{2})[^"]*"[^>]*>\s*<li[^>]*>\s*\d{2}\s+([^<]+?)\s*</li>',
            detail,
        )
        if not rows:
            break
        for code, name in rows:
            index.setdefault(name.strip(), code)
        if len(index) >= 47:
            break
        time.sleep(REQUEST_PAUSE_SEC)
    return index


def original_filename(headers: dict, fallback: str) -> str:
    cd = headers.get("Content-Disposition", "") or headers.get("content-disposition", "")
    m = re.search(r"filename\*=UTF-8''([^;\s]+)", cd)
    if m:
        return urllib.parse.unquote(m.group(1))
    m = re.search(r'filename="?([^";]+)"?', cd)
    if m:
        return m.group(1).strip()
    return fallback


def safe_extract(zip_bytes: bytes, dest_dir: Path) -> list[str]:
    """Extract a ZIP to dest_dir (overwriting), guarding against path traversal."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    root = dest_dir.resolve()
    names: list[str] = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for member in zf.namelist():
            target = (dest_dir / member).resolve()
            if root != target and root not in target.parents:
                raise SystemExit(f"Unsafe path in ZIP: {member}")
            zf.extract(member, dest_dir)
            names.append(member)
    return names


def find_repo_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "docs" / "profiles" / "Locations.md").exists():
            return p
    raise SystemExit("Could not locate repo root (docs/profiles/Locations.md not found).")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", type=Path, default=None,
                    help="Repo root (auto-detected if omitted).")
    args = ap.parse_args()

    repo_root = args.repo_root or find_repo_root(Path(__file__).resolve())
    locations_md = repo_root / "docs" / "profiles" / "Locations.md"
    raw_dir = repo_root / "DATA" / "s01_raw"
    inter_dir = repo_root / "DATA" / "s02_intermediate"
    raw_dir.mkdir(parents=True, exist_ok=True)

    prefectures = read_target_prefectures(locations_md)
    print(f"Target prefectures ({len(prefectures)}): {'、'.join(prefectures)}")

    print("Fetching e-stat GIS 地域 (region) list ...")
    index = build_prefecture_index()

    rc = 0
    for ja in prefectures:
        code = index.get(ja)
        if not code:
            print(f"  [SKIP] {ja}: not found in portal 地域 list")
            rc = 1
            continue

        time.sleep(REQUEST_PAUSE_SEC)
        body, headers = fetch_with_headers(DOWNLOAD_URL.format(code=code))
        fname = original_filename(headers, fallback=f"{SERVEY_ID}DDSWC{code}.zip")

        zip_path = raw_dir / f"geoshape_{fname}"
        zip_path.write_bytes(body)                     # overwrite

        dest = inter_dir / f"geoshape_{code}"
        members = safe_extract(body, dest)             # overwrite

        print(f"  [OK]   {ja} (code {code}): {zip_path.relative_to(repo_root)} "
              f"({len(body):,} bytes) -> {dest.relative_to(repo_root)}/ "
              f"[{', '.join(members)}]")

    return rc


if __name__ == "__main__":
    sys.exit(main())
