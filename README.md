# Where should we eat?

A small site for two people who can't decide. It finds the restaurants near you, lets you
filter by mood, remembers what each of you likes and where you've eaten, and will pick one
for you. Same stack as the D&D Companion: FastAPI, SQLite, plain HTML/CSS/JS, optional
Ollama.

## Pages

- **Tonight** — filter by food, kind of place, distance, and "new to us / been before /
  liked". **Pick for us** chooses one at random from whatever is left, leaning toward
  favorites and want-to-tries and away from places you ate at in the last two weeks.
  Each card has **Favorite**, **Want to try**, **We ate here** and **Hide**.
- **Ask** — type what you feel like ("cheap and cozy, not pizza again"); a local Ollama
  model suggests up to three places from your own list.
- **Our places** — favorites, want-to-try, places saved on Google Maps, where you've
  eaten, and a form to add a place by hand.
- **Google import** — bring in each person's saved places, reviews and Timeline.
- **Settings** — location, search distance, logins.

## Run it on this PC

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

Open http://127.0.0.1:8010. The first visit asks you to create a login; add the second
person and set your location under **Settings**.

Settings are optional and go in `.env` (copy `.env.example`).

## Where the restaurants come from

| | OpenStreetMap (default) | Google Places (`GOOGLE_PLACES_API_KEY` set) |
|---|---|---|
| Cost | free, no account | needs a Google Cloud project with billing; light use fits the free allowance |
| Names, cuisine, address, hours text | yes | yes |
| Ratings, price level, "open now" | no | yes |

Results are stored in the database, so the outside service is only contacted when you set
your location or press **Save and search again**. Favorites and history are tied to the
source they were made with: choose before you build up history.

OpenStreetMap's search servers are volunteer-run and sometimes busy; the site tries three
of them and tells you to retry if all are.

## Google Maps history

Google offers no way for an app to read saved places or the Timeline from an account, so
each person exports their own data (the **Google import** page has the steps) and uploads
it while signed in as themselves:

- **Saved places and reviews** (Google Takeout .zip) are matched to nearby restaurants by
  name and position. Unmatched ones are listed so you can add them by hand.
- **Timeline** stops are kept only when they match a known restaurant, as a date on that
  restaurant. The rest of the location history is discarded, never stored. With
  OpenStreetMap the match is by position (within 30 m), so check the result under
  **Our places**.

## What leaves this computer

- The address you type under Settings → OpenStreetMap's Nominatim, once, to find it.
- Your location's coordinates and search distance → OpenStreetMap's Overpass servers, or
  Google Places, when a search runs.
- Nothing else. Ask runs on your own Ollama; imports stay in `data/eat.db`.

## Docker / Unraid

`docker compose up -d` runs it on port 8766. Pushing to `main` on GitHub builds
`ghcr.io/mglavelle/eat:latest`. Step-by-step Unraid, Cloudflare and SWAG instructions for
**eat.gulu.stream** are in [docs/UNRAID.md](docs/UNRAID.md), with the template and SWAG
config in [docs/unraid/](docs/unraid/).

## Layout

| Path | What |
|---|---|
| `app/main.py` | pages and the JSON API behind the buttons |
| `app/places.py` | restaurant list, filters, weights, "Pick for us" |
| `app/providers.py` | nearby search: OpenStreetMap and Google Places |
| `app/takeout.py` | Google export parsing and matching |
| `app/ask.py` | Ask page prompt and streaming from Ollama |
| `app/users.py`, `app/db.py`, `app/config.py`, `app/geo.py` | logins, SQLite schema, settings, distances |
| `app/templates/`, `app/static/` | pages, CSS, scripts (no build step) |

## Not tested yet

The Google Places search and the Timeline import were written from Google's documented
formats but have not been run against a real key or a real export.
