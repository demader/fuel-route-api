"""In-memory, vectorised index of all fuel stations.

The ~6.7k stations are loaded from the database once per process into numpy arrays.
Finding the stations that lie along a route is then a bounding-box filter followed by
a chunked matrix product of unit vectors (nearest route vertex per station) -- a few
milliseconds even for coast-to-coast routes, with no per-request database scan.
"""
import threading
from dataclasses import dataclass

import numpy as np

from .geo import EARTH_RADIUS_MI, chord_to_miles, to_unit_vectors

_CHUNK = 256  # stations per matmul block; bounds memory to ~CHUNK x route_points floats
_COARSE_SPACING_MI = 5.0


@dataclass
class Candidate:
    station: int          # row index into StationIndex arrays
    position_mi: float    # distance along the route to the closest route point
    offset_mi: float      # straight-line distance from that route point to the station


class StationIndex:
    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.lat = np.array([r["latitude"] for r in rows], dtype=float)
        self.lon = np.array([r["longitude"] for r in rows], dtype=float)
        self.price = np.array([float(r["retail_price"]) for r in rows], dtype=float)
        self.vec = to_unit_vectors(self.lat, self.lon) if rows else np.empty((0, 3))

    def __len__(self):
        return len(self.rows)

    def along_route(self, route_pts: np.ndarray, route_cum: np.ndarray, corridor_mi: float) -> list[Candidate]:
        """Stations within ``corridor_mi`` of the route, sorted by distance along it."""
        if not len(self) or len(route_pts) < 2:
            return []
        rlon, rlat = route_pts[:, 0], route_pts[:, 1]

        # 1) Cheap bounding-box prefilter (degrees), padded by the corridor width.
        pad_lat = np.degrees(corridor_mi / EARTH_RADIUS_MI)
        max_abs_lat = min(np.abs(rlat).max() + pad_lat, 89.0)
        pad_lon = pad_lat / np.cos(np.radians(max_abs_lat))
        in_box = np.flatnonzero(
            (self.lat >= rlat.min() - pad_lat) & (self.lat <= rlat.max() + pad_lat)
            & (self.lon >= rlon.min() - pad_lon) & (self.lon <= rlon.max() + pad_lon)
        )
        if not len(in_box):
            return []

        rvec = to_unit_vectors(rlat, rlon)

        # 2) Coarse pass against every k-th route point. Any route point lies within
        #    half a coarse spacing of a coarse point, so this slack never drops a station.
        gaps = np.diff(route_cum)
        k = max(1, int(_COARSE_SPACING_MI / max(float(np.median(gaps)), 1e-6)))
        coarse = np.unique(np.append(np.arange(0, len(rvec), k), len(rvec) - 1))
        # Every route point sits between two coarse points <= k gaps apart, so it is within
        # half of that (along the route, hence also in a straight line) of one of them.
        slack = k * float(gaps.max()) / 2
        _, coarse_off = self._nearest(in_box, rvec[coarse])
        near = in_box[coarse_off <= corridor_mi + slack]
        if not len(near):
            return []

        # 3) Exact nearest route vertex for the survivors only.
        nearest, offset = self._nearest(near, rvec)
        keep = offset <= corridor_mi
        cands = [
            Candidate(int(s), float(route_cum[n]), float(o))
            for s, n, o in zip(near[keep], nearest[keep], offset[keep])
        ]
        cands.sort(key=lambda c: (c.position_mi, self.price[c.station]))
        return cands

    def _nearest(self, stations: np.ndarray, rvec: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Index of, and miles to, the closest of ``rvec`` for each station.
        Max dot product of unit vectors == min great-circle distance."""
        nearest = np.empty(len(stations), dtype=int)
        best_dot = np.empty(len(stations))
        for start in range(0, len(stations), _CHUNK):
            dots = self.vec[stations[start:start + _CHUNK]] @ rvec.T
            idx = dots.argmax(axis=1)
            nearest[start:start + _CHUNK] = idx
            best_dot[start:start + _CHUNK] = dots[np.arange(len(idx)), idx]
        return nearest, chord_to_miles(np.sqrt(np.clip(2 - 2 * best_dot, 0, None)))


_index: StationIndex | None = None
_lock = threading.Lock()


def get_station_index() -> StationIndex:
    global _index
    if _index is None:
        with _lock:
            if _index is None:
                from planner.models import FuelStation

                fields = ("id", "opis_id", "name", "address", "city", "state",
                          "retail_price", "latitude", "longitude")
                _index = StationIndex(list(FuelStation.objects.values(*fields)))
    return _index


def reset_station_index():
    """Drop the cached index (used after re-importing prices and in tests)."""
    global _index
    with _lock:
        _index = None
