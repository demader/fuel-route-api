"""Resolve user input ("City, ST", ZIP, "lat,lon" or a free-form address) to a US coordinate.

Resolution order -- the first three never touch the network:
  1. "lat,lon" literal
  2. 5-digit ZIP code            (offline Census gazetteer)
  3. "City, ST" / "City, State"  (offline Census gazetteer)
  4. Nominatim (OpenStreetMap) search restricted to the US, cached
"""
import hashlib
import re
from dataclasses import asdict, dataclass

import requests
from django.conf import settings
from django.core.cache import cache

from .gazetteer import get_gazetteer
from .geo import in_usa_bbox
from .http import session
from .textnorm import state_code

_LATLON = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")
_ZIP = re.compile(r"^\s*(\d{5})(?:-\d{4})?\s*$")
_COUNTRY_SUFFIX = re.compile(r",?\s*(usa|us|u\.s\.a?\.?|united states(?: of america)?)\s*$", re.IGNORECASE)


class LocationError(ValueError):
    """Input could not be resolved to a location inside the USA."""


@dataclass(frozen=True)
class Location:
    query: str
    label: str
    lat: float
    lon: float
    source: str  # coordinates | zip | gazetteer | nominatim

    def as_dict(self) -> dict:
        return asdict(self)


def resolve_location(query: str, stats: dict | None = None) -> Location:
    stats = stats if stats is not None else {}
    text = (query or "").strip()
    if not text:
        raise LocationError("Location is empty.")

    loc = _from_coordinates(text) or _from_zip(text) or _from_city_state(text) or _from_nominatim(text, stats)
    if loc is None:
        raise LocationError(f"Could not find '{text}' in the USA. Try 'City, ST', a ZIP code or 'lat,lon'.")
    if not in_usa_bbox(loc.lat, loc.lon):
        raise LocationError(f"'{text}' resolves to ({loc.lat:.4f}, {loc.lon:.4f}), which is outside the USA.")
    return loc


def _from_coordinates(text: str) -> Location | None:
    m = _LATLON.match(text)
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise LocationError(f"'{text}' is not a valid 'lat,lon' pair.")
    return Location(text, f"{lat:.5f}, {lon:.5f}", lat, lon, "coordinates")


def _from_zip(text: str) -> Location | None:
    m = _ZIP.match(text)
    if not m:
        return None
    place = get_gazetteer().zip_code(m.group(1))
    return Location(text, f"ZIP {place.name}", place.lat, place.lon, "zip") if place else None


def _from_city_state(text: str) -> Location | None:
    cleaned = _COUNTRY_SUFFIX.sub("", text).strip().rstrip(",")
    if "," not in cleaned:
        return None
    city, state_part = (p.strip() for p in cleaned.rsplit(",", 1))
    # "Austin, TX 78701" -> drop the trailing ZIP from the state part
    state_part = re.sub(r"\s+\d{5}(-\d{4})?$", "", state_part)
    code = state_code(state_part)
    if not code and re.fullmatch(r"[A-Za-z]{2}", state_part):
        # "Toronto, ON": a region code that isn't a US state -- don't fuzzy-match it.
        raise LocationError(f"'{state_part.upper()}' is not a US state; both locations must be in the USA.")
    if not code or not city:
        return None
    place = get_gazetteer().city(city, code)
    return Location(text, f"{place.name}, {code}", place.lat, place.lon, "gazetteer") if place else None


def _from_nominatim(text: str, stats: dict) -> Location | None:
    if not settings.GEOCODER_FALLBACK_ENABLED:
        return None
    key = "geocode:" + hashlib.sha1(text.lower().encode()).hexdigest()
    cached = cache.get(key)
    if cached is not None:
        return Location(**cached) if cached else None

    stats["geocoding_calls"] = stats.get("geocoding_calls", 0) + 1
    try:
        resp = session.get(
            settings.NOMINATIM_URL,
            params={"q": text, "format": "jsonv2", "countrycodes": "us", "limit": 1},
            timeout=settings.EXTERNAL_API_TIMEOUT,
        )
        resp.raise_for_status()
        results = resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise LocationError(f"Geocoding service unavailable while resolving '{text}': {exc}") from exc

    loc = None
    if results:
        r = results[0]
        loc = Location(text, r.get("display_name", text), float(r["lat"]), float(r["lon"]), "nominatim")
    cache.set(key, loc.as_dict() if loc else {}, settings.GEOCODE_CACHE_SECONDS)
    return loc
