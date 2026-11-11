"""The restaurant list: storing search results, what each person thinks, filtering, picking."""
import json
import random
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime

from . import config, db, geo, providers

COLUMNS = ("provider", "ext_id", "name", "lat", "lon", "kind", "cuisines", "price", "rating",
           "rating_count", "address", "website", "phone", "hours_text", "periods_json",
           "takeaway", "delivery", "maps_url", "fetched_at")
STATUSES = ("fav", "want", "no")
DEFAULT_RADIUS_KM = 8.0


# ------------------------------------------------------------------ settings
def home(conn) -> dict | None:
    lat, lon = db.get_setting(conn, "home_lat"), db.get_setting(conn, "home_lon")
    if lat is None or lon is None:
        return None
    return {"lat": float(lat), "lon": float(lon), "label": db.get_setting(conn, "home_label", "")}


def units(conn) -> str:
    return db.get_setting(conn, "units", "mi")


def radius_km(conn) -> float:
    return float(db.get_setting(conn, "radius_km", DEFAULT_RADIUS_KM))


# ------------------------------------------------------------------ storing
def upsert(conn, rows: list[dict]) -> None:
    updates = ", ".join(f"{c}=excluded.{c}" for c in COLUMNS[2:])
    conn.executemany(
        f"INSERT INTO places({', '.join(COLUMNS)}) VALUES ({', '.join(':' + c for c in COLUMNS)}) "
        f"ON CONFLICT(provider, ext_id) DO UPDATE SET {updates}", rows)
    conn.commit()


def refresh(conn) -> int:
    """Search around home and store the results. Returns how many places came back."""
    h = home(conn)
    if not h:
        raise RuntimeError("Set your location first.")
    rows = providers.fetch(h["lat"], h["lon"], int(radius_km(conn) * 1000))
    upsert(conn, rows)
    db.set_setting(conn, "fetched_at", db.now())
    db.set_setting(conn, "fetched_count", len(rows))
    from . import takeout
    takeout.rematch(conn)
    return len(rows)


def add_manual(conn, name: str, cuisines: str = "", address: str = "",
               lat: float | None = None, lon: float | None = None) -> int:
    from urllib.parse import quote_plus
    cur = conn.execute(
        "INSERT INTO places(provider, ext_id, name, lat, lon, cuisines, address, maps_url, fetched_at) "
        "VALUES ('manual', ?, ?, ?, ?, ?, ?, ?, ?)",
        (uuid.uuid4().hex, name.strip(), lat, lon,
         ",".join(c.strip().lower() for c in cuisines.split(",") if c.strip()), address.strip(),
         "https://www.google.com/maps/search/?api=1&query=" + quote_plus(f"{name} {address}".strip()),
         db.now()))
    conn.commit()
    return cur.lastrowid


# ------------------------------------------------------------------ open now
def is_open(periods_json: str | None, when: datetime | None = None) -> bool | None:
    """True / False from Google's opening periods; None when we don't know."""
    if not periods_json:
        return None
    when = when or datetime.now()
    week = 7 * 1440
    now_m = ((when.weekday() + 1) % 7) * 1440 + when.hour * 60 + when.minute  # Google: Sunday = 0
    try:
        for p in json.loads(periods_json):
            o = p["open"]
            if "close" not in p:
                return True                                   # open 24 hours
            c = p["close"]
            start = o["day"] * 1440 + o.get("hour", 0) * 60 + o.get("minute", 0)
            end = c["day"] * 1440 + c.get("hour", 0) * 60 + c.get("minute", 0)
            if end <= start:
                end += week
            if start <= now_m < end or start <= now_m + week < end:
                return True
        return False
    except (ValueError, KeyError, TypeError):
        return None


# ------------------------------------------------------------------ loading
def _weight(p: dict) -> float:
    """How strongly the spinner (and the default order) favours a place."""
    w = 1.0 + 1.0 * p["fav_count"] + 0.6 * p["want_count"]
    if p["saved_by"]:
        w *= 1.3
    if p["rating"]:
        w *= min(1.6, max(0.4, (p["rating"] - 2.5) / 1.5))
    d = p["days_since"]
    if d is not None:
        w *= 0.05 if d <= 2 else 0.3 if d <= 6 else 0.6 if d <= 13 else 1.0
    return w


