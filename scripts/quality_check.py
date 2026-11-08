#!/usr/bin/env python3
"""品質チェック。結果を標準出力と data/quality_report.json に出す。

1. レイヤーごとの件数
2. 座標の範囲(bbox)と、東京都内/23区内に収まっているか
3. JARTICとOSMの一時停止が半径15m以内でどれだけ重なるか(23区内のみで比較)
"""
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geo_util import points_in_rings  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
GEO = ROOT / "data" / "geojson"

# 東京都全域(島しょ部を含む)。parse_jartic.py の TOKYO_BBOX と同じ値
TOKYO_BBOX = (138.9, 20.3, 142.3, 36.0)
RADII = (15, 30, 50)  # 15mが本題。他は感度確認用
MAIN_RADIUS = 15

LAYER_LABELS = {
    "jartic_stop": "JARTIC 一時停止(63)",
    "jartic_bike_sidewalk_ok": "JARTIC 普通自転車歩道通行可(81)",
    "jartic_bike_sidewalk_part": "JARTIC 普通自転車の歩道通行部分(82)",
    "jartic_bike_no_entry": "JARTIC 普通自転車の交差点進入禁止(83)",
    "jartic_bike_crossing": "JARTIC 自転車横断帯(87)",
    "jartic_closure_bike": "JARTIC 通行止め系(自転車対象)",
    "osm_cycleway": "OSM 自転車道・自転車レーン",
    "osm_cycleway_shared": "OSM 共用の自転車設備(矢羽根等)",
    "osm_bicycle_designated": "OSM bicycle=designated",
    "osm_bicycle_no": "OSM 自転車通行不可(bicycle=no)",
    "osm_stop": "OSM 一時停止(highway=stop)",
}


def iter_coords(geom):
    t = geom["type"]
    c = geom["coordinates"]
    if t == "Point":
        yield c
    elif t == "LineString":
        yield from c
    elif t == "Polygon":
        for r in c:
            yield from r
    elif t == "MultiPolygon":
        for p in c:
            for r in p:
                yield from r


def load_fc(name: str):
    return json.loads((GEO / f"{name}.geojson").read_text(encoding="utf-8"))["features"]


def project(lon: np.ndarray, lat: np.ndarray, lat0: float = 35.68):
    """東京付近で十分な精度の等距離円筒近似(メートル)。"""
    kx = 111320.0 * math.cos(math.radians(lat0))
    ky = 110574.0
    return lon * kx, lat * ky


def match_fraction(a_xy, b_xy, radius: float) -> float:
    """aの各点について、半径radius[m]以内にbの点が1つでもある割合。"""
    cell = radius
    grid = defaultdict(list)
    for x, y in zip(*b_xy):
        grid[(int(x // cell), int(y // cell))].append((x, y))
    hit = 0
    r2 = radius * radius
    for x, y in zip(*a_xy):
        cx, cy = int(x // cell), int(y // cell)
        found = False
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for bx, by in grid.get((cx + dx, cy + dy), ()):
                    if (bx - x) ** 2 + (by - y) ** 2 <= r2:
                        found = True
                        break
                if found:
                    break
            if found:
                break
        hit += found
    return hit / len(a_xy[0]) if len(a_xy[0]) else float("nan")


def main() -> None:
    wards = json.loads((ROOT / "data" / "wards_23.geojson").read_text(encoding="utf-8"))["features"]
    rings = [r for f in wards for poly in f["geometry"]["coordinates"] for r in poly]

    report = {"layers": {}, "stop_overlap": {}}
    print("=== 1/2. レイヤー別 件数・座標範囲 ===")
    stops_xy = {}
    for name, label in LAYER_LABELS.items():
        feats = load_fc(name)
        coords = [c for f in feats for c in iter_coords(f["geometry"])]
        entry = {"label": label, "count": len(feats), "vertices": len(coords)}
        if coords:
            a = np.asarray(coords, dtype=float)
            lon, lat = a[:, 0], a[:, 1]
            entry["bbox"] = [float(lon.min()), float(lat.min()), float(lon.max()), float(lat.max())]
            in_tokyo = ((lon >= TOKYO_BBOX[0]) & (lon <= TOKYO_BBOX[2])
                        & (lat >= TOKYO_BBOX[1]) & (lat <= TOKYO_BBOX[3]))
            entry["vertices_outside_tokyo_bbox"] = int((~in_tokyo).sum())
            in23 = points_in_rings(lon, lat, rings)
            entry["vertices_in_23wards"] = int(in23.sum())
            entry["vertices_outside_23wards"] = int((~in23).sum())
            entry["share_in_23wards"] = float(in23.mean())
        report["layers"][name] = entry
        bb = entry.get("bbox")
        print(f"{label:34s} 件数={entry['count']:>7,}"
              + (f"  bbox=({bb[0]:.4f},{bb[1]:.4f})-({bb[2]:.4f},{bb[3]:.4f})"
                 f"  都内bbox外頂点={entry['vertices_outside_tokyo_bbox']}"
                 f"  23区内頂点率={entry['share_in_23wards']:.1%}" if bb else "  (データなし)"))
        if name in ("jartic_stop", "osm_stop"):
            pts = np.asarray([f["geometry"]["coordinates"] for f in feats
                              if f["geometry"]["type"] == "Point"], dtype=float).reshape(-1, 2)
            stops_xy[name] = pts

    print("\n=== 3. 一時停止の重なり(JARTIC vs OSM, 23区内の点のみ) ===")
    j = stops_xy["jartic_stop"]
    o = stops_xy["osm_stop"]
    j = j[points_in_rings(j[:, 0], j[:, 1], rings)]
    o = o[points_in_rings(o[:, 0], o[:, 1], rings)]
    jxy = project(j[:, 0], j[:, 1])
    oxy = project(o[:, 0], o[:, 1])
    report["stop_overlap"]["jartic_stops_in_23wards"] = int(len(j))
    report["stop_overlap"]["osm_stops_in_23wards"] = int(len(o))
    print(f"JARTIC 一時停止(23区内): {len(j):,} / OSM highway=stop(23区内): {len(o):,}")
    for r in RADII:
        jf = match_fraction(jxy, oxy, r)
        of = match_fraction(oxy, jxy, r)
        report["stop_overlap"][f"within_{r}m"] = {
            "jartic_with_osm_nearby": jf, "osm_with_jartic_nearby": of}
        mark = "  <-- 本題" if r == MAIN_RADIUS else ""
        print(f"半径{r:>2}m: JARTICのうちOSMが近くにある {jf:6.1%} / "
              f"OSMのうちJARTICが近くにある {of:6.1%}{mark}")

    (ROOT / "data" / "quality_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n-> data/quality_report.json")


if __name__ == "__main__":
    sys.exit(main())
