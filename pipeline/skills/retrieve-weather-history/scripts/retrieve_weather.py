#!/usr/bin/env python3
"""Retrieve daily weather history from the JMA "過去の気象データ・ダウンロード" portal.

For every prefecture referenced in ``docs/pipeline/profiles/Locations.md`` this script
downloads daily-value (日別値) weather CSVs for all observation stations in the
prefecture, one calendar month at a time, and saves each as
``DATA/s01_raw/weather-history-<prefecture>-<YYYY-MM-01>.csv``.

The default period runs from January three years before the end year up to the
month of the date two days before today (the final month is truncated at that
day), i.e. at least three full calendar years computed from the system date
(e.g. run on 2026-06-15 -> 2023-01 .. 2026-06, the last month covering days 1..13).

It speaks the portal's own POST API directly (no headless browser); only the
Python standard library is used.

Elements retrieved (daily values):
    日平均気温(201) 日最高気温(202) 日最低気温(203) 降水量の日合計(101)
    降雪量の日合計(503) 日平均風速(301) 日最大風速(302) 日平均相対湿度(605)
    日平均雲量(607) 天気概況・昼(701)

Source portal: https://www.data.jma.go.jp/risk/obsdl/index.php

NOTE: the JMA portal asks users to refrain from excessive automated access.
This script downloads month-by-month and pauses WAIT_BETWEEN_DOWNLOAD_ONCE
seconds between downloads; keep that courtesy in place.
"""
from __future__ import annotations

import argparse
import calendar
import csv
import datetime
import http.client
import http.cookiejar
import io
import json
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

JST = datetime.timezone(datetime.timedelta(hours=9))  # config time_horizon is in JST

# --- JMA obsdl endpoints -------------------------------------------------------
ROOT = "https://www.data.jma.go.jp/risk/obsdl"
INDEX_URL = ROOT + "/index.php"
STATION_URL = ROOT + "/top/station"
DOWNLOAD_URL = ROOT + "/show/table"

# --- Fixed request configuration (verified against the portal) -----------------
AGGRG_PERIOD = "1"        # 日別値 (daily values)
INTER_ANNUAL_TYPE = "1"   # 連続した期間で表示する
# elementNumList: [elementCode, option]; option is "" for these plain elements.
ELEMENTS = [
    ["201", ""],  # 日平均気温
    ["202", ""],  # 日最高気温
    ["203", ""],  # 日最低気温
    ["101", ""],  # 降水量の日合計
    ["503", ""],  # 降雪量の日合計
    ["301", ""],  # 日平均風速
    ["302", ""],  # 日最大風速
    ["605", ""],  # 日平均相対湿度
    ["607", ""],  # 日平均雲量
    ["701", ""],  # 天気概況（昼：06時～18時）
]
# Display options (表示オプションを選ぶ):
#   rmkFlag=1        値を表示する（利用上注意が必要な情報をつける）
#   csvFlag=1        すべて数値で格納
#   youbiFlag=1      日付に曜日を表示（日別値）
#   fukenFlag=1      都道府県名を格納
# disconnectFlag / ymdLiteral kept at portal defaults (1).
OPTION_FLAGS = {
    "rmkFlag": "1", "disconnectFlag": "1", "csvFlag": "1", "ymdLiteral": "1",
    "youbiFlag": "1", "fukenFlag": "1", "kijiFlag": "0",
    "jikantaiFlag": "0",
}

WAIT_BETWEEN_DOWNLOAD_ONCE = 2  # seconds to pause between downloads

# --- Network retry -------------------------------------------------------------
# The JMA portal occasionally times out or drops a connection mid-download. Each
# HTTP request is retried up to NET_ATTEMPTS times, pausing RETRY_BACKOFF * attempt
# seconds between tries, before giving up and re-raising.
NET_ATTEMPTS = 3
RETRY_BACKOFF = 3  # seconds (multiplied by the attempt number)
# Transient errors worth retrying: timeouts, dropped/refused connections, and a
# download that aborts in the middle (IncompleteRead).
RETRYABLE_ERRORS = (
    urllib.error.URLError,
    http.client.HTTPException,
    socket.timeout,
    TimeoutError,
    ConnectionError,
)