def load(conn, me_id: int, only_id: int | None = None) -> list[dict]:
    """Every place we know within the search radius, decorated for display."""
    h, unit, radius = home(conn), units(conn), radius_km(conn)
    names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM users")}
    sql, args = "SELECT * FROM places WHERE provider IN (?, 'manual')", [config.PROVIDER]
    if only_id is not None:
        sql, args = sql + " AND id=?", args + [only_id]

    marks: dict[int, dict[int, str]] = {}
    for r in conn.execute("SELECT * FROM marks"):
        marks.setdefault(r["place_id"], {})[r["user_id"]] = r["status"]
    visits = {r["place_id"]: (r["n"], r["last"]) for r in conn.execute(
        "SELECT place_id, COUNT(*) AS n, MAX(day) AS last FROM visits GROUP BY place_id")}
    imports: dict[int, list] = {}
    for r in conn.execute("SELECT * FROM import_items WHERE place_id IS NOT NULL"):
        imports.setdefault(r["place_id"], []).append(r)

    today, now, out = date.today(), datetime.now(), []
    for row in conn.execute(sql, args):
        p = dict(row)
        manual = p["provider"] == "manual"
        p["dist_km"] = (geo.haversine_km(h["lat"], h["lon"], p["lat"], p["lon"])
                        if h and p["lat"] is not None else None)
        if only_id is None and not manual and (p["dist_km"] is None or p["dist_km"] > radius * 1.05):
            continue
        p["dist_text"] = geo.fmt_dist(p["dist_km"], unit)
        p["cuisine_list"] = [c for c in p["cuisines"].split(",") if c]
        p["open"] = is_open(p["periods_json"], now)

        m = marks.get(p["id"], {})
        p["mine"] = m.get(me_id)
        p["fans"] = sorted(names[u] for u, s in m.items() if s == "fav" and u != me_id and u in names)
        p["wanters"] = sorted(names[u] for u, s in m.items() if s == "want" and u != me_id and u in names)
        p["fav_count"] = sum(1 for s in m.values() if s == "fav")
        p["want_count"] = sum(1 for s in m.values() if s == "want")
        p["hidden"] = "no" in m.values()
        p["hidden_by"] = sorted(names[u] for u, s in m.items() if s == "no" and u in names)
        liked = sum(1 for s in m.values() if s in ("fav", "want"))
        p["both"] = len(names) >= 2 and liked >= len(names)

        p["visits"], p["last_day"] = visits.get(p["id"], (0, None))
        p["days_since"] = (today - date.fromisoformat(p["last_day"])).days if p["last_day"] else None
        p["ate_today"] = p["days_since"] == 0
        items = imports.get(p["id"], [])
        p["saved_by"] = sorted({names[i["user_id"]] for i in items if i["kind"] == "saved"})
        p["reviews"] = sorted({(names[i["user_id"]], i["rating"]) for i in items
                               if i["kind"] == "review" and i["rating"]})
        p["been"] = p["visits"] > 0 or bool(p["reviews"])
        p["weight"] = _weight(p)
        out.append(p)
    return out


def one(conn, me_id: int, place_id: int) -> dict | None:
    found = load(conn, me_id, only_id=place_id)
    return found[0] if found else None


# ------------------------------------------------------------------ filtering
@dataclass
class Filters:
    cuisines: list[str] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)
    prices: list[int] = field(default_factory=list)
    dist: float | None = None          # in the user's units
    open_now: bool = False
    takeout: bool = False
    show: str = "all"                  # all | new | been | liked | hidden
    skip_days: int = 0                 # leave out places eaten at within this many days
    q: str = ""
    sort: str = "best"                 # best | near | rating

    @classmethod
    def from_query(cls, q) -> "Filters":
        def num(name, cast, default):
            try:
                return cast(q.get(name)) if q.get(name) else default
            except ValueError:
                return default
        return cls(
            cuisines=[c for c in q.getlist("cuisine") if c],
            kinds=[k for k in q.getlist("kind") if k],
            prices=[int(x) for x in q.getlist("price") if x in ("1", "2", "3", "4")],
            dist=num("dist", float, None), open_now=q.get("open") == "1",
            takeout=q.get("takeout") == "1",
            show=q.get("show") if q.get("show") in ("new", "been", "liked", "hidden") else "all",
            skip_days=num("skip", int, 0), q=(q.get("q") or "").strip()[:80],
            sort=q.get("sort") if q.get("sort") in ("near", "rating") else "best")

    @property
    def active(self) -> bool:
        return bool(self.cuisines or self.kinds or self.prices or self.dist or self.open_now
                    or self.takeout or self.show != "all" or self.skip_days or self.q)


