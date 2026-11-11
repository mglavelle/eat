"""Import a person's Google Maps history from files they export themselves.

Google has no way for an app like this to read saved places or the Timeline from an account,
so each person exports their own data and uploads it here:

  * Google Takeout "Saved" and "Maps (your places)"  -> Saved Places.json, Reviews.json, list CSVs
  * Google Maps on the phone, "Export Timeline data"  -> Timeline.json (places you actually went)

Saved places and reviews are kept (so they can be matched again after a new search). Timeline
visits are only kept when they match a restaurant we already know; the rest of the location
history is thrown away and never stored.
"""
import csv
import io
import json
import re
import unicodedata
import zipfile
from difflib import SequenceMatcher

from . import config, geo

MAX_MEMBER_BYTES = 300 * 1024 * 1024
_NOISE = {"the", "restaurant", "and", "bar", "grill", "cafe", "kitchen"}


# ------------------------------------------------------------------ reading files
def _norm(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    words = [w for w in re.findall(r"[a-z0-9]+", s)]
    kept = [w for w in words if w not in _NOISE] or words
    return " ".join(kept)


def _day(value) -> str:
    m = re.match(r"\d{4}-\d{2}-\d{2}", str(value or ""))
    return m.group(0) if m else ""


def _coords(text) -> tuple[float, float] | None:
    """'geo:39.1,-83.0' or '39.1°, -83.0°' -> (lat, lon)."""
    nums = re.findall(r"-?\d+(?:\.\d+)?", str(text or ""))
    if len(nums) < 2:
        return None
    lat, lon = float(nums[0]), float(nums[1])
    return (lat, lon) if (lat or lon) and -90 <= lat <= 90 and -180 <= lon <= 180 else None


def _from_geojson(data: dict, filename: str) -> list[dict]:
    items = []
    for f in data.get("features", []):
        props = f.get("properties") or {}
        loc = props.get("location") or {}
        name = loc.get("name") or props.get("Title") or props.get("name") or ""
        if not name:
            continue
        xy = (f.get("geometry") or {}).get("coordinates") or []
        lat, lon = (xy[1], xy[0]) if len(xy) >= 2 and (xy[0] or xy[1]) else (None, None)
        rating = props.get("five_star_rating_published")
        is_review = rating is not None or "review" in filename.lower()
        items.append({"kind": "review" if is_review else "saved", "name": name.strip(),
                      "address": (loc.get("address") or "").strip(), "lat": lat, "lon": lon,
                      "day": _day(props.get("date") or props.get("Published")),
                      "rating": int(rating) if isinstance(rating, (int, float)) else None,
                      "list_name": "Reviews" if is_review else "Saved places"})
    return items


def _from_csv(text: str, filename: str) -> list[dict]:
    list_name = filename.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if line.lower().startswith("title,")), None)
    if start is None:
        return []
    items = []
    for row in csv.DictReader(io.StringIO("\n".join(lines[start:]))):
        name = (row.get("Title") or "").strip()
        if name:
            items.append({"kind": "saved", "name": name, "address": "", "lat": None, "lon": None,
                          "day": "", "rating": None, "list_name": list_name})
    return items


def _visits_from_timeline(data) -> list[dict]:
    """[{ext_id, lat, lon, day, name}] from either Timeline format."""
    out = []
    if isinstance(data, dict) and "timelineObjects" in data:           # older Takeout format
        for obj in data["timelineObjects"]:
            pv = obj.get("placeVisit") or {}
            loc = pv.get("location") or {}
            if "latitudeE7" not in loc:
                continue
            out.append({"ext_id": loc.get("placeId", ""), "name": loc.get("name", ""),
                        "lat": loc["latitudeE7"] / 1e7, "lon": loc["longitudeE7"] / 1e7,
                        "day": _day((pv.get("duration") or {}).get("startTimestamp"))})
        return out
    segments = data.get("semanticSegments", []) if isinstance(data, dict) else data
    for seg in segments if isinstance(segments, list) else []:
        top = ((seg.get("visit") or {}).get("topCandidate") or {}) if isinstance(seg, dict) else {}
        if not top:
            continue
        where = top.get("placeLocation")
        xy = _coords(where.get("latLng") if isinstance(where, dict) else where)
        if not xy:
            continue
        out.append({"ext_id": top.get("placeId") or top.get("placeID") or "", "name": "",
                    "lat": xy[0], "lon": xy[1], "day": _day(seg.get("startTime"))})
    return out


def parse_file(filename: str, raw: bytes) -> tuple[list[dict], list[dict]]:
    """Return (saved/review items, timeline visits) found in one file."""
    lower = filename.lower()
    if lower.endswith(".csv"):
        return _from_csv(raw.decode("utf-8-sig", "replace"), filename), []
    if not lower.endswith(".json"):
        return [], []
    try:
        data = json.loads(raw.decode("utf-8-sig", "replace"))
    except ValueError:
        return [], []
    if isinstance(data, dict) and data.get("type") == "FeatureCollection":
        return _from_geojson(data, filename), []
    return [], _visits_from_timeline(data)