def with_retry(func, what: str, attempts: int = NET_ATTEMPTS):
    """Call ``func()``, retrying transient network errors up to ``attempts`` times.

    On a retryable error the call is retried after a short, growing backoff; if
    every attempt fails the last exception is re-raised so the caller still sees
    the failure. ``what`` is a short label used in the warning lines (stderr).
    """
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except RETRYABLE_ERRORS as exc:
            # HTTP error responses (4xx/5xx) other than the transient 5xx ones
            # are not worth retrying — treat them as a hard failure.
            if isinstance(exc, urllib.error.HTTPError) and exc.code < 500:
                raise
            if attempt >= attempts:
                sys.stderr.write(
                    f"\n[retry] {what}: failed after {attempts} attempts ({exc}); giving up.\n"
                )
                raise
            wait = RETRY_BACKOFF * attempt
            sys.stderr.write(
                f"\n[retry] {what}: attempt {attempt}/{attempts} failed ({exc}); "
                f"retrying in {wait}s ...\n"
            )
            sys.stderr.flush()
            time.sleep(wait)


def default_period():
    """Default (start, end, end_day), inclusive, relative to the system date.

    End   = the month of the date two days before today; the final month is
            truncated at that day (end_day), so it may be a partial month.
    Start = January of the year three years before the end year (>= three full
            calendar years).

    e.g. run on 2026-06-15 -> 2023-01 .. 2026-06, the final month covering only
    days 1..13. "Today" is evaluated in JST, matching pipeline/config/config.yaml.
    """
    end_date = datetime.datetime.now(JST).date() - datetime.timedelta(days=2)
    start_year = end_date.year - 3
    return (start_year, 1), (end_date.year, end_date.month), end_date.day

# --- Long-format (TSV) schema --------------------------------------------------
# The downloaded CSV is "wide": two leading columns (年月日, 曜日) followed by one
# variable-width block of columns per station. Each element contributes a value
# column plus quality/homogeneity sub-columns (and 風向 for 最大風速); 降水量 and
# 降雪量 additionally carry a 現象なし情報 column which is dropped here.
META_COLUMNS = ["都道府県", "観測地点", "日付", "曜日"]
ELEMENT_COLUMNS = [
    "平均気温(℃)", "平均気温(℃)_品質情報", "平均気温(℃)_均質番号",
    "最高気温(℃)", "最高気温(℃)_品質情報", "最高気温(℃)_均質番号",
    "最低気温(℃)", "最低気温(℃)_品質情報", "最低気温(℃)_均質番号",
    "降水量の合計(mm)", "降水量の合計(mm)_品質情報", "降水量の合計(mm)_均質番号",
    "降雪量合計(cm)", "降雪量合計(cm)_品質情報", "降雪量合計(cm)_均質番号",
    "平均風速(m/s)", "平均風速(m/s)_品質情報", "平均風速(m/s)_均質番号",
    "最大風速(m/s)", "最大風速(m/s)_品質情報",
    "最大風速(m/s)_風向", "最大風速(m/s)_風向_品質情報", "最大風速(m/s)_均質番号",
    "平均湿度(％)", "平均湿度(％)_品質情報", "平均湿度(％)_均質番号",
    "平均雲量(10分比)", "平均雲量(10分比)_品質情報", "平均雲量(10分比)_均質番号",
    "天気概況(昼：06時～18時)", "天気概況(昼：06時～18時)_品質情報", "天気概況(昼：06時～18時)_均質番号",
]
LONG_COLUMNS = META_COLUMNS + ELEMENT_COLUMNS

USER_AGENT = "Mozilla/5.0 (compatible; ichimaru-demo/1.0; +retrieve-weather-history skill)"

# --- English prefecture name -> full Japanese name (as in Locations.md) --------
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


def make_session():
    cj = http.cookiejar.CookieJar()  # in-memory jar handles the HttpOnly ci_session
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    opener.addheaders = [("User-Agent", USER_AGENT)]
    return opener


def open_session(opener):
    """GET index.php to (re)establish the ci_session cookie."""
    with_retry(lambda: opener.open(INDEX_URL, timeout=60).read(), "GET index.php")


def post(opener, url, data, timeout=180):
    body = urllib.parse.urlencode(data).encode()

    def _do():
        req = urllib.request.Request(url, data=body, headers={"Referer": INDEX_URL})
        resp = opener.open(req, timeout=timeout)
        return resp, resp.read()

    return with_retry(_do, f"POST {url}")


# -------------------------------------------------------------------------------
def read_target_prefectures(locations_md: Path) -> list[str]:
    """Extract the full Japanese prefecture names referenced in Locations.md."""
    text = locations_md.read_text(encoding="utf-8")
    lower = text.lower()
    found: list[str] = []
    for en, ja in EN_TO_JA.items():
        if (re.search(rf"\b{en}\b", lower) or ja in text) and ja not in found:
            found.append(ja)
    if not found:
        raise SystemExit(f"No known prefecture names found in {locations_md}")
    return found


