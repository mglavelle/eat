"""Nearby restaurant search.

Two sources, same output (a list of dicts shaped like the `places` table):
  * osm    - OpenStreetMap through the Overpass API. Free, no account. No ratings or prices.
  * google - Google Places API (New). Used when GOOGLE_PLACES_API_KEY is set.
"""
import json
from urllib.parse import quote_plus

import httpx

from . import config, db

# ------------------------------------------------------------------ OpenStreetMap
# Public, volunteer-run servers: any one of them can be busy, so try each in turn. The whole
# round stays under 90 s, because Cloudflare gives up on a page after 100 s.
OVERPASS_URLS = ["https://overpass-api.de/api/interpreter",
                 "https://overpass.private.coffee/api/interpreter",
                 "https://overpass.kumi.systems/api/interpreter"]
OSM_KINDS = {"restaurant": "restaurant", "food_court": "restaurant", "fast_food": "fast food",
             "cafe": "cafe", "pub": "bar", "bar": "bar", "biergarten": "bar", "ice_cream": "dessert"}
OSM_LIMIT = 4000


def _yes_no(value: str | None) -> int | None:
    if value in ("yes", "only"):
        return 1
    return 0 if value == "no" else None


def _osm_cuisines(raw: str) -> str:
    out = []
    for part in raw.replace(",", ";").split(";"):
        c = part.strip().lower().replace("_", " ")
        if c and c not in out:
            out.append(c)
    return ",".join(out[:5])


def fetch_osm(lat: float, lon: float, radius_m: int) -> list[dict]:
    query = (f'[out:json][timeout:25];'
             f'nwr["amenity"~"^({"|".join(OSM_KINDS)})$"]["name"](around:{radius_m},{lat},{lon});'
             f'out center tags {OSM_LIMIT};')
    last_error: Exception | None = None
    data = None
    for url in OVERPASS_URLS:
        try:
            r = httpx.post(url, data={"data": query}, timeout=httpx.Timeout(28, connect=6),
                           headers={"User-Agent": config.USER_AGENT})
            r.raise_for_status()
            data = r.json()
            break
        except (httpx.HTTPError, ValueError) as e:
            last_error = e
    if data is None:
        raise RuntimeError(
            "OpenStreetMap's free search servers are all busy right now. Your location is saved; "
            f"press \"Save and search again\" in a few minutes. (Last answer: {last_error})")

    now, rows = db.now(), []
    for el in data.get("elements", []):
        t = el.get("tags", {})
        center = el.get("center", el)
        if "lat" not in center or not t.get("name"):
            continue
        street = " ".join(x for x in (t.get("addr:housenumber"), t.get("addr:street")) if x)
        address = ", ".join(x for x in (street, t.get("addr:city")) if x)
        where = address or f'{center["lat"]:.5f},{center["lon"]:.5f}'
        rows.append({
            "provider": "osm", "ext_id": f'{el["type"]}/{el["id"]}', "name": t["name"],
            "lat": center["lat"], "lon": center["lon"],
            "kind": OSM_KINDS.get(t.get("amenity", ""), "restaurant"),
            "cuisines": _osm_cuisines(t.get("cuisine", "")),
            "price": None, "rating": None, "rating_count": None,
            "address": address,
            "website": t.get("website") or t.get("contact:website") or "",
            "phone": t.get("phone") or t.get("contact:phone") or "",
            "hours_text": t.get("opening_hours", ""), "periods_json": None,
            "takeaway": _yes_no(t.get("takeaway")), "delivery": _yes_no(t.get("delivery")),
            "maps_url": "https://www.google.com/maps/search/?api=1&query="
                        + quote_plus(f'{t["name"]} {where}'),
            "fetched_at": now,
        })
    return rows


# ------------------------------------------------------------------ Google Places
GOOGLE_URL = "https://places.googleapis.com/v1/places:searchNearby"
GOOGLE_FIELDS = ",".join("places." + f for f in (
    "id", "displayName", "location", "primaryType", "types", "priceLevel", "rating",
    "userRatingCount", "formattedAddress", "websiteUri", "nationalPhoneNumber",
    "regularOpeningHours", "googleMapsUri", "takeout", "delivery", "businessStatus"))
