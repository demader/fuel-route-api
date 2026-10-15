"""API tests with the routing service mocked -- no network access needed."""
import tempfile
from decimal import Decimal
from pathlib import Path
from unittest import mock

import numpy as np
from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings

from planner.models import FuelStation
from planner.services.stations import reset_station_index

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}

# A straight east-west route along latitude 40 from Denver-ish to Chicago-ish (~900 mi).
START, FINISH = "40.0,-105.0", "40.0,-88.0"


def encode_polyline(coords, precision=6):
    """Minimal encoder used to build fake OSRM responses."""
    out, prev = [], [0, 0]
    for lon, lat in coords:
        for k, value in enumerate((lat, lon)):
            v = int(round(value * 10 ** precision))
            delta, prev[k] = v - prev[k], v
            delta = ~(delta << 1) if delta < 0 else delta << 1
            while delta >= 0x20:
                out.append(chr((0x20 | (delta & 0x1F)) + 63))
                delta >>= 5
            out.append(chr(delta + 63))
    return "".join(out)


def fake_osrm(distance_miles=900.0):
    coords = [(lon, 40.0) for lon in np.linspace(-105.0, -88.0, 200)]
    payload = {"code": "Ok", "routes": [{
        "geometry": encode_polyline(coords),
        "distance": distance_miles * 1609.344,
        "duration": distance_miles / 60 * 3600,
    }]}
    return mock.Mock(json=lambda: payload)


def station(opis_id, lon, price, lat=40.0):
    return FuelStation(
        opis_id=opis_id, name=f"Stop {opis_id}", address="I-80", city="Town", state="NE",
        retail_price=Decimal(str(price)), latitude=lat, longitude=lon,
        geocode_source=FuelStation.GeocodeSource.GAZETTEER,
    )


@override_settings(CACHES=LOCMEM, GEOCODER_FALLBACK_ENABLED=False)
class RouteApiTests(TestCase):
    def setUp(self):
        cache.clear()
        reset_station_index()
        FuelStation.objects.bulk_create([
            station(1, -105.0, 3.50),           # at the start
            station(2, -101.0, 2.90),           # ~210 mi in, cheap
            station(3, -97.0, 3.80),            # ~420 mi in, expensive
            station(4, -94.0, 3.10),            # ~580 mi in
            station(5, -91.0, 3.30),            # ~740 mi in
            station(6, -97.0, 2.00, lat=42.0),  # cheapest but ~140 mi off the route: ignored
        ])

    def tearDown(self):
        reset_station_index()

    def get(self, **params):
        return self.client.get("/api/route/", {"start": START, "finish": FINISH, **params},
                               HTTP_ACCEPT="application/json")

    @mock.patch("planner.services.routing.session.get")
    def test_plan_uses_single_routing_call_and_cache(self, osrm):
        osrm.return_value = fake_osrm()
        first = self.get().json()
        self.assertEqual(osrm.call_count, 1)
        self.assertEqual(first["meta"]["external_api_calls"], {"routing": 1, "geocoding": 0})

        # Same trip again and its map page: served from cache, zero extra routing calls.
        second = self.get().json()
        page = self.client.get("/api/route/map/", {"start": START, "finish": FINISH})
        self.assertEqual(osrm.call_count, 1)
        self.assertTrue(second["meta"]["route_cache_hit"])
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "leaflet")

        summary = first["summary"]
        self.assertAlmostEqual(summary["total_gallons"], 90.0, delta=0.2)  # 900 mi / 10 mpg
        ids = [s["station_id"] for s in first["fuel_stops"]]
        self.assertNotIn(6, ids)            # off-route station never used
        self.assertNotIn(3, ids)            # expensive station skipped
        self.assertIn(2, ids)               # cheap station used
        self.assertAlmostEqual(summary["total_fuel_cost"],
                               sum(s["cost"] for s in first["fuel_stops"]), delta=0.02)
        self.assertEqual(first["route"]["geometry"]["type"], "LineString")
        self.assertIn("/api/route/map/?", first["map_url"])

    @mock.patch("planner.services.routing.session.get")
    def test_post_json_body(self, osrm):
        osrm.return_value = fake_osrm()
        resp = self.client.post("/api/route/", {"start": START, "finish": FINISH, "include_geometry": False,
                                                "start_with_full_tank": True}, content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertNotIn("geometry", body["route"])
        self.assertTrue(body["vehicle"]["starts_with_full_tank"])
        self.assertAlmostEqual(body["summary"]["total_gallons"], 40.0, delta=0.2)  # 900 - 500 mi

    def test_missing_params_is_400(self):
        resp = self.client.get("/api/route/", {"start": START}, HTTP_ACCEPT="application/json")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("finish", resp.json()["errors"])

    def test_location_outside_usa_is_400(self):
        resp = self.get(finish="48.8566,2.3522")
        self.assertEqual(resp.status_code, 400)
        self.assertIn("outside the USA", resp.json()["error"])

    @mock.patch("planner.services.routing.session.get")
    def test_unreachable_gap_is_422(self, osrm):
        osrm.return_value = fake_osrm()
        FuelStation.objects.exclude(opis_id=1).delete()
        reset_station_index()
        resp = self.get()
        self.assertEqual(resp.status_code, 422)
        self.assertIn("No fuel station reachable", resp.json()["error"])

    @mock.patch("planner.services.routing.session.get")
    def test_no_route_is_502(self, osrm):
        osrm.return_value = mock.Mock(json=lambda: {"code": "NoRoute", "message": "Impossible route"})
        resp = self.get()
        self.assertEqual(resp.status_code, 502)

    def test_health(self):
        self.assertEqual(self.client.get("/api/health/", HTTP_ACCEPT="application/json").json()["stations_loaded"], 6)


class ImportCommandTests(TestCase):
    CSV = (
        "OPIS Truckstop ID,Truckstop Name,Address,City,State,Rack ID,Retail Price\n"
        '7,WOODSHED OF BIG CABIN,"I-44, EXIT 283 & US-69",Big Cabin,OK,307,3.00733333\n'
        "105,TA SAGINAW,\"I-75, EXIT 144-B\",Bridgeport,MI,260,3.339\n"
        "105,TA SAGINAW,\"I-75, EXIT 144-B\",Bridgeport,MI,260,3.269\n"
        "900,FLYING J,HWY 2,Calgary,AB,1,1.50\n"
        "901,MYSTERY STOP,NOWHERE RD,Notarealtown,TX,1,2.50\n"
    )

    def test_import_dedupes_filters_and_geocodes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prices.csv"
            path.write_text(self.CSV, encoding="utf-8")
            call_command("import_fuel_prices", str(path), stdout=mock.Mock())
        self.assertEqual(FuelStation.objects.count(), 2)  # Canada skipped, unknown town skipped
        saginaw = FuelStation.objects.get(opis_id=105)
        self.assertEqual(saginaw.retail_price, Decimal("3.269"))  # lowest of the duplicate rows
        self.assertEqual(saginaw.price_entries, 2)
        self.assertAlmostEqual(saginaw.latitude, 43.36, delta=0.1)
