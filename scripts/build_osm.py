#!/usr/bin/env python3
"""fetch_osm.py が取得した区ごとのOverpass JSONを、レイヤーごとのGeoJSONにまとめる。

出力 (data/geojson/):
    osm_cycleway.geojson            自転車道・自転車レーン (highway=cycleway / cycleway*=lane,track 系)
    osm_cycleway_shared.geojson     共用・補助的な設備のみ (cycleway*=shared_lane,shoulder など)
    osm_bicycle_designated.geojson  bicycle=designated (highway=cycleway は osm_cycleway 側にのみ入れる)
    osm_bicycle_no.geojson          bicycle=no (自転車通行不可)
    osm_stop.geojson                highway=stop (点)
    data/wards_23.geojson           23区の境界 (品質チェックと範囲確認用)
"""
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from geo_util import points_in_rings  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "osm"
OUT = ROOT / "data" / "geojson"

WARD_LIST = [
    "千代田", "中央", "港", "新宿", "文京", "台東", "墨田", "江東", "品川", "目黒", "大田",
    "世田谷", "渋谷", "中野", "杉並", "豊島", "北", "荒川", "板橋", "練馬", "足立", "葛飾", "江戸川",
]
CYCLEWAY_KEYS = ("cycleway", "cycleway:left", "cycleway:right", "cycleway:both")
# cycleway=* のうち、自転車専用の走行空間(レーン・自転車道)を示す値
CYCLEWAY_POSITIVE = {"lane", "track", "opposite_lane", "opposite_track"}
# 設備なしを示す値。これ以外(shared_lane, shoulder, shared, crossing など)は
# 「共用・補助的な設備」として osm_cycleway_shared に入れる
CYCLEWAY_NEGATIVE = {"no", "none", "separate", "use_sidepath"}

WAY_PROPS = ("highway", "name", "oneway", "surface", "bicycle", "foot", "segregated",
             "cycleway", "cycleway:left", "cycleway:right", "cycleway:both",
             "oneway:bicycle", "bicycle:forward", "bicycle:backward", "maxspeed")
STOP_PROPS = ("direction", "stop", "traffic_sign", "name", "highway")


def load(name: str) -> dict:
    path = RAW / f"{name}.json"
    if not path.exists():
        raise SystemExit(f"missing {path}: 先に scripts/fetch_osm.py を実行してください")
    return json.loads(path.read_text(encoding="utf-8"))


def stitch(segments: list) -> list:
    """境界リレーションのouterメンバー(線分の集まり)を閉じたリングにつなぎ直す。"""
    segs = [s[:] for s in segments if len(s) >= 2]
    rings = []
    while segs:
        ring = segs.pop(0)
        changed = True
        while changed and ring[0] != ring[-1]:
            changed = False
            for i, s in enumerate(segs):
                if s[0] == ring[-1]:
                    ring.extend(s[1:])
                elif s[-1] == ring[-1]:
                    ring.extend(reversed(s[:-1]))
                elif s[-1] == ring[0]:
                    ring[:0] = s[:-1]
                elif s[0] == ring[0]:
                    ring[:0] = list(reversed(s[1:]))
                else:
                    continue
                segs.pop(i)
                changed = True
                break
        if ring[0] == ring[-1] and len(ring) >= 4:
            rings.append(ring)
    return rings