# One request returns at most 20 places, so ask once per group to cover the area.
GOOGLE_GROUPS = [
    ["restaurant"], ["cafe", "coffee_shop", "bakery"], ["fast_food_restaurant", "meal_takeaway"],
    ["bar", "pub"], ["pizza_restaurant"], ["hamburger_restaurant"], ["american_restaurant"],
    ["italian_restaurant"], ["mexican_restaurant"], ["chinese_restaurant"],
    ["japanese_restaurant", "sushi_restaurant", "ramen_restaurant"], ["indian_restaurant"],
    ["thai_restaurant"], ["vietnamese_restaurant"], ["korean_restaurant"],
    ["mediterranean_restaurant", "greek_restaurant", "middle_eastern_restaurant"],
    ["seafood_restaurant"], ["steak_house"], ["barbecue_restaurant"], ["sandwich_shop"],
    ["breakfast_restaurant", "brunch_restaurant"], ["vegetarian_restaurant", "vegan_restaurant"],
    ["ice_cream_shop", "dessert_shop"],
]
GOOGLE_PRICE = {"PRICE_LEVEL_INEXPENSIVE": 1, "PRICE_LEVEL_MODERATE": 2,
                "PRICE_LEVEL_EXPENSIVE": 3, "PRICE_LEVEL_VERY_EXPENSIVE": 4}
_GENERIC = {"restaurant", "fast food", "fine dining"}


def _google_kind(primary: str) -> str:
    if any(w in primary for w in ("cafe", "coffee", "bakery", "tea_house")):
        return "cafe"
    if any(w in primary for w in ("bar", "pub")) and "barbecue" not in primary:
        return "bar"
    if any(w in primary for w in ("ice_cream", "dessert", "donut", "confectionery")):
        return "dessert"
    if any(w in primary for w in ("fast_food", "meal_takeaway", "sandwich", "hamburger")):
        return "fast food"
    return "restaurant"


def _google_cuisines(types: list[str]) -> str:
    out = []
    for t in types:
        if t.endswith("_restaurant") or t in ("steak_house", "sandwich_shop"):
            c = t.removesuffix("_restaurant").removesuffix("_shop").replace("_", " ")
            c = {"hamburger": "burger", "steak house": "steak"}.get(c, c)
            if c not in _GENERIC and c not in out:
                out.append(c)
    return ",".join(out[:5])


def fetch_google(lat: float, lon: float, radius_m: int) -> list[dict]:
    headers = {"X-Goog-Api-Key": config.GOOGLE_PLACES_API_KEY, "X-Goog-FieldMask": GOOGLE_FIELDS}
    now, found = db.now(), {}
    with httpx.Client(timeout=30, headers=headers) as c:
        for group in GOOGLE_GROUPS:
            r = c.post(GOOGLE_URL, json={
                "includedTypes": group, "maxResultCount": 20, "rankPreference": "POPULARITY",
                "locationRestriction": {"circle": {
                    "center": {"latitude": lat, "longitude": lon},
                    "radius": min(radius_m, 50000)}}})
            if r.status_code != 200:
                try:
                    msg = r.json()["error"]["message"]
                except (ValueError, KeyError):
                    msg = r.text[:200]
                raise RuntimeError(f"Google Places said: {msg}")
            for p in r.json().get("places", []):
                if p.get("businessStatus", "OPERATIONAL") != "OPERATIONAL":
                    continue
                hours = p.get("regularOpeningHours") or {}
                found[p["id"]] = {
                    "provider": "google", "ext_id": p["id"],
                    "name": p.get("displayName", {}).get("text", "?"),
                    "lat": p["location"]["latitude"], "lon": p["location"]["longitude"],
                    "kind": _google_kind(p.get("primaryType", "")),
                    "cuisines": _google_cuisines(p.get("types", [])),
                    "price": GOOGLE_PRICE.get(p.get("priceLevel", "")),
                    "rating": p.get("rating"), "rating_count": p.get("userRatingCount"),
                    "address": p.get("formattedAddress", ""),
                    "website": p.get("websiteUri", ""), "phone": p.get("nationalPhoneNumber", ""),
                    "hours_text": "; ".join(hours.get("weekdayDescriptions", [])),
                    "periods_json": json.dumps(hours["periods"]) if hours.get("periods") else None,
                    "takeaway": None if "takeout" not in p else int(p["takeout"]),
                    "delivery": None if "delivery" not in p else int(p["delivery"]),
                    "maps_url": p.get("googleMapsUri", ""),
                    "fetched_at": now,
                }
    return list(found.values())


def fetch(lat: float, lon: float, radius_m: int) -> list[dict]:
    return (fetch_google if config.PROVIDER == "google" else fetch_osm)(lat, lon, radius_m)
