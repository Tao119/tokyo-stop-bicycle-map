#!/usr/bin/env python3
"""推定した進入方向を、OSMの一時停止(direction=forward/backward)から求めた向きと突き合わせる。

OSMの一時停止ノードが乗っている道路の進行方向と direction タグから、実際の「止まる車の進行方位」を作り、
15m以内にあるJARTICの一時停止の推定方位と比べる。OSMにない点は検証できない(約23%のみ)。
OSM側も現地確認はしていないため、あくまで参考の一致率。
"""
import gzip
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from infer_direction import GEO, ROADS, ROOT, bearing, to_xy  # noqa: E402


def angdiff(a: float, b: float) -> float:
    d = abs(a - b) % 360
    return min(d, 360 - d)


def osm_headings() -> dict:
    """{(lon,lat): 方位} 。direction が forward/backward で、1本の道路にだけ属するノードのみ。"""
    stops = {}
    for f in json.loads((GEO / "osm_stop.geojson").read_text(encoding="utf-8"))["features"]:
        d = f["properties"].get("direction")
        if d in ("forward", "backward"):
            lon, lat = f["geometry"]["coordinates"]
            stops[(round(lon, 6), round(lat, 6))] = d
    owners = defaultdict(set)
    heading = {}
    for path in sorted(ROADS.glob("roads_*_*.json*")):
        opener = gzip.open if path.suffix == ".gz" else open
        with opener(path, "rt", encoding="utf-8") as fh:
            elements = json.load(fh).get("elements", [])
        for w in elements:
            geom = w.get("geometry")
            if not geom or any(g is None for g in geom):
                continue
            pts = [(round(g["lon"], 6), round(g["lat"], 6)) for g in geom]
            for i, p in enumerate(pts):
                if p in stops:
                    owners[p].add(w["id"])
                    a = pts[i - 1] if i > 0 else p
                    b = pts[i + 1] if i + 1 < len(pts) and i == 0 else p
                    if i > 0:
                        x0, y0 = to_xy(*pts[i - 1]); x1, y1 = to_xy(*p)
                    else:
                        x0, y0 = to_xy(*p); x1, y1 = to_xy(*pts[i + 1])
                    brg = bearing(x1 - x0, y1 - y0)
                    heading[p] = (brg if stops[p] == "forward" else (brg + 180) % 360)
    return {p: h for p, h in heading.items() if len(owners[p]) == 1}


def main() -> None:
    truth = osm_headings()
    print(f"検証に使えるOSM一時停止(向きあり・1道路のみ): {len(truth):,}")
    keys = list(truth)
    cell = defaultdict(list)
    for p in keys:
        x, y = to_xy(*p)
        cell[(int(x // 30), int(y // 30))].append((x, y, truth[p]))
    rows = []
    for f in json.loads((GEO / "jartic_stop.geojson").read_text(encoding="utf-8"))["features"]:
        p = f["properties"]
        x, y = to_xy(*f["geometry"]["coordinates"])
        best, bd = None, 15.0
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for ox, oy, h in cell.get((int(x // 30) + dx, int(y // 30) + dy), ()):
                    d = math.hypot(ox - x, oy - y)
                    if d < bd:
                        best, bd = h, d
        if best is None:
            continue
        rows.append((p.get("dir_status"), p.get("dir_bearing"), best, p.get("dir_basis", "")))
    print(f"JARTICと突き合わせできた点: {len(rows):,}")
    report = {"matched": len(rows), "by_status": {}}
    for status in ("高", "中", "低", "推定不可"):
        sub = [r for r in rows if r[0] == status]
        if not sub:
            continue
        if status == "推定不可":
            report["by_status"][status] = {"n": len(sub)}
            print(f"{status}: {len(sub):,}件(向きを出していない)")
            continue
        ok45 = sum(angdiff(r[1], r[2]) <= 45 for r in sub)
        ok90 = sum(angdiff(r[1], r[2]) <= 90 for r in sub)
        rev = sum(angdiff(r[1], r[2]) >= 135 for r in sub)
        report["by_status"][status] = {"n": len(sub), "within45": ok45 / len(sub), "within90": ok90 / len(sub), "opposite": rev / len(sub)}
        print(f"{status}: n={len(sub):,}  45度以内 {ok45/len(sub):.1%}  90度以内 {ok90/len(sub):.1%}  逆向き(135度以上) {rev/len(sub):.1%}")
    est = [r for r in rows if r[0] != "推定不可"]
    print(f"全体(推定ありのみ) n={len(est):,}  45度以内 {sum(angdiff(r[1], r[2]) <= 45 for r in est)/len(est):.1%}")
    (ROOT / "data" / "direction_validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