def build_prefecture_index(opener) -> dict[str, str]:
    """Map JMA area name -> pd code from the portal's 地点を選ぶ map (pd=00)."""
    _, body = post(opener, STATION_URL, {"pd": "00"})
    html = body.decode("utf-8", "replace")
    index: dict[str, str] = {}
    for pd, name in re.findall(
        r'<div class="prefecture" id="pr(\d+)">([^<]+)<input[^>]*name="prid"', html
    ):
        index.setdefault(name.strip(), pd)
    return index


def resolve_pd(full_name: str, index: dict[str, str]) -> tuple[str, str] | None:
    """Resolve a full prefecture name (e.g. 東京都) to (jma_name, pd).

    The JMA map uses short names (東京, 神奈川, ...). We strip a trailing
    都/道/府/県 and look for an exact match.
    """
    short = re.sub(r"[都道府県]$", "", full_name)
    if short in index:
        return short, index[short]
    if full_name in index:
        return full_name, index[full_name]
    return None


def get_stations(opener, pd: str) -> list[str]:
    """Return all station ids (stid) for a prefecture (pd), in display order."""
    _, body = post(opener, STATION_URL, {"pd": pd})
    html = body.decode("utf-8", "replace")
    stids: list[str] = []
    for block in re.findall(r'<div[^>]*class="station[^"]*"[^>]*>(.*?)</div>', html, re.S):
        m_id = re.search(r'name="stid"[^>]*value="([^"]*)"', block)
        m_pr = re.search(r'name="prid"[^>]*value="([^"]*)"', block)
        if m_id and m_pr and m_pr.group(1) == pd:   # exclude movepr / other prefectures
            sid = m_id.group(1)
            if sid not in stids:
                stids.append(sid)
    return stids


def month_iter(start, end):
    y, m = start
    while (y, m) <= end:
        yield y, m
        m += 1
        if m > 12:
            y, m = y + 1, 1


def download_month(opener, stids, year, month, last_day):
    """POST show/table for one month (days 1..last_day); CSV bytes or None."""
    ymd = [str(year), str(year), str(month), str(month), "1", str(last_day)]
    data = {
        "stationNumList": json.dumps(stids),
        "aggrgPeriod": AGGRG_PERIOD,
        "elementNumList": json.dumps(ELEMENTS),
        "interAnnualType": INTER_ANNUAL_TYPE,
        "ymdList": json.dumps(ymd),
        "optionNumList": json.dumps([]),
        "jikantaiList": json.dumps([]),
        "downloadFlag": "true",
        **OPTION_FLAGS,
    }
    resp, body = post(opener, DOWNLOAD_URL, data)
    ctype = resp.headers.get("Content-Type", "")
    if "octet-stream" not in ctype and b"DOCTYPE" in body[:200]:
        return None     # session expired / rejected -> caller retries
    return body


def _column_label(element: str, sub5: str, sub6: str) -> str | None:
    """Build the long-format column name for one wide column, or None to drop it.

    sub5 carries the 風向 sub-label (最大風速 only); sub6 carries
    品質情報 / 均質番号 / 現象なし情報. 現象なし情報 columns are dropped.
    """
    if sub6 == "現象なし情報":
        return None
    parts = [element]
    if sub5:
        parts.append(sub5)
    if sub6:
        parts.append(sub6)
    return "_".join(parts)


def wide_to_long(csv_bytes: bytes) -> list[list[str]]:
    """Convert a JMA wide-format CSV (CP932) into long-format rows (LONG_COLUMNS).

    One output row per (station, date); element columns missing for a station
    (e.g. AMeDAS sites that do not observe 雲量/天気概況) are left blank.
    """
    rows = list(csv.reader(io.StringIO(csv_bytes.decode("cp932", "replace"))))
    pref, station, element, sub5, sub6 = rows[2], rows[3], rows[4], rows[5], rows[6]

    # Split the wide columns into per-station blocks (a block starts when the
    # station name changes) and map each block's columns to LONG_COLUMNS names.
    blocks: list[tuple[str, str, dict[str, int]]] = []
    for j in range(2, len(element)):
        if not blocks or station[j] != blocks[-1][1]:
            blocks.append((pref[j], station[j], {}))
        label = _column_label(element[j], sub5[j], sub6[j])
        if label is not None:
            blocks[-1][2][label] = j

    out: list[list[str]] = []
    for drow in rows[7:]:
        if not drow or not drow[0]:
            continue
        date = drow[0]
        weekday = drow[1] if len(drow) > 1 else ""
        for pref_name, station_name, colmap in blocks:
            rec = [pref_name, station_name, date, weekday]
            for col in ELEMENT_COLUMNS:
                j = colmap.get(col)
                rec.append(drow[j] if (j is not None and j < len(drow)) else "")
            out.append(rec)
    return out


