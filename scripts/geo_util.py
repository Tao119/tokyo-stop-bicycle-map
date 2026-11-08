"""共通の幾何ユーティリティ。"""
import numpy as np


def points_in_rings(px: np.ndarray, py: np.ndarray, rings) -> np.ndarray:
    """偶奇規則(レイキャスト)。ringsは [(lon,lat),...] のリスト群。"""
    inside = np.zeros(px.shape, dtype=bool)
    for ring in rings:
        r = np.asarray(ring, dtype=float)
        x0, y0 = r[:-1, 0], r[:-1, 1]
        x1, y1 = r[1:, 0], r[1:, 1]
        sel = (px >= r[:, 0].min()) & (px <= r[:, 0].max()) & (py >= r[:, 1].min()) & (py <= r[:, 1].max())
        idx = np.nonzero(sel)[0]
        if idx.size == 0:
            continue
        sx, sy = px[idx], py[idx]
        crossings = np.zeros(idx.size, dtype=bool)
        for ax, ay, bx, by in zip(x0, y0, x1, y1):
            if ay == by:
                continue
            cond = ((ay > sy) != (by > sy)) & (sx < (bx - ax) * (sy - ay) / (by - ay) + ax)
            crossings ^= cond
        inside[idx] ^= crossings
    return inside
