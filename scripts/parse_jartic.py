#!/usr/bin/env python3
"""JARTIC 交通規制情報(東京都, typeD 拡張版標準フォーマット k_2.1)を取得・パースし、
レイヤーごとのGeoJSONを data/geojson/ に出力する。

使い方:
    python3 scripts/parse_jartic.py            # 未取得ならダウンロードしてパース
    python3 scripts/parse_jartic.py --refresh  # カタログを取り直して再ダウンロード

ダウンロード・パースの流れは shiwaku/jartic-traffic-regulation-converter
(Apache-2.0) を参考にしている(カタログJSON経由のURL解決、cp932のストリーム読み、
経度緯度列の `lon lat;lon lat` パース)。
"""
import csv
import io
import json
import sys
import time
import urllib.error
import urllib.request
import zipfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw" / "jartic"
OUT = ROOT / "data" / "geojson"

CATALOG_URL = "https://www.jartic.or.jp/d/opendata/opendata.json"
BASE_URL = "https://www.jartic.or.jp/d/opendata"
TOKYO_ID = "R13"
UA = "tokyo-bike-map/0.1"

csv.field_size_limit(2**31 - 1)

# レイヤー定義: 共通規制種別コード -> レイヤー名
CODE_TO_LAYER = {
    "63": "jartic_stop",
    "81": "jartic_bike_sidewalk_ok",
    "82": "jartic_bike_sidewalk_part",
    "83": "jartic_bike_no_entry",
    "87": "jartic_bike_crossing",
    "4": "jartic_closure_bike",
    "5": "jartic_closure_bike",
    "7": "jartic_closure_bike",
    "13": "jartic_closure_bike",
}
# 対象車両による絞り込みを行う(自転車を含むものだけ残す)規制種別
VEHICLE_FILTERED = {"4", "5", "7", "13"}

CODE_NAMES = {
    "4": "通行止め",
    "5": "車両通行止め",
    "7": "車両通行止め(踏切)",
    "13": "車両進入禁止",
    "63": "一時停止",
    "81": "普通自転車歩道通行可",
    "82": "普通自転車の歩道通行部分",
    "83": "普通自転車の交差点進入禁止",
    "87": "自転車横断帯",
}

# 対象コード(別記2 表10)。各カテゴリの値は15桁で、右から n 桁目が 1 以上ならコード n
VEHICLE_NAMES = {
    "A": {1: "車両", 2: "自動車", 3: "乗用", 4: "普乗", 5: "普通", 6: "中乗", 7: "中型",
          8: "特定中乗", 9: "特定中型", 10: "軽", 11: "準中乗", 12: "準中型",
          13: "タクシー", 14: "標章車", 15: "タイヤチェーン未装着車"},
    "B": {1: "大型", 2: "大乗", 3: "大型等", 4: "大型バス", 5: "バス", 6: "マイクロ",
          7: "路線バス"},
    "C": {1: "貨物", 2: "普貨", 3: "特定中貨", 4: "中貨", 5: "大貨等", 6: "大貨",
          7: "準中貨", 8: "小特", 9: "大特", 10: "けん引"},
    "D": {1: "二輪", 2: "自二輪", 3: "自転車", 4: "原付", 5: "小二輪", 6: "軽車両",
          7: "歩行者", 8: "遠隔小型", 9: "移動小型", 10: "特定原付",
          11: "特例特定原付", 15: "その他"},
}
# 自転車を含む対象コード: A=1(車両全般), D=3(普通自転車), D=6(軽車両)
BIKE_CODES = {("A", 1), ("D", 3), ("D", 6)}

# 道路種別コード(別記2 表7)
ROAD_TYPES = {"1": "一般道", "2": "高速自動車国道", "3": "自動車専用道路", "99": "その他"}

# 東京都全域(島しょ部を含む)の許容範囲。範囲外の頂点は破棄して件数を報告する。
TOKYO_BBOX = (138.9, 20.3, 142.3, 36.0)  # lon_min, lat_min, lon_max, lat_max


def http_get(url: str, timeout: int = 300) -> bytes:
    last = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            time.sleep(2**attempt)
    raise RuntimeError(f"download failed: {url}: {last}")