def apply(items: list[dict], f: Filters, unit: str = "mi") -> list[dict]:
    max_km = f.dist * (geo.KM_PER_MILE if unit == "mi" else 1) if f.dist else None
    needle = f.q.lower()
    out = []
    for p in items:
        if (f.show == "hidden") != p["hidden"]:
            continue
        if f.cuisines and not set(f.cuisines) & set(p["cuisine_list"]):
            continue
        if f.kinds and p["kind"] not in f.kinds:
            continue
        if f.prices and p["price"] not in f.prices:
            continue
        if max_km is not None and (p["dist_km"] is None or p["dist_km"] > max_km):
            continue
        if f.open_now and p["open"] is False:
            continue
        if f.takeout and p["takeaway"] == 0:
            continue
        if f.show == "new" and p["been"]:
            continue
        if f.show == "been" and not p["been"]:
            continue
        if f.show == "liked" and not (p["fav_count"] or p["want_count"] or p["saved_by"]):
            continue
        if f.skip_days and p["days_since"] is not None and p["days_since"] < f.skip_days:
            continue
        if needle and needle not in p["name"].lower():
            continue
        out.append(p)

    far = 1e9
    if f.sort == "near":
        out.sort(key=lambda p: p["dist_km"] if p["dist_km"] is not None else far)
    elif f.sort == "rating":
        out.sort(key=lambda p: (-(p["rating"] or 0), -(p["rating_count"] or 0)))
    else:
        out.sort(key=lambda p: -p["weight"] / (1 + (p["dist_km"] if p["dist_km"] is not None else 5) / 3))
    return out


def facets(items: list[dict]) -> dict:
    """What the filter bar should offer, based on what we actually have."""
    visible = [p for p in items if not p["hidden"]]
    counts: dict[str, int] = {}
    for p in visible:
        for c in p["cuisine_list"]:
            counts[c] = counts.get(c, 0) + 1
    kinds = sorted({p["kind"] for p in visible})
    return {
        "cuisines": [c for c, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:18]],
        "kinds": kinds,
        "has_price": any(p["price"] for p in visible),
        "has_open": any(p["open"] is not None for p in visible),
        "has_rating": any(p["rating"] for p in visible),
    }


def spin(pool: list[dict], exclude: set[int]) -> dict | None:
    choices = [p for p in pool if p["id"] not in exclude] or pool
    if not choices:
        return None
    return random.choices(choices, weights=[p["weight"] for p in choices], k=1)[0]


# ------------------------------------------------------------------ marks and visits
def set_mark(conn, user_id: int, place_id: int, status: str) -> None:
    """Set a mark; setting the same one again clears it."""
    cur = conn.execute("SELECT status FROM marks WHERE user_id=? AND place_id=?",
                       (user_id, place_id)).fetchone()
    if cur and cur["status"] == status:
        conn.execute("DELETE FROM marks WHERE user_id=? AND place_id=?", (user_id, place_id))
    else:
        conn.execute("INSERT INTO marks(user_id, place_id, status, updated_at) VALUES (?,?,?,?) "
                     "ON CONFLICT(user_id, place_id) DO UPDATE SET status=excluded.status, "
                     "updated_at=excluded.updated_at", (user_id, place_id, status, db.now()))
    conn.commit()


def toggle_visit_today(conn, user_id: int, place_id: int) -> None:
    today = date.today().isoformat()
    cur = conn.execute("DELETE FROM visits WHERE place_id=? AND day=?", (place_id, today))
    if cur.rowcount == 0:
        conn.execute("INSERT INTO visits(place_id, user_id, day, source) VALUES (?,?,?,'app')",
                     (place_id, user_id, today))
    conn.commit()


def history(conn, limit: int = 40) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT v.id, v.day, v.source, p.name, p.id AS place_id, u.name AS who FROM visits v "
        "JOIN places p ON p.id = v.place_id LEFT JOIN users u ON u.id = v.user_id "
        "WHERE p.provider IN (?, 'manual') ORDER BY v.day DESC, v.id DESC LIMIT ?",
        (config.PROVIDER, limit))]