def parse_upload(filename: str, fileobj) -> tuple[list[dict], list[dict]]:
    """One uploaded file: a .zip straight from Takeout, or a single .json / .csv."""
    items, visits = [], []
    if filename.lower().endswith(".zip"):
        with zipfile.ZipFile(fileobj) as z:
            for info in z.infolist():
                if info.is_dir() or info.file_size > MAX_MEMBER_BYTES:
                    continue
                if not info.filename.lower().endswith((".json", ".csv")):
                    continue
                a, b = parse_file(info.filename, z.read(info))
                items += a
                visits += b
    else:
        items, visits = parse_file(filename, fileobj.read(MAX_MEMBER_BYTES))
    return items, visits


# ------------------------------------------------------------------ matching
def _known_places(conn) -> list[dict]:
    rows = conn.execute("SELECT id, provider, ext_id, name, lat, lon FROM places "
                        "WHERE provider IN (?, 'manual')", (config.PROVIDER,))
    return [dict(r) | {"norm": _norm(r["name"])} for r in rows]


def _similar(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if len(a) >= 5 and len(b) >= 5 and (a in b or b in a):
        return 0.9
    return SequenceMatcher(None, a, b).ratio()


def _match_item(item: dict, places: list[dict], by_norm: dict[str, list[dict]]) -> int | None:
    norm = _norm(item["name"])
    if item["lat"] is not None:
        best, best_score = None, 0.0
        for p in places:
            if p["lat"] is None or abs(p["lat"] - item["lat"]) > 0.004:
                continue
            if geo.haversine_km(item["lat"], item["lon"], p["lat"], p["lon"]) > 0.25:
                continue
            score = _similar(norm, p["norm"])
            if score > best_score:
                best, best_score = p, score
        return best["id"] if best and best_score >= 0.62 else None
    same = by_norm.get(norm, [])          # no coordinates (list CSVs): the name must be unique nearby
    return same[0]["id"] if len(same) == 1 else None


def rematch(conn) -> int:
    """Try again to link unmatched saved places / reviews to restaurants we know."""
    places = _known_places(conn)
    by_norm: dict[str, list[dict]] = {}
    for p in places:
        by_norm.setdefault(p["norm"], []).append(p)
    linked = 0
    for row in conn.execute("SELECT * FROM import_items WHERE place_id IS NULL").fetchall():
        pid = _match_item(dict(row), places, by_norm)
        if pid:
            conn.execute("UPDATE import_items SET place_id=? WHERE id=?", (pid, row["id"]))
            if row["kind"] == "review" and row["day"]:
                conn.execute("INSERT OR IGNORE INTO visits(place_id, user_id, day, source) "
                             "VALUES (?,?,?,'google')", (pid, row["user_id"], row["day"]))
            linked += 1
    conn.commit()
    return linked


def _match_visits(conn, user_id: int, visits: list[dict]) -> int:
    places = [p for p in _known_places(conn) if p["lat"] is not None]
    by_ext = {p["ext_id"]: p for p in places if p["provider"] == "google"}
    added = 0
    for v in visits:
        if not v["day"]:
            continue
        place = by_ext.get(v["ext_id"])
        if not place:
            norm, best, best_d = _norm(v["name"]), None, 1.0
            for p in places:
                if abs(p["lat"] - v["lat"]) > 0.002:
                    continue
                d = geo.haversine_km(v["lat"], v["lon"], p["lat"], p["lon"])
                # With a name (older exports) allow 150 m; coordinates alone must be within 30 m.
                ok = d <= 0.15 and _similar(norm, p["norm"]) >= 0.62 if norm else d <= 0.03
                if ok and d < best_d:
                    best, best_d = p, d
            place = best
        if place:
            cur = conn.execute("INSERT OR IGNORE INTO visits(place_id, user_id, day, source) "
                               "VALUES (?,?,?,'google')", (place["id"], user_id, v["day"]))
            added += cur.rowcount
    conn.commit()
    return added


def store(conn, user_id: int, items: list[dict], visits: list[dict]) -> dict:
    """Save what was parsed for this person and link it to known restaurants."""
    new = 0
    for it in items:
        cur = conn.execute(
            "INSERT INTO import_items(user_id, kind, name, address, lat, lon, day, rating, list_name) "
            "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id, kind, name, address) DO NOTHING",
            (user_id, it["kind"], it["name"], it["address"], it["lat"], it["lon"], it["day"],
             it["rating"], it["list_name"]))
        new += cur.rowcount
    conn.commit()
    rematch(conn)
    matched = conn.execute("SELECT COUNT(*) FROM import_items WHERE user_id=? AND place_id IS NOT NULL",
                           (user_id,)).fetchone()[0]
    return {"items": len(items), "new": new, "matched": matched,
            "visits_seen": len(visits), "visits_added": _match_visits(conn, user_id, visits)}


def summary(conn, user_id: int) -> dict:
    row = conn.execute(
        "SELECT COUNT(*) AS total, SUM(place_id IS NOT NULL) AS matched, "
        "SUM(kind='review') AS reviews FROM import_items WHERE user_id=?", (user_id,)).fetchone()
    unmatched = conn.execute(
        "SELECT * FROM import_items WHERE user_id=? AND place_id IS NULL ORDER BY name LIMIT 300",
        (user_id,)).fetchall()
    return {"total": row["total"] or 0, "matched": row["matched"] or 0,
            "reviews": row["reviews"] or 0, "unmatched": [dict(r) for r in unmatched]}
