from unittest import mock

import numpy as np
from django.test import SimpleTestCase, override_settings

from planner.services.geo import haversine_miles, resample_polyline
from planner.services.geocoding import LocationError, resolve_location
from planner.services.routing import decode_polyline
from planner.services.textnorm import clean_census_name, normalize, state_code

NO_FALLBACK = override_settings(GEOCODER_FALLBACK_ENABLED=False)
LOCMEM = override_settings(CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}})


class GeometryTests(SimpleTestCase):
    def test_decode_polyline_reference_example(self):
        # Example from Google's encoded polyline algorithm documentation (precision 5).
        coords = decode_polyline("_p~iF~ps|U_ulLnnqC_mqNvxq`@", precision=5)
        np.testing.assert_allclose(coords, [[-120.2, 38.5], [-120.95, 40.7], [-126.453, 43.252]])

    def test_resample_bounds_spacing_and_preserves_length(self):
        line = np.array([[-100.0, 40.0], [-99.0, 40.0], [-99.0, 40.001], [-99.0, 40.002], [-98.0, 41.0]])
        pts, cum = resample_polyline(line, max_step_mi=1.0)
        seg = haversine_miles(line[:-1, 1], line[:-1, 0], line[1:, 1], line[1:, 0]).sum()
        self.assertLessEqual(np.diff(cum).max(), 1.0 + 1e-9)
        self.assertAlmostEqual(cum[-1], seg, delta=0.5)
        np.testing.assert_allclose(pts[0], line[0])
        np.testing.assert_allclose(pts[-1], line[-1])


class TextNormTests(SimpleTestCase):
    def test_normalize_handles_spacing_and_abbreviations(self):
        self.assertEqual(normalize("Mc Calla"), normalize("McCalla"))
        self.assertEqual(normalize("Saint Johns"), normalize("St. Johns"))
        self.assertEqual(normalize("Winston Salem"), normalize("Winston-Salem"))
        self.assertEqual(normalize("Bois D Arc"), normalize("Bois d'Arc"))

    def test_clean_census_name_strips_one_suffix(self):
        self.assertEqual(clean_census_name("Oklahoma City city"), "Oklahoma City")
        self.assertEqual(clean_census_name("Abanda CDP"), "Abanda")

    def test_state_code(self):
        self.assertEqual(state_code("tx"), "TX")
        self.assertEqual(state_code("New Mexico"), "NM")
        self.assertIsNone(state_code("ON"))


@NO_FALLBACK
@LOCMEM
class GeocodingTests(SimpleTestCase):
    def test_lat_lon(self):
        loc = resolve_location("39.7392, -104.9903")
        self.assertEqual((loc.source, loc.lat, loc.lon), ("coordinates", 39.7392, -104.9903))

    def test_zip_code(self):
        loc = resolve_location("10001")
        self.assertEqual(loc.source, "zip")
        self.assertAlmostEqual(loc.lat, 40.75, delta=0.05)

    def test_city_state_variants(self):
        for text in ("Chicago, IL", "chicago, illinois", "Chicago, IL, USA", "Chicago, IL 60601"):
            loc = resolve_location(text)
            self.assertEqual(loc.source, "gazetteer", text)
            self.assertAlmostEqual(loc.lat, 41.84, delta=0.1)

    def test_outside_usa_rejected(self):
        with self.assertRaises(LocationError):
            resolve_location("51.5072, -0.1276")  # London

    def test_non_us_region_code_rejected(self):
        with self.assertRaisesMessage(LocationError, "not a US state"):
            resolve_location("Toronto, ON")

    def test_unknown_place_without_fallback(self):
        with self.assertRaises(LocationError):
            resolve_location("Atlantis, ZZ")

    @override_settings(GEOCODER_FALLBACK_ENABLED=True)
    def test_nominatim_fallback_is_cached(self):
        reply = mock.Mock(status_code=200, json=lambda: [{"lat": "38.8977", "lon": "-77.0365", "display_name": "White House"}])
        reply.raise_for_status = lambda: None
        with mock.patch("planner.services.geocoding.session.get", return_value=reply) as get:
            stats = {}
            first = resolve_location("1600 Pennsylvania Ave NW, Washington", stats)
            second = resolve_location("1600 Pennsylvania Ave NW, Washington", stats)
        self.assertEqual(get.call_count, 1)
        self.assertEqual(stats["geocoding_calls"], 1)
        self.assertEqual(first, second)
        self.assertEqual(first.source, "nominatim")
