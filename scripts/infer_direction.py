#!/usr/bin/env python3
"""JARTICの一時停止(63)に、OSM道路網から進入方向(進行方向)を推定して付与する。

JARTIC東京都の一時停止は進入方向がほぼ空のため、次の手順で推定する。

1. 点に最も近い道路(way)を探し、その道路に沿って両方向の最寄りの交差点まで辿る。
2. 各方向について「その交差点で、いま走っている道路が従道路か」を判定する。
   - 行き止まり側(T字路の突き当たり)       -> 高
   - 交差点で他の道路より格が低い            -> 高
   - 格が同じ(同格)                          -> 低(どちらが止まるか不明)
   - 他の道路より格が高い(大通りを走行中)   -> その向きは候補から外す
3. 候補が1つならそれを採用。2つなら近い交差点に向かう向きを採用する(左右の位置は当てにならず使わない)。
4. 最寄りが主道路側で候補がなければ、大通り走行中に誤判定しないよう向きを出さず「推定不可」にする。

方位(dir_bearing)は北=0度の時計回りで、止まる車の進行方向。画面側でこの方位から矢印を回転して描く。
"""
import gzip
import json
import math
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
ROADS = ROOT / "data" / "raw" / "osm_roads"
GEO = ROOT / "data" / "geojson"

LAT0 = 35.68
KX = 111320.0 * math.cos(math.radians(LAT0))
KY = 110574.0

# 道路の格(小さいほど主要)
RANK = {"motorway": 0, "trunk": 1, "primary": 2, "secondary": 3, "tertiary": 4,
        "unclassified": 5, "residential": 6, "road": 6, "living_street": 7, "service": 8}
MAX_SNAP = 14.0       # 点から道路までの許容距離(m)
MAX_WALK = 70.0       # 交差点まで辿る最大距離(m)
COMPASS = ["北", "北東", "東", "南東", "南", "南西", "西", "北西"]


def load_roads(tile_key) -> list:
    """区画の道路JSONを読む(.json または .json.gz)。なければ None。"""
    base = ROADS / f"roads_{tile_key[0]}_{tile_key[1]}.json"
    for path, opener in ((base, open), (base.with_name(base.name + ".gz"), gzip.open)):
        if path.exists():
            with opener(path, "rt", encoding="utf-8") as f:
                return json.load(f).get("elements", [])
    return None


def rank_of(tags: dict) -> float:
    hw = tags.get("highway", "")
    base = hw.replace("_link", "")
    r = RANK.get(base, 6)
    return r + (0.5 if hw.endswith("_link") else 0.0)


def to_xy(lon: float, lat: float):
    return lon * KX, lat * KY


def to_lonlat(x: float, y: float):
    return round(x / KX, 6), round(y / KY, 6)


