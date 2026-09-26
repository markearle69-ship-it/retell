import math

EARTH_RADIUS_MILES = 3958.8

# Clock-face slices, 45 degrees each, centred on the compass point.
SECTORS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def distance_miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


def bearing_degrees(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lng2 - lng1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def sector_for(bearing: float) -> str:
    return SECTORS[int(((bearing + 22.5) % 360) // 45)]


def offset_point(lat: float, lng: float, north_miles: float, east_miles: float) -> tuple[float, float]:
    """Small-distance offset, accurate enough for sampling points ~1 mile away."""
    dlat = north_miles / 69.0
    dlng = east_miles / (69.0 * math.cos(math.radians(lat)))
    return lat + dlat, lng + dlng
