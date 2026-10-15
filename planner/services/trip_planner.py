"""End-to-end trip planning: geocode -> one routing call -> stations on route -> cheapest plan."""
import time

import numpy as np
from django.conf import settings

from .geo import resample_polyline
from .geocoding import resolve_location
from .optimizer import plan_fuel
from .routing import get_route
from .stations import get_station_index


def plan_trip(start: str, finish: str, *, start_full: bool = False, corridor_miles: float | None = None,
              stop_penalty: float | None = None) -> dict:
    t0 = time.perf_counter()
    stats: dict = {"geocoding_calls": 0, "routing_calls": 0, "route_cache_hit": False}
    cfg = settings.FUEL_PLANNER
    corridor = float(corridor_miles if corridor_miles is not None else cfg["CORRIDOR_MILES"])
    max_range, mpg = float(cfg["MAX_RANGE_MILES"]), float(cfg["MILES_PER_GALLON"])
    penalty = float(stop_penalty if stop_penalty is not None else cfg["STOP_PENALTY_USD"])

    origin = resolve_location(start, stats)
    destination = resolve_location(finish, stats)
    route = get_route(origin, destination, stats)
    t_route = time.perf_counter()

    # Evenly spaced route points; rescale so positions agree with the router's distance.
    pts, cum = resample_polyline(route.coordinates)
    if cum[-1] > 0:
        cum = cum * (route.distance_miles / cum[-1])

    index = get_station_index()
    candidates = index.along_route(pts, cum, corridor)
    plan = plan_fuel(
        positions=[c.position_mi for c in candidates],
        prices=[float(index.price[c.station]) for c in candidates],
        total_miles=route.distance_miles,
        max_range=max_range,
        mpg=mpg,
        start_full=start_full,
        origin_window=float(cfg["ORIGIN_WINDOW_MILES"]),
        stop_penalty=penalty,
    )

    stops = []
    for n, purchase in enumerate(plan.purchases, start=1):
        cand = candidates[purchase.index]
        row = index.rows[cand.station]
        gallons = purchase.miles_of_fuel / mpg
        stops.append({
            "stop_number": n,
            "station_id": row["opis_id"],
            "name": row["name"],
            "address": row["address"],
            "city": row["city"],
            "state": row["state"],
            "latitude": row["latitude"],
            "longitude": row["longitude"],
            "price_per_gallon": round(purchase.price, 3),
            "miles_from_start": round(purchase.position_mi, 1),
            "miles_off_route": round(cand.offset_mi, 1),
            "fuel_on_arrival_miles": round(purchase.fuel_on_arrival_mi, 1),
            "gallons": round(gallons, 2),
            "cost": round(gallons * purchase.price, 2),
        })

    total_gallons = plan.total_gallons
    total_cost = plan.total_cost
    return {
        "start": origin.as_dict(),
        "finish": destination.as_dict(),
        "route": {
            "distance_miles": round(route.distance_miles, 1),
            "duration_hours": round(route.duration_seconds / 3600, 2),
            "geometry": {"type": "LineString", "coordinates": np.round(pts, 5).tolist()},
        },
        "vehicle": {
            "max_range_miles": max_range,
            "miles_per_gallon": mpg,
            "starts_with_full_tank": start_full,
            "corridor_miles": corridor,
            "stop_penalty_usd": penalty,
        },
        "fuel_stops": stops,
        "summary": {
            "total_fuel_cost": round(total_cost, 2),
            "total_gallons": round(total_gallons, 2),
            "number_of_stops": len(stops),
            "average_price_per_gallon": round(total_cost / total_gallons, 3) if total_gallons else None,
            "stations_considered": len(candidates),
        },
        "meta": {
            "external_api_calls": {
                "routing": stats["routing_calls"],
                "geocoding": stats["geocoding_calls"],
            },
            "route_cache_hit": stats["route_cache_hit"],
            "timing_ms": {
                "geocode_and_route": round((t_route - t0) * 1000, 1),
                "fuel_optimisation": round((time.perf_counter() - t_route) * 1000, 1),
                "total": round((time.perf_counter() - t0) * 1000, 1),
            },
        },
    }