def write_long_tsv(csv_bytes: bytes, dest: Path) -> int:
    """Write the long-format TSV (UTF-8). Returns the number of data rows."""
    rows = wide_to_long(csv_bytes)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, delimiter="\t", lineterminator="\n")
        w.writerow(LONG_COLUMNS)
        w.writerows(rows)
    return len(rows)


def fetch_month(opener, stids, year, month, last_day):
    """Download one month (days 1..last_day), retrying once after a refresh."""
    body = download_month(opener, stids, year, month, last_day)
    if body is None:
        open_session(opener)
        body = download_month(opener, stids, year, month, last_day)
    return body


def save_outputs(body, token, year, month, raw_dir, long_dir):
    """Save the raw CSV and the long-format TSV; return (raw_path, tsv_path, n_rows)."""
    stamp = f"{year}-{month:02d}-01"
    raw = raw_dir / f"weather-history-{token}-{stamp}.csv"
    raw.write_bytes(body)
    tsv = long_dir / f"weather_history_{token}_{stamp}.tsv"
    n = write_long_tsv(body, tsv)
    return raw, tsv, n


class Progress:
    """A tiny progress bar with a refreshable status line (stdlib only).

    On a TTY the bar and the currently-downloading month are redrawn in place via
    a carriage return; when output is piped/redirected it prints one plain line
    per completed download instead.
    """

    def __init__(self, total: int, prefix: str = "Weather history", width: int = 28):
        self.total = total
        self.done = 0
        self.prefix = prefix
        self.width = width
        self.tty = sys.stdout.isatty()
        self.start = time.monotonic()

    def _line(self, label: str) -> str:
        frac = self.done / self.total if self.total else 1.0
        filled = int(round(self.width * frac))
        bar = "=" * filled + "-" * (self.width - filled)
        elapsed = time.monotonic() - self.start
        # Total = running time stretched to 100% by the completed fraction (the
        # projected total runtime); unknown until the first download completes.
        total = f"Total {elapsed / frac:.0f} sec" if self.done else "Total -- sec"
        return (f"{self.prefix} |{bar}| {frac * 100:3.0f}%  "
                f"({elapsed:.0f} sec / {total})  {self.done}/{self.total}  {label}")

    def show(self, label: str) -> None:
        """Refresh the status line for the in-progress month (TTY only)."""
        if self.tty:
            sys.stdout.write("\r\033[K" + self._line(label))
            sys.stdout.flush()

    def advance(self, label: str) -> None:
        """Mark one download complete and redraw the bar."""
        self.done += 1
        if self.tty:
            sys.stdout.write("\r\033[K" + self._line(label))
            sys.stdout.flush()
        else:
            print(self._line(label))

    def note(self, message: str) -> None:
        """Print a permanent line (e.g. a heading or failure) above the bar."""
        if self.tty:
            sys.stdout.write("\r\033[K")
        print(message)

    def close(self) -> None:
        if self.tty:
            sys.stdout.write("\n")
            sys.stdout.flush()