def bearing(dx: float, dy: float) -> float:
    return (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0


class Tile:
    """1区画分の道路グラフ。"""

    def __init__(self, ways: list):
        self.xy = {}                        # node id -> (x, y)
        self.adj = defaultdict(list)        # node id -> [(neighbor id, way idx)]
        self.ways = []                      # way idx -> dict(rank, oneway, name, hw, nodes)
        segs = []
        for w in ways:
            geom = w.get("geometry")
            if not geom or any(g is None for g in geom) or len(geom) < 2:
                continue
            # `out tags geom` にはノードIDが含まれないため、座標(7桁)をノードのキーにする。
            # 同じOSMノードは同一座標になるので、道路どうしの接続は座標一致で判定できる。
            nodes = [(round(g["lon"], 7), round(g["lat"], 7)) for g in geom]
            tags = w.get("tags", {})
            oneway = tags.get("oneway")
            if tags.get("junction") == "roundabout" and oneway is None:
                oneway = "yes"
            oneway = {"yes": 1, "true": 1, "1": 1, "-1": -1}.get(oneway, 0)
            wi = len(self.ways)
            self.ways.append({"rank": rank_of(tags), "oneway": oneway, "name": tags.get("name"),
                              "hw": tags.get("highway"), "nodes": nodes})
            for n in nodes:
                if n not in self.xy:
                    self.xy[n] = to_xy(n[0], n[1])
            for a, b in zip(nodes, nodes[1:]):
                if a == b or a not in self.xy or b not in self.xy:
                    continue
                self.adj[a].append((b, wi))
                self.adj[b].append((a, wi))
                segs.append((a, b, wi))
        self.segs = segs
        self.sa = np.array([self.xy[a] for a, _, _ in segs]).reshape(-1, 2)
        self.sb = np.array([self.xy[b] for _, b, _ in segs]).reshape(-1, 2)
        self.grid = defaultdict(list)
        cell = 30.0
        for i in range(len(segs)):
            (ax, ay), (bx, by) = self.sa[i], self.sb[i]
            for cx in range(int(min(ax, bx) // cell), int(max(ax, bx) // cell) + 1):
                for cy in range(int(min(ay, by) // cell), int(max(ay, by) // cell) + 1):
                    self.grid[(cx, cy)].append(i)

    def candidates(self, px: float, py: float):
        """点から MAX_SNAP 以内の線分を (距離, seg idx, 足の位置t) で、距離の近い順に返す。"""
        cx, cy = int(px // 30), int(py // 30)
        idx = set()
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                idx.update(self.grid.get((cx + dx, cy + dy), ()))
        if not idx:
            return []
        ids = np.fromiter(idx, dtype=int)
        a, b = self.sa[ids], self.sb[ids]
        d = b - a
        L = (d ** 2).sum(1) + 1e-9
        t = np.clip(((px - a[:, 0]) * d[:, 0] + (py - a[:, 1]) * d[:, 1]) / L, 0, 1)
        fx, fy = a[:, 0] + t * d[:, 0], a[:, 1] + t * d[:, 1]
        dist = np.hypot(fx - px, fy - py)
        order = np.argsort(dist)
        return [(float(dist[o]), int(ids[o]), float(t[o])) for o in order if dist[o] <= MAX_SNAP]

    def walk(self, start: int, prev: int, wi: int, dist0: float):
        """prev -> start の向きに進み、最初の交差点(次数3以上)まで辿る。

        戻り値: (交差点node, 距離, 到達時に通っていた道路wi, 直前node) / 見つからなければ None
        """
        dist, cur, before, cw = dist0, start, prev, wi
        for _ in range(60):
            if len(self.adj[cur]) >= 3:
                return cur, dist, cw, before
            nxt = [(n, w) for n, w in self.adj[cur] if n != before]
            if not nxt:
                return None  # 行き止まり
            # 次数2の継ぎ目では同じ道路を優先して続行
            nxt.sort(key=lambda e: 0 if e[1] == cw else 1)
            n, w = nxt[0]
            x0, y0 = self.xy[cur]
            x1, y1 = self.xy[n]
            dist += math.hypot(x1 - x0, y1 - y0)
            if dist > MAX_WALK:
                return None
            before, cur, cw = cur, n, w
        return None

    def judge(self, junction: int, arriving_from: int, wi: int):
        """交差点に向かって道路wiで進入するとき、その道路が従道路か。

        戻り値: ('高' | '低' | None, 理由, 交差する道路wi or None)
        """
        w = self.ways[wi]
        others = [(n, ow) for n, ow in self.adj[junction] if ow != wi]
        if not others:
            return None, "他の道路なし", None
        best = min(others, key=lambda e: self.ways[e[1]]["rank"])
        r_other = self.ways[best[1]]["rank"]
        same_legs = [n for n, ow in self.adj[junction] if ow == wi]
        terminates = len(same_legs) <= 1  # この道路が交差点で終わる(T字の突き当たり)
        if terminates:
            if r_other <= w["rank"] + 0.5:
                return "高", "T字路の突き当たり側", best[1]
            return "低", "突き当たりだが相手が従道路", best[1]
        if r_other < w["rank"]:
            return "高", "より主要な道路と交差", best[1]
        if r_other == w["rank"]:
            return "低", "同格の道路どうしの交差", best[1]
        return None, "主道路側(大通り走行中)", best[1]


def evaluate_way(tile: Tile, px: float, py: float, seg: int, t: float, dist: float):
    """1本の道路について、両方向の候補を評価して最良のものを返す。"""
    a, b, wi = tile.segs[seg]
    (ax, ay), (bx, by) = tile.xy[a], tile.xy[b]
    seglen = math.hypot(bx - ax, by - ay)
    fx, fy = ax + t * (bx - ax), ay + t * (by - ay)
    off = (px - fx, py - fy)
    w = tile.ways[wi]
    out = []
    # (向き, 次のnode, 手前node, 手前node側の位置までの距離)
    for name, head, tail, d0 in (("fwd", b, a, (1 - t) * seglen), ("bwd", a, b, t * seglen)):
        hx, hy = (bx - ax, by - ay) if name == "fwd" else (ax - bx, ay - by)
        norm = math.hypot(hx, hy) or 1.0
        hx, hy = hx / norm, hy / norm
        # 一方通行は進行方向が固定
        if w["oneway"] == 1 and name == "bwd" or w["oneway"] == -1 and name == "fwd":
            continue
        res = tile.walk(head, tail, wi, d0)
        if res is None:
            continue
        junc, jd, cw, before = res
        if jd > MAX_WALK:
            continue
        # 交差点に入る直前の道路で従道路か判定
        conf, reason, cross = tile.judge(junc, before, cw)
        if conf is None:
            continue
        left = (-hy) * off[0] + hx * off[1]  # 左法線(-hy, hx)との内積。正なら点は進行方向の左側
        out.append({"conf": conf, "reason": reason, "cross": cross, "dist": jd, "junction": junc,
                    "h": (hx, hy), "left": left, "way": wi})
    return out, off


def pick(cands: list):
    """候補から1つ選ぶ。戻り値 (候補, 信頼度, 根拠リスト)。

    左側通行を使った左右判定は、実測(OSMとの突き合わせ)で近い交差点側を選ぶ方式より
    大きく劣ったため使わない(停止点が車線の左右に正確に置かれていない)。
    """
    if not cands:
        return None
    if len(cands) == 1:
        c = cands[0]
        return c, c["conf"], [c["reason"]]
    # 2方向とも従道路側になる(短いブロックなど): 近い交差点に向かう向きを採用
    near, far = sorted(cands, key=lambda c: c["dist"])
    ratio = far["dist"] / max(near["dist"], 0.1)
    basis = [near["reason"], f"両方向とも従道路側のため、近い交差点側を採用(距離比{ratio:.1f})"]
    if ratio >= 2.0 and near["conf"] == "高":
        return near, "中", basis
    return near, "低", basis


def infer_point(tile: Tile, px: float, py: float):
    cands = tile.candidates(px, py)
    if not cands:
        return {"status": "推定不可", "why": "近くに道路がない"}
    # 最寄りの道路だけで判定する。最寄りが主道路側(大通り走行中)なら、近くの従道路への
    # 付け替えは実測で一致率が低かったため行わず、向きを出さない。
    dist, seg, t = cands[0]
    evals, _ = evaluate_way(tile, px, py, seg, t, dist)
    picked = pick(evals)
    if picked is None:
        return {"status": "推定不可",
                "why": "最寄りの道路が主道路側、または交差点が近くにない(大通り走行中の誤判定を避けるため向きを出さない)"}
    c, conf, basis = picked
    chosen = (c, conf, basis, dist)
    c, conf, basis, dist = chosen
    hx, hy = c["h"]
    brg = bearing(hx, hy)
    wname = tile.ways[c["way"]].get("name")
    cross = tile.ways[c["cross"]] if c["cross"] is not None else {}
    return {"status": conf, "bearing": round(brg), "heading": hx, "headingy": hy,
            "basis": basis, "junction_dist_m": round(c["dist"], 1), "road_class": tile.ways[c["way"]]["hw"],
            "road_name": wname, "cross_class": cross.get("hw"), "cross_name": cross.get("name"),
            "snap_m": round(dist, 1)}


def main() -> None:
    files = sorted(ROADS.glob("roads_*_*.json*"))
    if not files:
        raise SystemExit("道路データがありません。先に scripts/fetch_roads.py を実行してください")
    fc = json.loads((GEO / "jartic_stop.geojson").read_text(encoding="utf-8"))
    by_tile = defaultdict(list)
    for k, f in enumerate(fc["features"]):
        lon, lat = f["geometry"]["coordinates"]
        by_tile[(int(lon // 0.05), int(lat // 0.05))].append(k)

    results = {}
    stats = Counter()
    for tile_key, idxs in sorted(by_tile.items()):
        elements = load_roads(tile_key)
        if elements is None:
            for k in idxs:
                results[k] = {"status": "推定不可", "why": "道路データ未取得"}
            continue
        tile = Tile([w for w in elements if w.get("type") == "way"])
        for k in idxs:
            lon, lat = fc["features"][k]["geometry"]["coordinates"]
            px, py = to_xy(lon, lat)
            results[k] = infer_point(tile, px, py)
        print(f"tile {tile_key}: {len(idxs)} stops, {len(tile.ways)} ways", flush=True)

    for k, f in enumerate(fc["features"]):
        r = results[k]
        p = f["properties"]
        for key in [k2 for k2 in p if k2.startswith("dir_")]:
            del p[key]  # 再実行時に前回の推定結果を引きずらない
        p["dir_status"] = r["status"]
        if r["status"] == "推定不可":
            p["dir_note"] = r["why"]
        else:
            brg = r["bearing"]
            p["dir_bearing"] = brg
            p["dir_text"] = f"{COMPASS[int(((brg + 22.5) % 360) // 45)]}へ進行する車が停止"
            p["dir_basis"] = " / ".join(r["basis"])
            p["dir_junction_m"] = r["junction_dist_m"]
            for src, dst in (("road_class", "dir_road_class"), ("road_name", "dir_road_name"),
                             ("cross_class", "dir_cross_class"), ("cross_name", "dir_cross_name")):
                if r.get(src):
                    p[dst] = r[src]
        stats[r["status"]] += 1

    out = json.dumps(fc, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    target = GEO / "jartic_stop.geojson"
    free = shutil.disk_usage(GEO).free
    need = len(out) + 20_000_000
    if free < need:
        # 書き込み途中で容量切れになると既存ファイルが壊れるため、先に確認して中止する
        raise SystemExit(f"ディスクの空きが足りません(空き {free/1e6:.0f}MB < 必要 {need/1e6:.0f}MB)。"
                         "既存の jartic_stop.geojson には触れていません")
    target.write_bytes(out)
    report = {"total": len(fc["features"]), "by_status": dict(stats)}
    (ROOT / "data" / "direction_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    sys.exit(main())
