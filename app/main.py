"""FastAPI app: pages and the small JSON API behind the buttons."""
import secrets
import time
import zipfile
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.sessions import SessionMiddleware

from . import ask, config, db, geo, places, takeout, users
from .web import LoginRequired, card_html, page_user, templates

HERE = Path(__file__).resolve().parent
PAGE_SIZE = 60
MIN_PASSWORD = 6

app = FastAPI(title="Where should we eat?", docs_url=None, redoc_url=None)


def _session_secret() -> str:
    if config.SESSION_SECRET:
        return config.SESSION_SECRET
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    f = config.DATA_DIR / "session_secret"
    if not f.exists():
        f.write_text(secrets.token_urlsafe(48))
    return f.read_text().strip()


app.add_middleware(SessionMiddleware, secret_key=_session_secret(), session_cookie="eat_session",
                   max_age=60 * 60 * 24 * 90, same_site="lax")
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")


@app.exception_handler(LoginRequired)
def _to_login(request: Request, exc: LoginRequired):
    return RedirectResponse("/login", status_code=303)


def page(request: Request, name: str, me: users.User, active: str, status_code: int = 200, **ctx):
    return templates.TemplateResponse(request, name, {"me": me, "active": active, **ctx},
                                      status_code=status_code)


# --------------------------------------------------------------------------- sign in
@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, conn=Depends(db.dep)):
    if users.current(request, conn):
        return RedirectResponse("/", status_code=303)
    if users.count(conn) == 0:
        return RedirectResponse("/setup", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"mode": "login", "error": None})


@app.post("/login", response_class=HTMLResponse)
def login(request: Request, name: str = Form(...), password: str = Form(...), conn=Depends(db.dep)):
    user = users.check(conn, name, password)
    if not user:
        time.sleep(1.0)                       # slow down guessing
        return templates.TemplateResponse(request, "login.html", {
            "mode": "login", "error": "That name and password didn't match.", "name": name},
            status_code=401)
    request.session.clear()
    request.session["uid"] = user.id
    return RedirectResponse("/", status_code=303)


@app.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request, conn=Depends(db.dep)):
    if users.count(conn) > 0:
        return RedirectResponse("/login", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"mode": "setup", "error": None})


@app.post("/setup", response_class=HTMLResponse)
def setup(request: Request, name: str = Form(...), password: str = Form(...), conn=Depends(db.dep)):
    if users.count(conn) > 0:
        return RedirectResponse("/login", status_code=303)
    if not name.strip() or len(password) < MIN_PASSWORD:
        return templates.TemplateResponse(request, "login.html", {
            "mode": "setup", "name": name,
            "error": f"Pick a name and a password of at least {MIN_PASSWORD} characters."},
            status_code=400)
    request.session.clear()
    request.session["uid"] = users.create(conn, name, password)
    return RedirectResponse("/settings", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# --------------------------------------------------------------------------- tonight
@app.get("/", response_class=HTMLResponse)
def tonight(request: Request, me: users.User = Depends(page_user), conn=Depends(db.dep)):
    if not places.home(conn):
        return RedirectResponse("/settings", status_code=303)
    f = places.Filters.from_query(request.query_params)
    unit = places.units(conn)
    items = places.load(conn, me.id)
    shown = places.apply(items, f, unit)
    return page(request, "tonight.html", me, "tonight", f=f, facets=places.facets(items),
                cards=shown[:PAGE_SIZE], total=len(shown), known=len(items), unit=unit,
                home=places.home(conn), query=str(request.query_params),
                dist_choices=[1, 2, 5, 10] if unit == "mi" else [1, 3, 8, 15])


@app.get("/api/spin")
def api_spin(request: Request, me: users.User = Depends(users.require), conn=Depends(db.dep)):
    f = places.Filters.from_query(request.query_params)
    pool = places.apply(places.load(conn, me.id), f, places.units(conn))
    skip = {int(x) for x in request.query_params.getlist("not") if x.isdigit()}
    pick = places.spin(pool, skip)
    if not pick:
        return {"count": 0}
    return {"count": len(pool), "id": pick["id"], "name": pick["name"], "html": card_html(pick, me)}


class MarkRequest(BaseModel):
    place_id: int
    status: str = Field(pattern="^(fav|want|no)$")


class VisitRequest(BaseModel):
    place_id: int


def _card_or_404(conn, me: users.User, place_id: int) -> dict:
    p = places.one(conn, me.id, place_id)
    if not p:
        raise HTTPException(status_code=404, detail="No such place")
    return {"html": card_html(p, me)}


@app.post("/api/mark")
def api_mark(req: MarkRequest, me: users.User = Depends(users.require), conn=Depends(db.dep)):
    if not places.one(conn, me.id, req.place_id):
        raise HTTPException(status_code=404, detail="No such place")
    places.set_mark(conn, me.id, req.place_id, req.status)
    return _card_or_404(conn, me, req.place_id)


@app.post("/api/visit")
def api_visit(req: VisitRequest, me: users.User = Depends(users.require), conn=Depends(db.dep)):
    if not places.one(conn, me.id, req.place_id):
        raise HTTPException(status_code=404, detail="No such place")
    places.toggle_visit_today(conn, me.id, req.place_id)
    return _card_or_404(conn, me, req.place_id)


@app.get("/api/card/{place_id}")
def api_card(place_id: int, me: users.User = Depends(users.require), conn=Depends(db.dep)):
    return _card_or_404(conn, me, place_id)


# --------------------------------------------------------------------------- ask
@app.get("/ask", response_class=HTMLResponse)
def ask_page(request: Request, me: users.User = Depends(page_user)):
    return page(request, "ask.html", me, "ask", model=config.CHAT_MODEL)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=600)