def find_repo_root(start: Path) -> Path:
    for p in [start, *start.parents]:
        if (p / "docs" / "pipeline" / "profiles" / "Locations.md").exists():
            return p
    raise SystemExit("Could not locate repo root (docs/pipeline/profiles/Locations.md not found).")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo-root", type=Path, default=None)
    ap.add_argument("--start", default=None,
                    help="YYYY-MM (default: January of the end year minus three)")
    ap.add_argument("--end", default=None,
                    help="YYYY-MM (default: the month two days before today; "
                         "an explicit value covers the whole month)")
    ap.add_argument("--prefectures", default=None,
                    help="comma-separated full names to override Locations.md")
    ap.add_argument("--per-prefecture", action="store_true",
                    help="force one download per prefecture (skip the combined attempt)")
    args = ap.parse_args()

    repo_root = args.repo_root or find_repo_root(Path(__file__).resolve())
    out_dir = repo_root / "DATA" / "s01_raw"
    long_dir = repo_root / "DATA" / "s02_intermediate"
    out_dir.mkdir(parents=True, exist_ok=True)

    default_start, default_end, default_end_day = default_period()
    start = tuple(int(x) for x in args.start.split("-")) if args.start else default_start
    end = tuple(int(x) for x in args.end.split("-")) if args.end else default_end
    # The default end month is truncated two days before today; an explicit --end
    # is treated as a whole month.
    end_day = calendar.monthrange(*end)[1] if args.end else default_end_day

    def last_day_of(year, month):
        return end_day if (year, month) == end else calendar.monthrange(year, month)[1]

    if args.prefectures:
        prefectures = [p.strip() for p in args.prefectures.split(",") if p.strip()]
    else:
        prefectures = read_target_prefectures(repo_root / "docs" / "pipeline" / "profiles" / "Locations.md")
    months = list(month_iter(start, end))
    print(f"Prefectures: {'、'.join(prefectures)}")
    print(f"Period: {start[0]}-{start[1]:02d} .. {end[0]}-{end[1]:02d} ({len(months)} months)")

    opener = make_session()
    open_session(opener)
    index = build_prefecture_index(opener)

    # Resolve every prefecture and fetch its station list up front.
    rc = 0
    resolved = []   # (full_name, jma_name, pd, stids)
    for full_name in prefectures:
        r = resolve_pd(full_name, index)
        if not r:
            print(f"  [SKIP] {full_name}: no single JMA area (Hokkaido needs sub-areas)")
            rc = 1
            continue
        jma_name, pd = r
        stids = get_stations(opener, pd)
        if not stids:
            print(f"  [SKIP] {full_name}: no stations found")
            rc = 1
            continue
        print(f"  {full_name} -> JMA {jma_name} (pd {pd}), {len(stids)} stations")
        resolved.append((full_name, jma_name, pd, stids))
    if not resolved:
        return 1

    # Combined station list (all prefectures), de-duplicated, order preserved.
    all_stids = []
    for *_, stids in resolved:
        for s in stids:
            if s not in all_stids:
                all_stids.append(s)

    # Try a single combined download (all places at once); fall back if rejected.
    combined_body0 = None
    if not args.per_prefecture:
        open_session(opener)
        y0, m0 = months[0]
        combined_body0 = fetch_month(opener, all_stids, y0, m0, last_day_of(y0, m0))

    if combined_body0 is not None:
        print(f"Combined mode: {len(all_stids)} stations in one request "
              f"-> {len(months)} downloads")
        prog = Progress(len(months))
        for i, (year, month) in enumerate(months):
            stamp = f"{year}-{month:02d}-01"
            prog.show(f"downloading {stamp} ...")
            if i:
                time.sleep(WAIT_BETWEEN_DOWNLOAD_ONCE)
            body = combined_body0 if i == 0 else fetch_month(opener, all_stids, year, month, last_day_of(year, month))
            if body is None:
                prog.note(f"    [FAIL] weather-history-all-{stamp}.csv: portal returned no CSV")
                rc = 1
                continue
            save_outputs(body, "all", year, month, out_dir, long_dir)
            prog.advance(stamp)
        prog.close()
        return rc

    # Fallback: one download per prefecture per month.
    if not args.per_prefecture:
        print("Combined download was rejected — falling back to per-prefecture mode.")
    else:
        print("Per-prefecture mode (forced).")
    print(f"-> {len(months) * len(resolved)} downloads")
    prog = Progress(len(months) * len(resolved))
    for full_name, jma_name, pd, stids in resolved:
        open_session(opener)                       # refresh session per prefecture
        prog.note(f"  {full_name} ({jma_name}): {len(stids)} stations")
        for i, (year, month) in enumerate(months):
            stamp = f"{year}-{month:02d}-01"
            prog.show(f"downloading {jma_name} {stamp} ...")
            if i:
                time.sleep(WAIT_BETWEEN_DOWNLOAD_ONCE)
            body = fetch_month(opener, stids, year, month, last_day_of(year, month))
            if body is None:
                prog.note(f"    [FAIL] weather-history-{jma_name}-{stamp}.csv: portal returned no CSV")
                rc = 1
                continue
            save_outputs(body, jma_name, year, month, out_dir, long_dir)
            prog.advance(f"{jma_name} {stamp}")
    prog.close()

    return rc


if __name__ == "__main__":
    sys.exit(main())
