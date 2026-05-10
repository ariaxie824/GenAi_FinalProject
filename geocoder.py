"""
Nominatim geocoding with persistent JSON cache.
"""

import json
import time
from pathlib import Path

from geopy.geocoders import Nominatim
from geopy.exc import GeocoderTimedOut, GeocoderServiceError

CACHE_PATH = Path(__file__).parent / "data" / "geo_cache.json"
_geolocator = Nominatim(user_agent="memoir-map/1.0")


def _load_cache() -> dict:
    if CACHE_PATH.exists():
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_cache(cache: dict) -> None:
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def geocode(location_name: str) -> tuple[float, float] | None:
    """
    Return (lat, lon) for a location name, using cache to avoid repeated API calls.
    Returns None if geocoding fails.
    """
    cache = _load_cache()
    key = location_name.strip().lower()

    if key in cache:
        entry = cache[key]
        if entry is None:
            return None
        return entry["lat"], entry["lon"]

    try:
        time.sleep(1)  # Nominatim rate limit: 1 request/second
        location = _geolocator.geocode(location_name, timeout=10)
    except (GeocoderTimedOut, GeocoderServiceError):
        return None

    if location is None:
        cache[key] = None
        _save_cache(cache)
        return None

    cache[key] = {"lat": location.latitude, "lon": location.longitude}
    _save_cache(cache)
    return location.latitude, location.longitude


def coords_from_exif_or_geocode(
    exif_data: dict | None, location_name: str
) -> tuple[float, float] | None:
    """
    Prefer EXIF GPS coordinates; fall back to geocoding the location name.
    """
    if exif_data and exif_data.get("gps"):
        g = exif_data["gps"]
        return g["lat"], g["lon"]
    return geocode(location_name)
