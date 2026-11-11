"""Ask page: a local model (Ollama) picks from our own restaurant list."""
import json
import re
from datetime import datetime
from typing import Iterator

import httpx

from . import config, places

MAX_CANDIDATES = 40

SYSTEM = """You help a couple decide where to eat. You get a numbered list of real restaurants \
near them, what each of them thinks of each place, and when they last ate there.

Rules:
- Recommend up to 3 places, ONLY from the list. Write each as its name followed by its number \
in square brackets, for example: Luigi's [4].
- Give one short reason each, using only the facts in the list (distance, cuisine, price, \
rating, favorites, how recently they ate there).
- Prefer places they have not eaten at in the last week, unless they ask for a usual.
- Do not recommend a place marked "closed now" if they want to eat now.
- If nothing in the list fits the request, say so plainly and offer the closest matches.
- Never invent dishes, menus, prices or opening hours. Keep the whole answer under 120 words."""


def _line(n: int, p: dict, me: str) -> str:
    bits = [", ".join(p["cuisine_list"]) or p["kind"]]
    if p["cuisine_list"] and p["kind"] != "restaurant":
        bits.append(p["kind"])
    if p["dist_text"]:
        bits.append(p["dist_text"])
    if p["price"]:
        bits.append("$" * p["price"])
    if p["rating"]:
        bits.append(f"rated {p['rating']:.1f}")
    if p["open"] is not None:
        bits.append("open now" if p["open"] else "closed now")
    if p["takeaway"] == 1:
        bits.append("takeout")
    if p["mine"] in ("fav", "want"):
        bits.append(f"{me}: {'favorite' if p['mine'] == 'fav' else 'wants to try'}")
    bits += [f"{n_}: favorite" for n_ in p["fans"]] + [f"{n_}: wants to try" for n_ in p["wanters"]]
    if p["saved_by"]:
        bits.append("saved on Google Maps by " + " and ".join(p["saved_by"]))
    if p["visits"]:
        bits.append(f"eaten there {p['visits']}x, last {p['days_since']} days ago")
    else:
        bits.append("never eaten there")
    return f"[{n}] {p['name']} - " + "; ".join(bits)


def candidates(items: list[dict], question: str) -> list[dict]:
    """Places whose name or cuisine is mentioned in the question first, then our best picks."""
    pool = [p for p in items if not p["hidden"]]
    words = set(re.findall(r"[a-z]{3,}", question.lower()))
    words |= {w[:-1] for w in words if w.endswith("s")}

    def mentioned(p: dict) -> bool:
        terms = set(re.findall(r"[a-z]{3,}", (p["name"] + " " + p["cuisines"]).lower()))
        return bool(words & terms)

    ranked = places.apply(pool, places.Filters())
    first = [p for p in ranked if mentioned(p)][:MAX_CANDIDATES * 2 // 3]
    ids = {p["id"] for p in first}
    rest = [p for p in ranked if p["id"] not in ids]
    return (first + rest)[:MAX_CANDIDATES]


def answer_stream(items: list[dict], question: str, me: str, people: list[str]) -> Iterator[str]:
    """NDJSON lines: candidates, then text pieces, then done (or error)."""
    picks = candidates(items, question)
    if not picks:
        yield json.dumps({"type": "error", "message":
                          "There are no restaurants yet. Set your location in Settings first."}) + "\n"
        return
    yield json.dumps({"type": "candidates", "items": [
        {"n": i, "id": p["id"], "name": p["name"]} for i, p in enumerate(picks, 1)]}) + "\n"

    now = datetime.now()
    prompt = (f"It is {now:%A, %I:%M %p}. The couple: {' and '.join(people)}. {me} is asking.\n\n"
              "Restaurants:\n" + "\n".join(_line(i, p, me) for i, p in enumerate(picks, 1))
              + f"\n\nRequest: {question}")
    body = {"model": config.CHAT_MODEL, "stream": True, "think": False,
            "keep_alive": config.KEEP_ALIVE,
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
            "options": {"num_ctx": config.CHAT_CTX, "temperature": 0.4,
                        **({"num_gpu": config.CHAT_NUM_GPU} if config.CHAT_NUM_GPU is not None else {})}}
    try:
        with httpx.Client(base_url=config.OLLAMA_URL, timeout=httpx.Timeout(None, connect=5)) as c:
            with c.stream("POST", "/api/chat", json=body) as r:
                if r.status_code != 200:
                    r.read()
                    try:
                        detail = r.json().get("error", "")
                    except ValueError:
                        detail = ""
                    raise RuntimeError(detail or f"Ollama answered with status {r.status_code}")
                for line in r.iter_lines():
                    if not line:
                        continue
                    msg = json.loads(line)
                    if msg.get("error"):
                        raise RuntimeError(msg["error"])
                    piece = msg.get("message", {}).get("content")
                    if piece:
                        yield json.dumps({"type": "text", "piece": piece}) + "\n"
                    if msg.get("done"):
                        break
        yield json.dumps({"type": "done"}) + "\n"
    except httpx.ConnectError:
        yield json.dumps({"type": "error", "message":
                          f"Could not reach Ollama at {config.OLLAMA_URL}. Is it running?"}) + "\n"
    except (httpx.HTTPError, RuntimeError, ValueError) as e:
        yield json.dumps({"type": "error", "message": f"The model could not answer: {e}"}) + "\n"