def build_wards() -> dict:
    rels = {e["tags"]["name"]: e for e in load("wards")["elements"] if e["type"] == "relation"}
    feats = []
    for ward in WARD_LIST:
        rel = rels.get(f"{ward}区")
        if rel is None:
            raise SystemExit(f"{ward}区の境界リレーションが取得できていません")
        outers = []
        for m in rel["members"]:
            if m["type"] == "way" and m.get("role") == "outer" and "geometry" in m:
                outers.append([[round(p["lon"], 6), round(p["lat"], 6)] for p in m["geometry"]])
        rings = stitch(outers)
        feats.append({
            "type": "Feature",
            "properties": {"name": f"{ward}区", "osm_relation": rel["id"]},
            "geometry": {"type": "MultiPolygon", "coordinates": [[r] for r in rings]},
        })
    fc = {"type": "FeatureCollection", "features": feats}
    (ROOT / "data" / "wards_23.geojson").write_text(
        json.dumps(fc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    return fc


def pick(tags: dict, keys: tuple) -> dict:
    return {k: tags[k] for k in keys if k in tags}


def has_cycleway_infra(tags: dict) -> bool:
    """自転車道・自転車レーン: highway=cycleway、または cycleway* が lane/track 系。"""
    if tags.get("highway") == "cycleway":
        return True
    return any(tags.get(k) in CYCLEWAY_POSITIVE for k in CYCLEWAY_KEYS)


def has_cycleway_shared(tags: dict) -> bool:
    """共用・補助的な設備(矢羽根 shared_lane、路肩 shoulder など)のみの道路。"""
    if has_cycleway_infra(tags):
        return False
    return any(k in tags and tags[k] not in CYCLEWAY_NEGATIVE for k in CYCLEWAY_KEYS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    wards = build_wards()
    print(f"wards: {len(wards['features'])}")

    rings = [r for f in wards["features"] for poly in f["geometry"]["coordinates"] for r in poly]

    def in_23(points: list) -> list:
        arr = np.asarray(points, dtype=float).reshape(-1, 2)
        return points_in_rings(arr[:, 0], arr[:, 1], rings).tolist()

    layers = {n: {} for n in ("osm_cycleway", "osm_cycleway_shared", "osm_bicycle_designated", "osm_bicycle_no")}
    osm_base = set()
    candidates = {}
    for i in range(4):
        d = load(f"ways_{i}")
        osm_base.add(d.get("osm3s", {}).get("timestamp_osm_base"))
        for e in d["elements"]:
            if e["type"] == "way" and len(e.get("geometry", [])) >= 2:
                candidates[e["id"]] = e  # bbox分割の境界で重複しうるのでidで一意化
    ids = list(candidates)
    # 代表点(中央の頂点)が23区内にある道路だけ残す
    mids = [[candidates[i]["geometry"][len(candidates[i]["geometry"]) // 2][k] for k in ("lon", "lat")]
            for i in ids]
    keep = in_23(mids)
    dropped_ways = 0
    for wid, ok in zip(ids, keep):
        if not ok:
            dropped_ways += 1
            continue
        e = candidates[wid]
        tags = e.get("tags", {})
        coords = [[round(p["lon"], 6), round(p["lat"], 6)] for p in e["geometry"]]
        feat = {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {"osm_id": f"way/{wid}", **pick(tags, WAY_PROPS)},
        }
        is_cycleway = has_cycleway_infra(tags)
        if is_cycleway:
            layers["osm_cycleway"][wid] = feat
        elif has_cycleway_shared(tags):
            layers["osm_cycleway_shared"][wid] = feat
        if tags.get("bicycle") == "designated" and not is_cycleway:
            layers["osm_bicycle_designated"][wid] = feat
        if tags.get("bicycle") == "no":
            layers["osm_bicycle_no"][wid] = feat

    sdata = load("stops")
    osm_base.add(sdata.get("osm3s", {}).get("timestamp_osm_base"))
    nodes = [e for e in sdata["elements"] if e["type"] == "node"]
    keep = in_23([[e["lon"], e["lat"]] for e in nodes])
    stops = {}
    for e, ok in zip(nodes, keep):
        if ok:
            stops[e["id"]] = {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [round(e["lon"], 6), round(e["lat"], 6)]},
                "properties": {"osm_id": f"node/{e['id']}", **pick(e.get("tags", {}), STOP_PROPS)},
            }
    print(f"bbox取得のうち23区外で除外: stops {len(nodes) - len(stops)}, ways {dropped_ways}")
    layers["osm_stop"] = stops

    summary = Counter()
    for name, feats in layers.items():
        fc = {"type": "FeatureCollection", "features": list(feats.values())}
        path = OUT / f"{name}.geojson"
        path.write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        summary[name] = len(feats)
        print(f"{name}: {len(feats)} features, {path.stat().st_size/1e6:.1f} MB")
    (ROOT / "data" / "osm_build_report.json").write_text(
        json.dumps({"osm_base_timestamps": sorted(t for t in osm_base if t),
                    "counts": summary, "dropped_outside_23wards": {"stops": len(nodes) - len(stops), "ways": dropped_ways}}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
