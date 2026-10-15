from rest_framework import serializers


class RouteRequestSerializer(serializers.Serializer):
    start = serializers.CharField(
        max_length=200, help_text="'City, ST', ZIP code, 'lat,lon' or a US address")
    finish = serializers.CharField(max_length=200)
    start_with_full_tank = serializers.BooleanField(
        default=False,
        help_text="False (default): tank starts empty, so the total cost covers the whole trip.")
    corridor_miles = serializers.FloatField(
        required=False, min_value=1, max_value=50,
        help_text="Max distance a station may be from the route. Default from settings.")
    stop_penalty = serializers.FloatField(
        required=False, min_value=0, max_value=200,
        help_text="$ a stop must save to be worth making (fewer, larger fill-ups). 0 = pure cheapest.")
    include_geometry = serializers.BooleanField(default=True)
