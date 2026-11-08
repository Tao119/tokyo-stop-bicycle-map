"""scripts/ の純粋関数の単体テスト。実行: python3 -m unittest discover -s tests -v"""
import sys
import unittest
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import build_osm  # noqa: E402
import parse_jartic as pj  # noqa: E402
import quality_check as qc  # noqa: E402
from geo_util import points_in_rings  # noqa: E402

HEAD = [f"{p}{n}_{c}" for p in ("対象車両コード", "除外車両コード") for n in range(1, 6) for c in "ABCD"]
CI = {c: i for i, c in enumerate(HEAD)}


def row(**kw):
    r = [""] * len(HEAD)
    for k, v in kw.items():
        r[CI[k]] = v
    return r


class DecodeVehicles(unittest.TestCase):
    def test_all_vehicles_code(self):
        t = pj.decode_vehicles(row(対象車両コード1_A="1"), CI, "対象車両コード")
        self.assertEqual(t, {("A", 1)})
        self.assertTrue(t & pj.BIKE_CODES)

    def test_bicycle_bit(self):
        t = pj.decode_vehicles(row(対象車両コード1_D="100"), CI, "対象車両コード")
        self.assertEqual(t, {("D", 3)})
        self.assertEqual(pj.vehicle_labels(t), ["自転車"])

    def test_multiple_bits_and_sets(self):
        t = pj.decode_vehicles(row(対象車両コード1_D="100100", 対象車両コード2_A="1000"), CI, "対象車両コード")
        self.assertEqual(t, {("D", 3), ("D", 6), ("A", 4)})

    def test_exclusion_prefix_is_separate(self):
        r = row(除外車両コード1_D="100")
        self.assertEqual(pj.decode_vehicles(r, CI, "対象車両コード"), set())
        self.assertEqual(pj.decode_vehicles(r, CI, "除外車両コード"), {("D", 3)})

    def test_empty_and_non_numeric_ignored(self):
        self.assertEqual(pj.decode_vehicles(row(対象車両コード1_A="x", 対象車両コード1_B=" "), CI, "対象車両コード"), set())

    def test_non_bike_target(self):
        t = pj.decode_vehicles(row(対象車両コード1_B="1", 対象車両コード1_C="1"), CI, "対象車両コード")
        self.assertFalse(t & pj.BIKE_CODES)


class ClassifyBike(unittest.TestCase):
    BIKE_EXCL = {("D", 3)}

    def test_not_target(self):
        self.assertEqual(pj.classify_bike({("B", 1)}, [])[0], False)

    def test_unconditional_exclusion_drops(self):
        keep, _, reason = pj.classify_bike({("A", 1)}, [(self.BIKE_EXCL, "")])
        self.assertFalse(keep)
        self.assertIn("除外", reason)

    def test_conditional_exclusion_kept(self):
        keep, status, _ = pj.classify_bike({("A", 1)}, [(self.BIKE_EXCL, "曜日コード3")])
        self.assertTrue(keep)
        self.assertTrue(status.startswith("条件付き"))

    def test_non_bike_exclusion_keeps(self):
        keep, status, _ = pj.classify_bike({("A", 1)}, [({("A", 13)}, ""), ({("B", 7)}, "")])
        self.assertTrue(keep)
        self.assertEqual(status, "対象車両に自転車を含む")

    def test_no_target_means_all(self):
        self.assertEqual(pj.classify_bike(set(), []), (True, "対象車両の指定なし(全車両)", None))

    def test_set_condition_and_describe(self):
        head = ["除外車両コード1_D", "除外曜日コード1", "除外時間1_開始", "除外時間1_終了"]
        ci2 = {c: i for i, c in enumerate(head)}
        r = ["100", "3", "0800", "1800"]
        self.assertEqual(pj.set_condition(r, ci2, "除外", 1), "時間0800-1800 曜日コード3")
        self.assertEqual(pj.describe_sets(r, ci2, "除外"), "[1] 自転車 (時間0800-1800 曜日コード3)")


class ParseCoords(unittest.TestCase):
    def test_basic(self):
        st = Counter()
        self.assertEqual(pj.parse_coords("139.5 35.5;139.6 35.6", st), [[139.5, 35.5], [139.6, 35.6]])

    def test_drops_out_of_range_and_bad(self):
        st = Counter()
        pts = pj.parse_coords("0 0;139.5 35.5;abc def;139.7", st)
        self.assertEqual(pts, [[139.5, 35.5]])
        self.assertEqual(st["vertex_outside_tokyo_bbox"], 1)
        self.assertEqual(st["bad_vertex"], 1)

    def test_geometry_types(self):
        self.assertIsNone(pj.build_geometry([], "1"))
        self.assertEqual(pj.build_geometry([[1, 2]], "2")["type"], "Point")
        self.assertEqual(pj.build_geometry([[1, 2], [3, 4]], "2")["type"], "LineString")
        sq = [[0, 0], [1, 0], [1, 1], [0, 1]]
        poly = pj.build_geometry(sq, "3")
        self.assertEqual(poly["type"], "Polygon")
        self.assertEqual(poly["coordinates"][0][0], poly["coordinates"][0][-1])


class GeoUtil(unittest.TestCase):
    SQUARE = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]

    def test_points_in_rings(self):
        px, py = np.array([5.0, 11.0, -1.0, 9.9]), np.array([5.0, 5.0, 5.0, 0.1])
        self.assertEqual(points_in_rings(px, py, [self.SQUARE]).tolist(), [True, False, False, True])

    def test_stitch_reverses_segments(self):
        rings = build_osm.stitch([[[0, 0], [1, 0]], [[1, 1], [1, 0]], [[1, 1], [0, 1]], [[0, 1], [0, 0]]])
        self.assertEqual(len(rings), 1)
        self.assertEqual(rings[0][0], rings[0][-1])

    def test_cycleway_detection(self):
        self.assertTrue(build_osm.has_cycleway_infra({"highway": "cycleway"}))
        self.assertTrue(build_osm.has_cycleway_infra({"highway": "residential", "cycleway:left": "lane"}))
        self.assertFalse(build_osm.has_cycleway_infra({"highway": "residential", "cycleway": "no"}))
        self.assertFalse(build_osm.has_cycleway_infra({"highway": "residential", "cycleway": "shared_lane"}))
        self.assertTrue(build_osm.has_cycleway_shared({"highway": "residential", "cycleway": "shared_lane"}))
        self.assertFalse(build_osm.has_cycleway_shared({"highway": "residential", "cycleway": "lane"}))
        self.assertFalse(build_osm.has_cycleway_shared({"highway": "residential", "cycleway:right": "use_sidepath"}))
        self.assertFalse(build_osm.has_cycleway_infra({"highway": "residential", "cycleway:both": "separate"}))


class MatchFraction(unittest.TestCase):
    def test_radius(self):
        a = (np.array([0.0, 100.0]), np.array([0.0, 0.0]))
        b = (np.array([10.0]), np.array([0.0]))
        self.assertEqual(qc.match_fraction(a, b, 15), 0.5)
        self.assertEqual(qc.match_fraction(a, b, 5), 0.0)
        self.assertEqual(qc.match_fraction(b, a, 15), 1.0)

    def test_project_scale(self):
        x, y = qc.project(np.array([139.0, 139.001]), np.array([35.68, 35.68]))
        self.assertAlmostEqual(float(x[1] - x[0]), 90.5, delta=1.0)


if __name__ == "__main__":
    unittest.main()
