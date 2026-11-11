"""SQLite storage: schema and connection helper."""
import sqlite3
from datetime import datetime

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    name          TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Restaurants. provider = osm | google (from a nearby search) or manual (added by hand).
CREATE TABLE IF NOT EXISTS places (
    id           INTEGER PRIMARY KEY,
    provider     TEXT NOT NULL,
    ext_id       TEXT NOT NULL,
    name         TEXT NOT NULL,
    lat          REAL,
    lon          REAL,
    kind         TEXT NOT NULL DEFAULT 'restaurant',  -- restaurant | fast food | cafe | bar | dessert
    cuisines     TEXT NOT NULL DEFAULT '',            -- comma separated, lower case
    price        INTEGER,                             -- 1-4, NULL = unknown
    rating       REAL,
    rating_count INTEGER,
    address      TEXT NOT NULL DEFAULT '',
    website      TEXT NOT NULL DEFAULT '',
    phone        TEXT NOT NULL DEFAULT '',
    hours_text   TEXT NOT NULL DEFAULT '',
    periods_json TEXT,                                -- Google opening periods, for "open now"
    takeaway     INTEGER,                             -- 1 / 0 / NULL = unknown
    delivery     INTEGER,
    maps_url     TEXT NOT NULL DEFAULT '',
    fetched_at   TEXT NOT NULL,
    UNIQUE (provider, ext_id)
);

-- What each person thinks of a place: fav | want | no.
CREATE TABLE IF NOT EXISTS marks (
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    place_id   INTEGER NOT NULL REFERENCES places(id) ON DELETE CASCADE,
    status     TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (user_id, place_id)
);

-- Meals. One row per place per day. source = app ("We ate here") | google (imported).
CREATE TABLE IF NOT EXISTS visits (
    id        INTEGER PRIMARY KEY,
    place_id  INTEGER NOT NULL REFERENCES places(id) ON DELETE CASCADE,
    user_id   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    day       TEXT NOT NULL,              -- YYYY-MM-DD
    source    TEXT NOT NULL DEFAULT 'app',
    UNIQUE (place_id, day)
);
CREATE INDEX IF NOT EXISTS visits_day ON visits(day);

-- Saved places and reviews imported from a person's Google export.
-- place_id is filled in when the item is matched to a restaurant we know.
CREATE TABLE IF NOT EXISTS import_items (
    id        INTEGER PRIMARY KEY,
    user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    kind      TEXT NOT NULL,              -- saved | review
    name      TEXT NOT NULL,
    address   TEXT NOT NULL DEFAULT '',
    lat       REAL,
    lon       REAL,
    day       TEXT NOT NULL DEFAULT '',
    rating    INTEGER,
    list_name TEXT NOT NULL DEFAULT '',
    place_id  INTEGER REFERENCES places(id) ON DELETE SET NULL,
    UNIQUE (user_id, kind, name, address)
);
"""

_ready = False


def connect() -> sqlite3.Connection:
    global _ready
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    if not _ready:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        conn.commit()
        _ready = True
    return conn


def dep():
    """FastAPI dependency: a connection that is closed after the request."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def get_setting(conn, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key: str, value) -> None:
    conn.execute("INSERT INTO settings(key, value) VALUES (?, ?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    conn.commit()
