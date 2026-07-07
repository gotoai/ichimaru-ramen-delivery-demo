"""One-time prefecture-outline GeoJSON generation.

The pipeline's ``geoshape_<NN>`` shapefiles are fine-grained 小地域 (ooaza) polygons —
thousands per prefecture, far too many to draw as a web overlay. This module dissolves
each prefecture's polygons into a single outline and caches the result as GeoJSON under
``web-app/data/geojson/``. The store coordinates come from these same shapefiles, so the
geometry is already in lon/lat (JGD geographic) — no reprojection needed.

Run once via ``make geojson`` (or it is generated lazily on first request and cached).
Degrades gracefully: if shapely/pyshp are unavailable or a shapefile is missing, the
prefecture layer is simply omitted (base tiles still show the geography).
"""
from __future__ import annotations

import json
from pathlib import Path

from . import config

# Prefecture code -> Japanese name (this demo's four prefectures).
_PREF_NAME = {"11": "埼玉県", "12": "千葉県", "13": "東京都", "14": "神奈川県"}


def _shapefile_for(code: str) -> Path:
    return config.GEOSHAPE_DIR / f"geoshape_{code}" / f"r2ka{code}.shp"


def _dissolve_to_geojson(code: str) -> dict | None:
    """Read one prefecture's shapefile and dissolve all polygons into one geometry."""
    try:
        import shapefile  # pyshp
        from shapely.geometry import shape
        from shapely.ops import unary_union
    except Exception:
        return None

    shp = _shapefile_for(code)
    if not shp.exists():
        return None

    reader = shapefile.Reader(str(shp))
    geoms = []
    for sr in reader.iterShapes():
        gj = sr.__geo_interface__
        try:
            g = shape(gj)
            if not g.is_valid:
                g = g.buffer(0)
            geoms.append(g)
        except Exception:
            continue
    if not geoms:
        return None

    merged = unary_union(geoms)
    # Simplify to keep the overlay payload small; tolerance in degrees (~300 m), which is
    # plenty for a prefecture outline drawn at regional zoom.
    merged = merged.simplify(0.003, preserve_topology=True)
    return {
        "type": "Feature",
        "properties": {"code": code, "name": _PREF_NAME.get(code, code)},
        "geometry": merged.__geo_interface__,
    }


def build_prefecture_geojson(force: bool = False) -> Path:
    """Build (and cache) a FeatureCollection of the demo's prefecture outlines.

    Returns the path to the cached file. If nothing could be built, writes an empty
    FeatureCollection so the endpoint always returns valid GeoJSON.
    """
    config.GEOJSON_DIR.mkdir(parents=True, exist_ok=True)
    out = config.GEOJSON_DIR / "prefectures.geojson"
    if out.exists() and not force:
        return out

    features = []
    for code in config.PREFECTURE_CODES:
        feat = _dissolve_to_geojson(code)
        if feat is not None:
            features.append(feat)

    fc = {"type": "FeatureCollection", "features": features}
    out.write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")
    return out


def prefecture_geojson() -> dict:
    """Return the cached FeatureCollection, building it on first use."""
    path = build_prefecture_geojson(force=False)
    return json.loads(path.read_text(encoding="utf-8"))


if __name__ == "__main__":  # `python -m app.geo` to (re)build the cache
    p = build_prefecture_geojson(force=True)
    fc = json.loads(p.read_text(encoding="utf-8"))
    print(f"wrote {p} with {len(fc['features'])} prefecture feature(s)")
