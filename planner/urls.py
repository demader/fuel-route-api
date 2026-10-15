from django.urls import path

from . import views

urlpatterns = [
    path("route/", views.RouteView.as_view(), name="route"),
    path("route/map/", views.RouteMapView.as_view(), name="route-map"),
    path("health/", views.HealthView.as_view(), name="health"),
]
