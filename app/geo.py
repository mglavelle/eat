"""Distances and address lookup."""
import math
import re

import httpx

from . import config

KM_PER_MILE = 1.609344


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 6371.0088 * 2 * math.asin(math.sqrt(a))


def fmt_dist(km: float | None, units: str) -> str:
    if km is None:
        return ""
    v = km / KM_PER_MILE if units == "mi" else km
    return f"{v:.1f} {units}" if v < 10 else f"{v:.0f} {units}"


def parse_latlon(text: str) -> tuple[float, float] | None:
    """'43.07, -89.38' -> (43.07, -89.38)."""
    m = re.fullmatch(r"\s*(-?\d{1,2}(?:\.\d+)?)\s*[, ]\s*(-?\d{1,3}(?:\.\d+)?)\s*", text)
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    return (lat, lon) if -90 <= lat <= 90 and -180 <= lon <= 180 else None


def geocode(query: str) -> list[dict]:
    """Look an address up with OpenStreetMap's Nominatim. Returns [{label, lat, lon}]."""
    r = httpx.get("https://nominatim.openstreetmap.org/search",
                  params={"q": query, "format": "jsonv2", "limit": 5},
                  headers={"User-Agent": config.USER_AGENT}, timeout=15)
    r.raise_for_status()
    return [{"label": x["display_name"], "lat": float(x["lat"]), "lon": float(x["lon"])}
            for x in r.json()]