@app.post("/api/ask")
def api_ask(req: AskRequest, me: users.User = Depends(users.require)):
    conn = db.connect()
    try:
        items = places.load(conn, me.id)
        people = [r["name"] for r in conn.execute("SELECT name FROM users ORDER BY id")]
    finally:
        conn.close()
    return StreamingResponse(ask.answer_stream(items, req.question.strip(), me.name, people),
                             media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --------------------------------------------------------------------------- our places
@app.get("/places", response_class=HTMLResponse)
def our_places(request: Request, me: users.User = Depends(page_user), conn=Depends(db.dep)):
    items = places.load(conn, me.id)
    by_name = sorted(items, key=lambda p: p["name"].lower())
    return page(request, "places.html", me, "places",
                favs=[p for p in by_name if p["fav_count"] and not p["hidden"]],
                wants=[p for p in by_name if p["want_count"] and not p["fav_count"] and not p["hidden"]],
                saved=[p for p in by_name if p["saved_by"] and not p["fav_count"]
                       and not p["want_count"] and not p["hidden"]],
                hidden=[p for p in by_name if p["hidden"]],
                history=places.history(conn))


@app.post("/places/add")
def add_place(name: str = Form(...), cuisines: str = Form(""), address: str = Form(""),
              me: users.User = Depends(page_user), conn=Depends(db.dep)):
    if name.strip():
        pid = places.add_manual(conn, name[:120], cuisines[:120], address[:200])
        places.set_mark(conn, me.id, pid, "want")
    return RedirectResponse("/places", status_code=303)


@app.post("/visits/{visit_id}/delete")
def delete_visit(visit_id: int, me: users.User = Depends(page_user), conn=Depends(db.dep)):
    conn.execute("DELETE FROM visits WHERE id=?", (visit_id,))
    conn.commit()
    return RedirectResponse("/places#history", status_code=303)


# --------------------------------------------------------------------------- google import
def _import_page(request, me, conn, result=None, error=None, status_code=200):
    return page(request, "import.html", me, "import", status_code=status_code,
                summary=takeout.summary(conn, me.id), result=result, error=error,
                provider=config.PROVIDER)


@app.get("/import", response_class=HTMLResponse)
def import_page(request: Request, me: users.User = Depends(page_user), conn=Depends(db.dep)):
    return _import_page(request, me, conn)


@app.post("/import", response_class=HTMLResponse)
def import_upload(request: Request, files: list[UploadFile] = File(...),
                  me: users.User = Depends(page_user), conn=Depends(db.dep)):
    items, visits = [], []
    try:
        for f in files:
            a, b = takeout.parse_upload(f.filename or "", f.file)
            items += a
            visits += b
    except zipfile.BadZipFile:
        return _import_page(request, me, conn, error="That .zip file could not be opened.",
                            status_code=400)
    if not items and not visits:
        return _import_page(request, me, conn, status_code=400, error=(
            "Nothing recognisable was in that upload. It should be the .zip from Google Takeout, "
            "or Saved Places.json, Reviews.json, a saved-list .csv, or Timeline.json."))
    return _import_page(request, me, conn, result=takeout.store(conn, me.id, items, visits))


@app.post("/import/add/{item_id}")
def import_add(item_id: int, me: users.User = Depends(page_user), conn=Depends(db.dep)):
    it = conn.execute("SELECT * FROM import_items WHERE id=? AND user_id=? AND place_id IS NULL",
                      (item_id, me.id)).fetchone()
    if it:
        pid = places.add_manual(conn, it["name"], "", it["address"], it["lat"], it["lon"])
        conn.execute("UPDATE import_items SET place_id=? WHERE id=?", (pid, item_id))
        conn.commit()
    return RedirectResponse("/import#unmatched", status_code=303)


@app.post("/import/clear")
def import_clear(me: users.User = Depends(page_user), conn=Depends(db.dep)):
    conn.execute("DELETE FROM import_items WHERE user_id=?", (me.id,))
    conn.execute("DELETE FROM visits WHERE user_id=? AND source='google'", (me.id,))
    conn.commit()
    return RedirectResponse("/import", status_code=303)


# --------------------------------------------------------------------------- settings
def _settings_page(request, me, conn, status_code=200, **ctx):
    unit = places.units(conn)
    radius = places.radius_km(conn)
    return page(request, "settings.html", me, "settings", status_code=status_code,
                home=places.home(conn), unit=unit, provider=config.PROVIDER,
                radius=round(radius / geo.KM_PER_MILE if unit == "mi" else radius, 1),
                fetched_at=(db.get_setting(conn, "fetched_at") or "")[:16].replace("T", " "),
                fetched_count=db.get_setting(conn, "fetched_count"),
                people=[r["name"] for r in conn.execute("SELECT name FROM users ORDER BY id")],
                min_password=MIN_PASSWORD, **ctx)


def _refresh_message(conn) -> dict:
    try:
        n = places.refresh(conn)
        return {"ok": f"Found {n} places nearby."} if n else {
            "error": "The search worked but found nothing in that area. Try a bigger distance."}
    except (RuntimeError, httpx.HTTPError) as e:
        return {"error": str(e)}


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, me: users.User = Depends(page_user), conn=Depends(db.dep)):
    return _settings_page(request, me, conn)


