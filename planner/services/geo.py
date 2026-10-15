"""Small vectorised geometry helpers (numpy only)."""
import numpy as np

EARTH_RADIUS_MI = 3958.7613

# Generous box around the 50 states + DC (lower 48, Alaska incl. Aleutians, Hawaii).
_US_BOXES = (
    (24.3, 49.5, -125.0, -66.8),   # contiguous US
    (51.0, 71.6, -180.0, -129.9),  # Alaska
    (18.8, 22.4, -160.4, -154.7),  # Hawaii
)


def in_usa_bbox(lat: float, lon: float) -> bool:
    return any(s <= lat <= n and w <= lon <= e for s, n, w, e in _US_BOXES)


def to_unit_vectors(lat_deg, lon_deg) -> np.ndarray:
    """(N,) lat/lon arrays in degrees -> (N, 3) unit vectors on the sphere."""
    lat = np.radians(np.asarray(lat_deg, dtype=float))
    lon = np.radians(np.asarray(lon_deg, dtype=float))
    cos_lat = np.cos(lat)
    return np.column_stack((cos_lat * np.cos(lon), cos_lat * np.sin(lon), np.sin(lat)))


def chord_to_miles(chord) -> np.ndarray:
    """Straight-line chord length between unit vectors -> great-circle distance in miles."""
    return 2 * EARTH_RADIUS_MI * np.arcsin(np.clip(np.asarray(chord) / 2, 0, 1))


def haversine_miles(lat1, lon1, lat2, lon2) -> np.ndarray:
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_MI * np.arcsin(np.sqrt(a))


def resample_polyline(coords: np.ndarray, max_step_mi: float = 1.0, min_step_mi: float = 0.25):
    """Normalise vertex spacing of a [lon, lat] polyline.

    Dense vertices (closer than ``min_step_mi``) are thinned and long straight segments
    are densified so no gap exceeds ``max_step_mi``. The result is small enough to ship to
    the client and evenly spaced enough for nearest-vertex corridor matching.

    Returns (points [M, 2] as lon/lat, cumulative distance along the route in miles [M]).
    """
    coords = np.asarray(coords, dtype=float)
    lon, lat = coords[:, 0], coords[:, 1]
    seg = haversine_miles(lat[:-1], lon[:-1], lat[1:], lon[1:])
    cum = np.concatenate(([0.0], np.cumsum(seg)))
    if len(coords) < 2:
        return coords, cum

    # Thin: keep the first vertex of every min_step-long stretch, plus the final vertex.
    bucket = np.floor(cum / min_step_mi).astype(np.int64)
    keep = np.flatnonzero(np.diff(bucket, prepend=-1) != 0)
    if keep[-1] != len(cum) - 1:
        keep = np.append(keep, len(cum) - 1)
    pts, kcum = coords[keep], cum[keep]

    # Densify: split each gap into ceil(gap / max_step) equal parts. Linear interpolation
    # in lon/lat is accurate enough over a mile or two.
    gaps = np.diff(kcum)
    parts = np.maximum(1, np.ceil(gaps / max_step_mi).astype(np.int64))
    seg_idx = np.repeat(np.arange(len(gaps)), parts)
    step = np.arange(parts.sum()) - np.repeat(np.cumsum(parts) - parts, parts) + 1
    t = step / parts[seg_idx]
    new_pts = pts[seg_idx] + (pts[seg_idx + 1] - pts[seg_idx]) * t[:, None]
    new_cum = kcum[seg_idx] + gaps[seg_idx] * t
    return np.concatenate((pts[:1], new_pts)), np.concatenate((kcum[:1], new_cum))
