from urllib.parse import urlencode

from django.shortcuts import render
from django.urls import reverse
from django.views import View
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .serializers import RouteRequestSerializer
from .services.geocoding import LocationError
from .services.optimizer import InfeasibleRoute
from .services.routing import RoutingError
from .services.stations import get_station_index
from .services.trip_planner import plan_trip


def _run_plan(params: dict) -> tuple[dict, int]:
    """Validate params and plan the trip. Returns (body, http_status)."""
    serializer = RouteRequestSerializer(data=params)
    if not serializer.is_valid():
        return {"errors": serializer.errors}, status.HTTP_400_BAD_REQUEST
    data = serializer.validated_data
    try:
        result = plan_trip(
            data["start"], data["finish"],
            start_full=data["start_with_full_tank"],
            corridor_miles=data.get("corridor_miles"),
            stop_penalty=data.get("stop_penalty"),
        )
    except LocationError as exc:
        return {"error": str(exc)}, status.HTTP_400_BAD_REQUEST
    except InfeasibleRoute as exc:
        return {"error": f"{exc} Try a larger corridor_miles."}, status.HTTP_422_UNPROCESSABLE_ENTITY
    except RoutingError as exc:
        return {"error": str(exc)}, status.HTTP_502_BAD_GATEWAY
    if not data["include_geometry"]:
        result["route"].pop("geometry")
    return result, status.HTTP_200_OK


def _map_query(params) -> str:
    keys = ("start", "finish", "start_with_full_tank", "corridor_miles", "stop_penalty")
    return urlencode({k: params[k] for k in keys if params.get(k) not in (None, "")})


class RouteView(APIView):
    """Plan a route with the cheapest fuel stops.

    GET  /api/route/?start=New York, NY&finish=Los Angeles, CA
    POST /api/route/  {"start": "...", "finish": "...", "start_with_full_tank": false}
    """

    def get(self, request):
        return self._respond(request, request.query_params)

    def post(self, request):
        return self._respond(request, request.data)

    def _respond(self, request, params):
        body, code = _run_plan(params)
        if code == status.HTTP_200_OK:
            body["map_url"] = request.build_absolute_uri(f"{reverse('route-map')}?{_map_query(params)}")
        return Response(body, status=code)


class RouteMapView(View):
    """Interactive Leaflet map of the route and fuel stops. Reuses the cached route,
    so opening it after calling /api/route/ makes no extra routing call."""

    def get(self, request):
        body, code = _run_plan(request.GET.dict() | {"include_geometry": True})
        return render(request, "planner/map.html", {"plan": body, "ok": code == 200}, status=code)


class HealthView(APIView):
    def get(self, request):
        return Response({"status": "ok", "stations_loaded": len(get_station_index())})