@app.post("/settings/location/search", response_class=HTMLResponse)
def location_search(request: Request, address: str = Form(...),
                    me: users.User = Depends(page_user), conn=Depends(db.dep)):
    direct = geo.parse_latlon(address)
    if direct:
        return _set_location(request, me, conn, direct[0], direct[1], "Chosen point")
    try:
        matches = geo.geocode(address.strip()[:200])
    except httpx.HTTPError:
        return _settings_page(request, me, conn, status_code=502,
                              error="The address lookup is not answering right now. Try again in a minute.")
    if not matches:
        return _settings_page(request, me, conn, address=address,
                              error="No place found for that. Try adding the city or ZIP code.")
    return _settings_page(request, me, conn, address=address, matches=matches)


def _set_location(request, me, conn, lat: float, lon: float, label: str):
    db.set_setting(conn, "home_lat", lat)
    db.set_setting(conn, "home_lon", lon)
    db.set_setting(conn, "home_label", label[:200])
    return _settings_page(request, me, conn, **_refresh_message(conn))


@app.post("/settings/location", response_class=HTMLResponse)
def location_set(request: Request, lat: float = Form(...), lon: float = Form(...),
                 label: str = Form("My location"),
                 me: users.User = Depends(page_user), conn=Depends(db.dep)):
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return _settings_page(request, me, conn, status_code=400, error="That location is not valid.")
    return _set_location(request, me, conn, lat, lon, label)


@app.post("/settings/search", response_class=HTMLResponse)
def search_prefs(request: Request, radius: float = Form(...), unit: str = Form("mi"),
                 me: users.User = Depends(page_user), conn=Depends(db.dep)):
    unit = unit if unit in ("mi", "km") else "mi"
    km = radius * (geo.KM_PER_MILE if unit == "mi" else 1)
    db.set_setting(conn, "units", unit)
    db.set_setting(conn, "radius_km", min(40.0, max(0.5, km)))
    if not places.home(conn):
        return _settings_page(request, me, conn)
    return _settings_page(request, me, conn, **_refresh_message(conn))


@app.post("/settings/password", response_class=HTMLResponse)
def change_password(request: Request, current: str = Form(...), new: str = Form(...),
                    me: users.User = Depends(page_user), conn=Depends(db.dep)):
    if not users.check(conn, me.name, current):
        time.sleep(1.0)
        return _settings_page(request, me, conn, status_code=400,
                              error="Your current password was not right.")
    if len(new) < MIN_PASSWORD:
        return _settings_page(request, me, conn, status_code=400,
                              error=f"The new password needs at least {MIN_PASSWORD} characters.")
    users.set_password(conn, me.id, new)
    return _settings_page(request, me, conn, ok="Password changed.")


@app.post("/settings/person", response_class=HTMLResponse)
def add_person(request: Request, name: str = Form(...), password: str = Form(...),
               me: users.User = Depends(page_user), conn=Depends(db.dep)):
    if not name.strip() or len(password) < MIN_PASSWORD:
        return _settings_page(request, me, conn, status_code=400, error=(
            f"The new login needs a name and a password of at least {MIN_PASSWORD} characters."))
    if users.create(conn, name[:40], password) is None:
        return _settings_page(request, me, conn, status_code=400,
                              error="Someone already has that name.")
    return _settings_page(request, me, conn, ok=f"{name.strip()} can now sign in.")


# --------------------------------------------------------------------------- misc
FAVICON = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><circle cx="16" cy="16" r="14" '
           'fill="#b3401f"/><circle cx="16" cy="16" r="8" fill="none" stroke="#fff" stroke-width="2"/></svg>')


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    return Response(FAVICON, media_type="image/svg+xml", headers={"Cache-Control": "max-age=86400"})


@app.get("/health")
def health():
    return {"ok": True}
