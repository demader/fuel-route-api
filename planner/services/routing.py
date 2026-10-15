"""Driving route from the OSRM HTTP API -- exactly one call per (start, finish) pair.

The full-resolution geometry is requested once; everything else (corridor search,
fuel optimisation, map rendering) works from that single response. Results are
cached, so repeating a request -- or opening its map page -- makes zero calls.
"""
from dataclasses import dataclass

import numpy as np
import requests
from django.conf import settings
from django.core.cache import cache

from .geocoding import Location
from .http import session

METERS_PER_MILE = 1609.344


class RoutingError(RuntimeError):
    """The routing service failed or found no drivable route."""


@dataclass
class Route:
    coordinates: np.ndarray  # [N, 2] lon/lat
    distance_miles: float
    duration_seconds: float


def decode_polyline(encoded: str, precision: int = 6) -> np.ndarray:
    """Decode a Google encoded polyline into an [N, 2] lon/lat array (vectorised)."""
    raw = np.frombuffer(encoded.encode("ascii"), dtype=np.uint8).astype(np.int64) - 63
    if raw.size == 0:
        return np.empty((0, 2))
    ends = (raw & 0x20) == 0                                   # last 5-bit chunk of each value
    group = np.concatenate(([0], np.cumsum(ends)[:-1]))         # value index of every chunk
    starts = np.flatnonzero(np.concatenate(([True], ends[:-1])))
    shift = 5 * (np.arange(raw.size) - starts[group])
    values = np.add.reduceat((raw & 0x1F) << shift, starts)
    deltas = np.where(values & 1, ~(values >> 1), values >> 1)  # zig-zag decode
    latlon = np.cumsum(deltas.reshape(-1, 2), axis=0) / 10 ** precision
    return latlon[:, ::-1].copy()


def _cache_key(start: Location, finish: Location) -> str:
    # ~1 m precision; identical inputs always hit the same key.
    return f"route:{start.lat:.5f},{start.lon:.5f};{finish.lat:.5f},{finish.lon:.5f}"


def get_route(start: Location, finish: Location, stats: dict | None = None) -> Route:
    stats = stats if stats is not None else {}
    key = _cache_key(start, finish)
    cached = cache.get(key)
    if cached is not None:
        stats["route_cache_hit"] = True
        return Route(cached["coordinates"], cached["distance_miles"], cached["duration_seconds"])

    stats["routing_calls"] = stats.get("routing_calls", 0) + 1
    url = (
        f"{settings.OSRM_BASE_URL.rstrip('/')}/route/v1/driving/"
        f"{start.lon:.6f},{start.lat:.6f};{finish.lon:.6f},{finish.lat:.6f}"
    )
    # polyline6 is ~5x smaller on the wire than GeoJSON, which roughly halves the call time.
    params = {"overview": "full", "geometries": "polyline6", "alternatives": "false", "steps": "false"}
    try:
        resp = session.get(url, params=params, timeout=settings.EXTERNAL_API_TIMEOUT)
        payload = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise RoutingError(f"Routing service unavailable: {exc}") from exc

    if payload.get("code") != "Ok" or not payload.get("routes"):
        raise RoutingError(f"No drivable route found ({payload.get('code')}: {payload.get('message', 'no route')}).")

    best = payload["routes"][0]
    route = Route(
        coordinates=decode_polyline(best["geometry"], precision=6),
        distance_miles=best["distance"] / METERS_PER_MILE,
        duration_seconds=float(best["duration"]),
    )
    cache.set(
        key,
        {
            "coordinates": route.coordinates,  # ndarray pickles as one compact binary blob
            "distance_miles": route.distance_miles,
            "duration_seconds": route.duration_seconds,
        },
        settings.ROUTE_CACHE_SECONDS,
    )
    return route
