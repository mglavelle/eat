"""Shared bits for HTML pages: templates and the login dependency."""
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from . import users

HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=HERE / "templates")


def _asset_version() -> str:
    """Changes whenever a static file changes, so browsers never use stale CSS/JS."""
    return str(int(max(p.stat().st_mtime for p in (HERE / "static").iterdir())))


templates.env.globals["asset_v"] = _asset_version


class LoginRequired(Exception):
    """Raised by page dependencies; main.py turns it into a redirect to /login."""


def page_user(request: Request) -> users.User:
    u = users.current(request)
    if not u:
        raise LoginRequired()
    return u


def card_html(place: dict, me: users.User) -> str:
    return templates.get_template("_card.html").render(p=place, me=me)