def ensure_zip(refresh: bool) -> Path:
    RAW.mkdir(parents=True, exist_ok=True)
    zip_path = RAW / "typeD_tokyo.zip"
    if zip_path.exists() and not refresh:
        return zip_path
    catalog = json.loads(http_get(CATALOG_URL, 60))
    (RAW / "catalog.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    entry = next(c for c in catalog if c.get("type") == "typeD")
    target = next(t for t in entry["targetList"] if t["id"] == TOKYO_ID)
    print(f"download {BASE_URL}{target['link']} (対象月 {entry.get('targetMonth')})")
    zip_path.write_bytes(http_get(BASE_URL + target["link"]))
    return zip_path


def decode_set(row: list, ci: dict, prefix: str, n: int) -> set:
    """{prefix}{n}_{A-D} の1セット分を (cat, code) の集合にする。"""
    found = set()
    for cat in "ABCD":
        col = f"{prefix}{n}_{cat}"
        v = (row[ci[col]] if col in ci and ci[col] < len(row) else "").strip()
        if not v or not v.isdigit():
            continue
        digits = v.zfill(15)[-15:]
        for pos in range(1, 16):
            if digits[-pos] != "0":
                found.add((cat, pos))
    return found


def decode_vehicles(row: list, ci: dict, prefix: str, n_sets: int = 5) -> set:
    """対象車両コード / 除外車両コード の全セットの和集合。"""
    found = set()
    for n in range(1, n_sets + 1):
        found |= decode_set(row, ci, prefix, n)
    return found


def set_condition(row: list, ci: dict, kind: str, n: int) -> str:
    """kind は '対象' か '除外'。そのセットの期間・時間・曜日を文字列にする(なければ空)。"""
    def g(name):
        i = ci.get(name)
        return row[i].strip() if i is not None and i < len(row) else ""

    parts = []
    d1, d2 = g(f"{kind}期間{n}_開始"), g(f"{kind}期間{n}_終了")
    t1, t2 = g(f"{kind}時間{n}_開始" if kind == "除外" else f"規制時間{n}_開始"), \
        g(f"{kind}時間{n}_終了" if kind == "除外" else f"規制時間{n}_終了")
    if kind == "対象":
        d1, d2 = g(f"対象期間{n}_開始"), g(f"対象期間{n}_終了")
    w = g(f"{kind}曜日コード{n}" if kind == "除外" else f"規制曜日コード{n}")
    if d1 or d2:
        parts.append(f"期間{d1}-{d2}")
    if t1 or t2:
        parts.append(f"時間{t1}-{t2}")
    if w:
        parts.append(f"曜日コード{w}")
    return " ".join(parts)


def describe_sets(row: list, ci: dict, kind: str) -> str | None:
    """各セットの車両と条件を 'セット1: 車両 / 時間0800-1800' の形で連結する。"""
    prefix = "対象車両コード" if kind == "対象" else "除外車両コード"
    out = []
    for n in range(1, 6):
        v = vehicle_labels(decode_set(row, ci, prefix, n))
        cond = set_condition(row, ci, kind, n)
        if v or cond:
            out.append(f"[{n}] {', '.join(v) or '全車両'}" + (f" ({cond})" if cond else ""))
    return " ; ".join(out) or None


def classify_bike(targets: set, exclude_sets: list):
    """自転車が規制対象かを判定する。

    targets: 全セットの対象コードの和集合
    exclude_sets: [(除外コード集合, 期間・時間・曜日の条件文字列), ...]
    戻り値: (残すか, bike_status, 落とす理由)
    """
    if targets and not (targets & BIKE_CODES):
        return False, None, "対象車両に自転車を含まない"
    bike_excl = [cond for codes, cond in exclude_sets if codes & BIKE_CODES]
    if any(not cond for cond in bike_excl):
        return False, None, "除外車両に自転車を含む"
    if bike_excl:
        return True, "条件付きで自転車を除外(他の日時は自転車も規制対象)", None
    return True, ("対象車両に自転車を含む" if targets else "対象車両の指定なし(全車両)"), None


def vehicle_labels(codes: set) -> list:
    return [VEHICLE_NAMES[c].get(p, f"{c}{p}") for c, p in sorted(codes)]


def parse_coords(s: str, stats: Counter) -> list:
    pts = []
    for tok in s.split(";"):
        parts = tok.split()
        if len(parts) < 2:
            continue
        try:
            lon, lat = float(parts[0]), float(parts[1])
        except ValueError:
            stats["bad_vertex"] += 1
            continue
        if not (TOKYO_BBOX[0] <= lon <= TOKYO_BBOX[2] and TOKYO_BBOX[1] <= lat <= TOKYO_BBOX[3]):
            stats["vertex_outside_tokyo_bbox"] += 1
            continue
        pts.append([round(lon, 6), round(lat, 6)])
    return pts


def build_geometry(pts: list, shape_code: str):
    if not pts:
        return None
    if len(pts) == 1:
        return {"type": "Point", "coordinates": pts[0]}
    if shape_code == "3" and len(pts) >= 4:
        ring = pts if pts[0] == pts[-1] else pts + [pts[0]]
        return {"type": "Polygon", "coordinates": [ring]}
    return {"type": "LineString", "coordinates": pts}


def clean(v: str):
    v = v.strip()
    return v if v else None


def main() -> None:
    refresh = "--refresh" in sys.argv
    zip_path = ensure_zip(refresh)
    OUT.mkdir(parents=True, exist_ok=True)

    features = {name: [] for name in set(CODE_TO_LAYER.values())}
    stats = Counter()
    per_code = Counter()
    excluded = Counter()
    stop_dir = Counter()

    with zipfile.ZipFile(zip_path) as z:
        member = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        with z.open(member) as f:
            reader = csv.reader(io.TextIOWrapper(f, encoding="cp932", errors="replace", newline=""))
            head = next(reader)
            ci = {c: i for i, c in enumerate(head)}

            def col(row, name):
                i = ci.get(name)
                return row[i] if i is not None and i < len(row) else ""

            for row in reader:
                stats["rows_total"] += 1
                code = col(row, "共通規制種別コード").strip()
                layer = CODE_TO_LAYER.get(code)
                if not layer:
                    continue
                per_code[code] += 1

                targets = decode_vehicles(row, ci, "対象車両コード")
                excludes = decode_vehicles(row, ci, "除外車両コード")
                bike_status = "対象"
                if code in VEHICLE_FILTERED:
                    # 対象車両の指定が空なら全車両(歩行者含む)が対象
                    exclude_sets = [
                        (decode_set(row, ci, "除外車両コード", n), set_condition(row, ci, "除外", n))
                        for n in range(1, 6)
                    ]
                    keep, bike_status, reason = classify_bike(targets, exclude_sets)
                    if not keep:
                        excluded[(code, reason)] += 1
                        continue
                    if bike_status.startswith("条件付き"):
                        excluded[(code, "条件付き除外(残した)")] += 1

                pts = parse_coords(col(row, "規制場所の経度緯度"), stats)
                geom = build_geometry(pts, col(row, "点・線・面コード").strip())
                if geom is None:
                    stats["no_geometry"] += 1
                    continue

                props = {
                    "code": int(code),
                    "regulation": CODE_NAMES[code],
                    "name_pref": clean(col(row, "県別規制種別名称")),
                    "intersection": clean(col(row, "交差点名称(踏切名含む)")),
                    "road_name": clean(col(row, "路線名(代表)")),
                    "road_type": ROAD_TYPES.get(col(row, "道路種別コード").strip()),
                    "approach_text": clean(col(row, "進入方向(文字)")),
                    "approach_coords": clean(col(row, "進入方向(座標)")),
                    "targets": describe_sets(row, ci, "対象"),
                    "excludes": describe_sets(row, ci, "除外"),
                    "condition": clean(col(row, "規制条件")),
                    "side_code": clean(col(row, "片側・両側コード")),
                    "length_m": clean(col(row, "距離・延長")),
                    "remarks": clean(col(row, "備考")),
                    "revised": clean(col(row, "意思決定改正日")),
                    "unique_key": clean(col(row, "ユニークキー")),
                }
                if code in VEHICLE_FILTERED:
                    props["bike_status"] = bike_status
                if code == "63":
                    has_text = props["approach_text"] is not None
                    has_coord = props["approach_coords"] is not None
                    stop_dir[(has_text, has_coord)] += 1
                    props["has_direction"] = bool(has_text or has_coord)
                    props["target_note"] = (
                        None if props["targets"] else "対象車両情報なし"
                    )
                props = {k: v for k, v in props.items() if v is not None}
                features[layer].append(
                    {"type": "Feature", "geometry": geom, "properties": props}
                )

    report = {
        "source_zip": zip_path.name,
        "csv_member_encoding": "cp932",
        "rows_total": stats["rows_total"],
        "rows_per_code": dict(sorted(per_code.items(), key=lambda x: int(x[0]))),
        "excluded_by_vehicle": {f"{c}:{r}": n for (c, r), n in excluded.items()},
        "vertex_outside_tokyo_bbox": stats["vertex_outside_tokyo_bbox"],
        "bad_vertex": stats["bad_vertex"],
        "no_geometry": stats["no_geometry"],
        "stop_direction": {
            "text_and_coords": stop_dir[(True, True)],
            "text_only": stop_dir[(True, False)],
            "coords_only": stop_dir[(False, True)],
            "none": stop_dir[(False, False)],
        },
        "layers": {},
    }
    for name, feats in sorted(features.items()):
        path = OUT / f"{name}.geojson"
        path.write_text(
            json.dumps({"type": "FeatureCollection", "features": feats}, ensure_ascii=False,
                       separators=(",", ":")),
            encoding="utf-8",
        )
        report["layers"][name] = {"count": len(feats), "bytes": path.stat().st_size}
        print(f"{name}: {len(feats)} features, {path.stat().st_size/1e6:.1f} MB")
    (OUT.parent / "jartic_parse_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
