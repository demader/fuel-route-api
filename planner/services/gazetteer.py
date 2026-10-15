"""Offline US gazetteer (Census places, township subdivisions and ZIP codes).

Loaded lazily once per process; lookups are pure dict hits, so geocoding a
"City, ST" or ZIP input costs no external API call.
"""
import csv
import gzip
import threading
from dataclasses import dataclass
from pathlib import Path

from .textnorm import normalize

GAZETTEER_PATH = Path(__file__).resolve().parent.parent / "data" / "us_gazetteer.csv.gz"


@dataclass(frozen=True)
class Place:
    name: str
    state: str
    lat: float
    lon: float


class Gazetteer:
    def __init__(self, path: Path = GAZETTEER_PATH):
        self.places: dict[tuple[str, str], Place] = {}
        self.zips: dict[str, Place] = {}
        with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                lat, lon = float(row["lat"]), float(row["lon"])
                if row["kind"] == "zip":
                    self.zips[row["key"]] = Place(row["key"], "", lat, lon)
                else:
                    self.places[(row["state"], row["key"])] = Place(row["name"], row["state"], lat, lon)

    def city(self, city: str, state: str) -> Place | None:
        return self.places.get((state, normalize(city)))

    def zip_code(self, code: str) -> Place | None:
        return self.zips.get(code)


_instance: Gazetteer | None = None
_lock = threading.Lock()


def get_gazetteer() -> Gazetteer:
    global _instance
    if _instance is None:
        with _lock:
            if _instance is None:
                _instance = Gazetteer()
    return _instance
