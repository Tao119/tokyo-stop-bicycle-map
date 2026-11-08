#!/usr/bin/env python3
"""Overpass APIから東京23区内の自転車関連・一時停止データを取得する。

wards.json(区境界) / stops.json(highway=stop) / ways_N.json(自転車関連の道路)
を data/raw/osm/ に保存する。取得済みのファイルはスキップ(--force で再取得)。
GeoJSON化は scripts/build_osm.py。
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "raw" / "osm"

WORKERS = 4

ENDPOINTS = [
    "https://z.overpass-api.de/api/interpreter",  # 最新データ。混雑時は応答が遅い
    "https://overpass-api.de/api/interpreter",
    "https://lz4.overpass-api.de/api/interpreter",
    # overpass.openstreetmap.fr は更新が遅れる(2026-08時点のデータだった)ため、
    # 区境界以外では使わない
]
STALE_OK_ENDPOINTS = ["https://overpass.openstreetmap.fr/api/interpreter"]

WARD_LIST = [
    "千代田", "中央", "港", "新宿", "文京", "台東", "墨田", "江東", "品川", "目黒", "大田",
    "世田谷", "渋谷", "中野", "杉並", "豊島", "北", "荒川", "板橋", "練馬", "足立", "葛飾", "江戸川",
]


WARDS_RE = "^(" + "|".join(WARD_LIST) + ")区$"
# 23区をおおまかに覆う範囲。区境界での絞り込みは build_osm.py で行う
BBOX = (35.50, 139.55, 35.84, 139.93)  # south, west, north, east
N_SPLIT = 4  # 道路クエリは経度方向に分割して1回あたりの負荷を下げる


def bbox_str(s, w, n, e) -> str:
    return f"{s},{w},{n},{e}"


def wards_query() -> str:
    return (
        "[out:json][timeout:300];\n"
        'area["ISO3166-2"="JP-13"]["admin_level"="4"]->.t;\n'
        f'rel["boundary"="administrative"]["admin_level"="7"]["name"~"{WARDS_RE}"](area.t);\n'
        "out geom;"
    )


def stops_query() -> str:
    return f'[out:json][timeout:300];\nnode["highway"="stop"]({bbox_str(*BBOX)});\nout;'


def ways_query(i: int) -> str:
    s, w, n, e = BBOX
    step = (e - w) / N_SPLIT
    b = bbox_str(s, round(w + step * i, 4), n, round(w + step * (i + 1), 4))
    return f"""[out:json][timeout:300];
(
  way["highway"="cycleway"]({b});
  way["cycleway"]({b});
  way["cycleway:left"]({b});
  way["cycleway:right"]({b});
  way["cycleway:both"]({b});
  way["bicycle"="no"]({b});
  way["bicycle"="designated"]({b});
);
out tags geom;"""


def post(query: str, allow_stale: bool = False) -> dict:
    data = urllib.parse.urlencode({"data": query}).encode()
    last = None
    for attempt in range(6):
        for ep in ENDPOINTS + (STALE_OK_ENDPOINTS if allow_stale else []):
            try:
                req = urllib.request.Request(
                    ep, data=data, headers={"User-Agent": "tokyo-bike-map/0.1", "Accept": "*/*"}
                )
                with urllib.request.urlopen(req, timeout=300) as r:
                    return json.loads(r.read())
            except Exception as e:  # noqa: BLE001
                last = e
                print(f"  {ep} failed: {e}", flush=True)
        wait = min(30 * 2**attempt, 300)
        print(f"  retry in {wait}s", flush=True)
        time.sleep(wait)
    raise RuntimeError(f"Overpass failed: {last}")


def fetch_one(name: str, query: str, allow_stale: bool) -> None:
    path = OUT / f"{name}.json"
    print(f"[{name}] ...", flush=True)
    res = post(query, allow_stale=allow_stale)
    path.write_text(json.dumps(res, ensure_ascii=False), encoding="utf-8")
    print(f"[{name}] {len(res.get('elements', []))} elements "
          f"(osm_base={res.get('osm3s', {}).get('timestamp_osm_base')})", flush=True)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    force = "--force" in sys.argv
    tasks = [("wards", wards_query(), True), ("stops", stops_query(), False)]
    tasks += [(f"ways_{i}", ways_query(i), False) for i in range(N_SPLIT)]
    tasks = [t for t in tasks if force or not (OUT / f"{t[0]}.json").exists()]
    # 公開サーバーはキュー待ちが長いので少数だけ並列にする(負荷をかけすぎない)
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for f in [ex.submit(fetch_one, *t) for t in tasks]:
            f.result()
    print("done", flush=True)


if __name__ == "__main__":
    main()
