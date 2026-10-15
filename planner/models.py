from django.db import models


class FuelStation(models.Model):
    """One truck stop from the OPIS price file, geocoded once at import time."""

    class GeocodeSource(models.TextChoices):
        GAZETTEER = "gazetteer", "Census gazetteer (city centroid)"
        OVERRIDE = "override", "Manual / cached override"
        NOMINATIM = "nominatim", "Nominatim (OpenStreetMap)"

    opis_id = models.PositiveIntegerField(unique=True)
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=255)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2, db_index=True)
    rack_id = models.PositiveIntegerField(null=True, blank=True)
    retail_price = models.DecimalField(max_digits=10, decimal_places=6, help_text="USD per gallon")
    price_entries = models.PositiveSmallIntegerField(default=1, help_text="Rows merged for this OPIS ID")
    latitude = models.FloatField()
    longitude = models.FloatField()
    geocode_source = models.CharField(max_length=20, choices=GeocodeSource.choices)

    class Meta:
        ordering = ["state", "city", "name"]

    def __str__(self):
        return f"{self.name} ({self.city}, {self.state}) ${self.retail_price}"
