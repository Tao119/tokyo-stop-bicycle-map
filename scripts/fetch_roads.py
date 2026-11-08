#!/usr/bin/env python3
"""一時停止の進入方向を推定するため、東京都の車道(OSM highway)を取得する。

一時停止の点がある0.05度区画ごとに取得し、data/raw/osm_roads/roads_{i}_{j}.json.gz に保存する。
取得済みのファイルはスキップ(--force で再取得)。
"""
import gzip
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_osm import post  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "raw" / "osm_roads"
TILE = 0.05      # 度。一時停止の点が1つでもある区画だけ取得する
MARGIN = 0.003   # 区画の端の点が、隣の区画にまたがる道路を見られるよう広げる
WORKERS = 2

HIGHWAYS = ("motorway|motorway_link|trunk|trunk_link|primary|primary_link|secondary|secondary_link|"
            "tertiary|tertiary_link|unclassified|residential|living_street|service|road")


def stop_tiles() -> list:
    fc = json.loads((ROOT / "data" / "geojson" / "jartic_stop.geojson").read_text(encoding="utf-8"))
    tiles = {(int(f["geometry"]["coordinates"][0] // TILE), int(f["geometry"]["coordinates"][1] // TILE))
             for f in fc["features"] if f["geometry"]["type"] == "Point"}
    return sorted(tiles)


def query(i: int, j: int) -> str:
    w, s = i * TILE - MARGIN, j * TILE - MARGIN
    e, n = (i + 1) * TILE + MARGIN, (j + 1) * TILE + MARGIN
    b = f"{s:.4f},{w:.4f},{n:.4f},{e:.4f}"
    return (f'[out:json][timeout:600];\nway["highway"~"^({HIGHWAYS})$"]'
            f'["service"!~"parking_aisle|driveway|drive-through"]["access"!~"^(private|no)$"]({b});\n'
            "out tags geom;")


def fetch(i: int, j: int) -> None:
    path = OUT / f"roads_{i}_{j}.json.gz"
    print(f"[roads {i},{j}] ...", flush=True)
    res = post(query(i, j))
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)
    print(f"[roads {i},{j}] {len(res.get('elements', []))} ways "
          f"(osm_base={res.get('osm3s', {}).get('timestamp_osm_base')})", flush=True)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    force = "--force" in sys.argv
    tasks = [(i, j) for i, j in stop_tiles() if force or not any((OUT / f"roads_{i}_{j}.json{ext}").exists() for ext in ("", ".gz"))]
    print(f"{len(tasks)} tiles to fetch", flush=True)
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for f in [ex.submit(fetch, *t) for t in tasks]:
            f.result()
    print("done", flush=True)


if __name__ == "__main__":
    main()
