"""Import the OPIS fuel-price CSV into FuelStation, geocoding each station once.

    python manage.py import_fuel_prices                       # uses data/fuel-prices-for-be-assessment.csv
    python manage.py import_fuel_prices path/to/file.csv --price-strategy avg
    python manage.py import_fuel_prices --geocode-missing     # look up unknown towns on Nominatim

Geocoding is done offline against the Census gazetteer (city/state centroid). Towns the
gazetteer doesn't know (mostly unincorporated communities) are read from
planner/data/geocode_overrides.csv; --geocode-missing fills that file via Nominatim
(1 request/second, per its usage policy) so later imports need no network at all.
"""
import csv
import time
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from planner.models import FuelStation
from planner.services.gazetteer import get_gazetteer
from planner.services.http import session
from planner.services.stations import reset_station_index
from planner.services.textnorm import US_STATES, normalize

DEFAULT_CSV = Path(settings.BASE_DIR) / "data" / "fuel-prices-for-be-assessment.csv"
OVERRIDES = Path(__file__).resolve().parents[2] / "data" / "geocode_overrides.csv"
OVERRIDE_FIELDS = ["state", "city", "lat", "lon", "source"]


def load_overrides() -> dict[tuple[str, str], tuple[float, float]]:
    if not OVERRIDES.exists():
        return {}
    with OVERRIDES.open(encoding="utf-8", newline="") as fh:
        return {(r["state"], normalize(r["city"])): (float(r["lat"]), float(r["lon"])) for r in csv.DictReader(fh)}


class Command(BaseCommand):
    help = "Import fuel prices from the OPIS CSV and geocode each truck stop."

    def add_arguments(self, parser):
        parser.add_argument("csv_path", nargs="?", default=str(DEFAULT_CSV))
        parser.add_argument("--price-strategy", choices=["min", "avg"], default="min",
                            help="How to merge multiple price rows for one OPIS ID (default: min).")
        parser.add_argument("--geocode-missing", action="store_true",
                            help="Query Nominatim for towns not in the gazetteer/overrides and cache them.")

    def handle(self, *args, csv_path, price_strategy, geocode_missing, **opts):
        path = Path(csv_path)
        if not path.exists():
            raise CommandError(f"CSV not found: {path}. Copy the provided fuel-prices-for-be-assessment.csv "
                               f"into data/ or pass its path as the first argument.")

        groups: dict[int, list[dict]] = defaultdict(list)
        skipped_non_us = 0
        with path.open(encoding="utf-8-sig", newline="") as fh:
            for raw in csv.DictReader(fh):
                row = {k.strip(): (v or "").strip() for k, v in raw.items()}
                if row["State"] not in US_STATES:
                    skipped_non_us += 1  # Canadian provinces etc.
                    continue
                groups[int(row["OPIS Truckstop ID"])].append(row)

        gazetteer = get_gazetteer()
        overrides = load_overrides()
        if geocode_missing:
            self._fill_missing(groups, gazetteer, overrides)

        stations, unresolved = [], defaultdict(int)
        for opis_id, rows in groups.items():
            prices = [Decimal(r["Retail Price"]) for r in rows]
            best = min(rows, key=lambda r: Decimal(r["Retail Price"]))
            price = min(prices) if price_strategy == "min" else sum(prices) / len(prices)
            city, state = best["City"], best["State"]

            if (state, normalize(city)) in overrides:
                (lat, lon), source = overrides[(state, normalize(city))], FuelStation.GeocodeSource.OVERRIDE
            elif place := gazetteer.city(city, state):
                lat, lon, source = place.lat, place.lon, FuelStation.GeocodeSource.GAZETTEER
            else:
                unresolved[(city, state)] += 1
                continue

            stations.append(FuelStation(
                opis_id=opis_id, name=best["Truckstop Name"], address=best["Address"],
                city=city, state=state, rack_id=int(best["Rack ID"]) if best["Rack ID"] else None,
                retail_price=round(price, 6), price_entries=len(rows),
                latitude=lat, longitude=lon, geocode_source=source,
            ))

        with transaction.atomic():
            FuelStation.objects.all().delete()
            FuelStation.objects.bulk_create(stations, batch_size=1000)
        reset_station_index()

        self.stdout.write(self.style.SUCCESS(
            f"Imported {len(stations)} stations ({sum(len(r) for r in groups.values())} US rows merged by "
            f"OPIS ID, price strategy '{price_strategy}'); skipped {skipped_non_us} non-US rows."
        ))
        if unresolved:
            sample = ", ".join(f"{c}, {s}" for (c, s) in list(unresolved)[:10])
            self.stdout.write(self.style.WARNING(
                f"{sum(unresolved.values())} stations in {len(unresolved)} towns could not be geocoded "
                f"(e.g. {sample}). Re-run with --geocode-missing to resolve them."
            ))

    def _fill_missing(self, groups, gazetteer, overrides):
        missing = sorted({
            (r["City"], r["State"]) for rows in groups.values() for r in rows[:1]
            if (r["State"], normalize(r["City"])) not in overrides and not gazetteer.city(r["City"], r["State"])
        })
        if not missing:
            return
        self.stdout.write(f"Geocoding {len(missing)} towns via Nominatim (~1/s)...")
        new_file = not OVERRIDES.exists()
        with OVERRIDES.open("a", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=OVERRIDE_FIELDS)
            if new_file:
                writer.writeheader()
            for city, state in missing:
                time.sleep(1.1)
                try:
                    resp = session.get(settings.NOMINATIM_URL, timeout=settings.EXTERNAL_API_TIMEOUT, params={
                        "city": city, "state": US_STATES[state], "country": "USA",
                        "format": "jsonv2", "limit": 1,
                    })
                    resp.raise_for_status()
                    hits = resp.json()
                except Exception as exc:  # network hiccup: leave this town unresolved
                    self.stdout.write(self.style.WARNING(f"  {city}, {state}: {exc}"))
                    continue
                if not hits:
                    self.stdout.write(self.style.WARNING(f"  {city}, {state}: not found"))
                    continue
                lat, lon = float(hits[0]["lat"]), float(hits[0]["lon"])
                writer.writerow({"state": state, "city": city, "lat": f"{lat:.6f}", "lon": f"{lon:.6f}",
                                 "source": "nominatim"})
                overrides[(state, normalize(city))] = (lat, lon)
