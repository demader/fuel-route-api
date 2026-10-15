"""Shared HTTP session: connection keep-alive for external APIs and a polite User-Agent
(required by the OSM Nominatim usage policy)."""
import requests
from django.conf import settings

session = requests.Session()
session.headers["User-Agent"] = settings.EXTERNAL_API_USER_AGENT
