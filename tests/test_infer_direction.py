"""infer_direction の合成道路での単体テスト。実行: python3 -m unittest discover -s tests -v"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import infer_direction as idr  # noqa: E402


def way(wid, hw, pts_ids, coords, **tags):
    """coords は (x, y) メートル。geometry は経度緯度に戻して Overpass 形式にする。"""
    geom = [{"lon": x / idr.KX, "lat": y / idr.KY} for x, y in coords]
    return {"type": "way", "id": wid, "nodes": pts_ids, "geometry": geom, "tags": {"highway": hw, **tags}}


def t_junction(stem_hw="residential", main_hw="primary", **stem_tags):
    # 東西の主道路(node 1-2-3)に、南から細い道(node 4-2)が突き当たるT字路。交差点は node 2(0,0)
    return idr.Tile([
        way(1, main_hw, [1, 2, 3], [(-100, 0), (0, 0), (100, 0)]),
        way(2, stem_hw, [4, 5, 2], [(0, -100), (0, -50), (0, 0)], **stem_tags),
    ])


class Infer(unittest.TestCase):
    def test_stem_stop_heads_toward_main_road(self):
        # 細い道を北向きに走る車の停止位置は進行方向の左(西側)にある
        r = idr.infer_point(t_junction(), -1.8, -8.0)
        self.assertEqual(r["status"], "高")
        self.assertAlmostEqual(r["bearing"], 0, delta=5)
        self.assertIn("T字路の突き当たり側", r["basis"][0])

    def test_point_on_main_road_is_not_given_a_direction(self):
        # 大通りの中心線のすぐ脇(交差点の手前10m): 大通り走行中の向きは出さない
        r = idr.infer_point(t_junction(), 10.0, -1.8)
        self.assertEqual(r["status"], "推定不可")

    def test_left_right_offset_is_ignored(self):
        # 停止点が車線の左右どちらにずれていても、向きと信頼度は同じ
        left = idr.infer_point(t_junction(), -1.8, -8.0)
        right = idr.infer_point(t_junction(), 1.8, -8.0)
        self.assertEqual((left["status"], left["bearing"]), (right["status"], right["bearing"]))

    def test_short_block_between_two_minor_junctions_picks_nearer(self):
        # 北と南の主道路にはさまれた短いブロックで、北の交差点に近い点は北向き
        tile = idr.Tile([
            way(1, "primary", [1, 2, 3], [(-100, 40), (0, 40), (100, 40)]),
            way(2, "primary", [4, 5, 6], [(-100, -40), (0, -40), (100, -40)]),
            way(3, "residential", [5, 7, 2], [(0, -40), (0, 0), (0, 40)]),
        ])
        r = idr.infer_point(tile, 0.0, 25.0)
        self.assertEqual(r["bearing"], 0)
        self.assertEqual(r["status"], "中")

    def test_equal_rank_t_stem_is_still_high(self):
        # 同格でも、T字の突き当たり側が止まるのが普通
        r = idr.infer_point(t_junction(stem_hw="residential", main_hw="residential"), -1.8, -8.0)
        self.assertEqual(r["status"], "高")

    def test_equal_rank_four_way_is_low(self):
        # 同格の道路どうしの十字路は、どちらが止まるか分からない
        tile = idr.Tile([
            way(1, "residential", [1, 2, 3], [(-100, 0), (0, 0), (100, 0)]),
            way(2, "residential", [4, 2, 5], [(0, -100), (0, 0), (0, 100)]),
        ])
        r = idr.infer_point(tile, -1.8, -8.0)
        self.assertEqual(r["status"], "低")

    def test_stem_more_major_than_crossing_is_low(self):
        tile = t_junction(stem_hw="primary", main_hw="residential")
        r = idr.infer_point(tile, -1.8, -8.0)
        self.assertEqual(r["status"], "低")

    def test_oneway_away_from_junction_is_rejected(self):
        # 交差点から南へ離れる一方通行(way 2 の向きは南->北なので oneway=-1 は北->南)
        tile = t_junction(oneway="-1")
        r = idr.infer_point(tile, -1.8, -8.0)
        self.assertEqual(r["status"], "推定不可")

    def test_oneway_toward_junction_is_accepted(self):
        r = idr.infer_point(t_junction(oneway="yes"), -1.8, -8.0)
        self.assertEqual(r["status"], "高")
        self.assertAlmostEqual(r["bearing"], 0, delta=5)

    def test_no_road_nearby(self):
        r = idr.infer_point(t_junction(), 300.0, 300.0)
        self.assertEqual(r["status"], "推定不可")

    def test_far_from_junction_is_rejected(self):
        tile = idr.Tile([way(1, "primary", [1, 2, 3], [(-100, 0), (0, 0), (100, 0)]),
                         way(2, "residential", [4, 2], [(0, -300), (0, 0)])])
        self.assertEqual(idr.infer_point(tile, -1.8, -200.0)["status"], "推定不可")

    def test_rank_and_bearing(self):
        self.assertLess(idr.rank_of({"highway": "primary"}), idr.rank_of({"highway": "residential"}))
        self.assertGreater(idr.rank_of({"highway": "primary_link"}), idr.rank_of({"highway": "primary"}))
        self.assertAlmostEqual(idr.bearing(1, 0), 90)
        self.assertAlmostEqual(idr.bearing(0, -1), 180)


if __name__ == "__main__":
    unittest.main()
