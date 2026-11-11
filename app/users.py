"""Logins: a name and a password per person. Passwords are stored as salted PBKDF2 hashes."""
import hashlib
import hmac
import secrets
from dataclasses import dataclass

from fastapi import HTTPException, Request

from . import db

ITERATIONS = 200_000


@dataclass
class User:
    id: int
    name: str


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt}${digest.hex()}"


def verify(password: str, stored: str) -> bool:
    try:
        _, iters, salt, digest = stored.split("$")
        check = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iters))
        return hmac.compare_digest(check.hex(), digest)
    except (ValueError, TypeError):
        return False


def count(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def create(conn, name: str, password: str) -> int | None:
    """Return the new user's id, or None if the name is taken."""
    name = name.strip()
    if conn.execute("SELECT 1 FROM users WHERE name=?", (name,)).fetchone():
        return None
    cur = conn.execute("INSERT INTO users(name, password_hash, created_at) VALUES (?,?,?)",
                       (name, hash_password(password), db.now()))
    conn.commit()
    return cur.lastrowid


def check(conn, name: str, password: str) -> User | None:
    row = conn.execute("SELECT * FROM users WHERE name=?", (name.strip(),)).fetchone()
    if row and verify(password, row["password_hash"]):
        return User(row["id"], row["name"])
    return None


def set_password(conn, user_id: int, password: str) -> None:
    conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(password), user_id))
    conn.commit()


def current(request: Request, conn=None) -> User | None:
    uid = request.session.get("uid")
    if not isinstance(uid, int):
        return None
    own = conn is None
    conn = conn or db.connect()
    try:
        row = conn.execute("SELECT id, name FROM users WHERE id=?", (uid,)).fetchone()
    finally:
        if own:
            conn.close()
    return User(row["id"], row["name"]) if row else None


def require(request: Request) -> User:
    """For JSON endpoints."""
    u = current(request)
    if not u:
        raise HTTPException(status_code=401, detail="Not signed in")
    return u
