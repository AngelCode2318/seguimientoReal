"""Revocable, opaque sessions. Socket handlers re-check SQLite on every event."""
import functools
import hashlib
import re
import secrets
import sqlite3
import time
from collections import defaultdict, deque
from threading import Lock

from flask import Blueprint, current_app, g, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db, public_user

bp = Blueprint("auth", __name__)
COOKIE = "occ_sid"


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def current_identity():
    token = request.cookies.get(COOKIE, "")
    if not token or len(token) > 128:
        return None
    return get_db().execute(
        "SELECT u.*, s.token_hash, s.expires_at FROM sessions s "
        "JOIN users u ON u.id = s.user_id WHERE s.token_hash = ? AND s.expires_at > ?",
        (token_hash(token), time.time()),
    ).fetchone()


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def require_login(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return jsonify(error="Tu sesión terminó. Vuelve a iniciar sesión."), 401
        return view(*args, **kwargs)
    return wrapped


def json_body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def profile_fields(data):
    def text(key, maximum, required=False):
        value = data.get(key, "")
        if not isinstance(value, str) or len(value.strip()) > maximum:
            raise ValueError(f"Revisa el campo {key}.")
        value = value.strip()
        if required and not value:
            raise ValueError("Completa tu alias y la identificación del auto.")
        return value

    alias = text("alias", 40, True)
    car = text("car", 80, True)
    color = text("color", 30)
    year = data.get("year")
    if year in (None, ""):
        year = None
    else:
        if isinstance(year, bool) or not re.fullmatch(r"\d{4}", str(year)):
            raise ValueError("Escribe un año de cuatro dígitos.")
        year = int(year)
        if not 1886 <= year <= time.gmtime().tm_year + 1:
            raise ValueError("Revisa el año de tu auto.")
    return dict(alias=alias, car=car, year=year, color=color)


def establish_session(user_id):
    raw_token = secrets.token_urlsafe(32)
    db = get_db()
    with db:
        db.execute("DELETE FROM sessions WHERE expires_at <= ?", (time.time(),))
        db.execute("INSERT INTO sessions VALUES (?, ?, ?)",
                   (token_hash(raw_token), user_id, time.time() + 86400 * 7))
    session.clear()
    user = db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    response = jsonify(user={**public_user(user), "email": user["email"]}, csrf_token=csrf_token())
    response.set_cookie(COOKIE, raw_token, max_age=86400 * 7, httponly=True,
                        secure=current_app.config["SESSION_COOKIE_SECURE"], samesite="Lax")
    return response


class AttemptLimiter:
    """Small bounded per-IP limiter; no external datastore needed for one instance."""
    def __init__(self):
        self.attempts = defaultdict(deque)
        self.lock = Lock()

    def allow(self, key, limit, period):
        now = time.monotonic()
        with self.lock:
            # Periodically retire idle IPs rather than retaining them forever.
            if len(self.attempts) > 1000:
                self.attempts = defaultdict(deque, {
                    k: v for k, v in self.attempts.items() if v and v[-1] > now - 3600
                })
            entries = self.attempts[key]
            while entries and entries[0] <= now - period:
                entries.popleft()
            if len(entries) >= limit:
                return False
            entries.append(now)
            return True


def throttled(action, limit, period):
    if current_app.config["TESTING"]:
        return False
    return not current_app.extensions["auth_limiter"].allow(
        (action, request.remote_addr), limit, period
    )


@bp.post("/api/register")
def register():
    if g.user is not None:
        return jsonify(error="Ya tienes una sesión abierta."), 409
    if throttled("register", 60, 3600):
        return jsonify(error="Demasiados registros. Intenta de nuevo más tarde."), 429
    data = json_body()
    try:
        profile = profile_fields(data)
        email = data.get("email", "")
        password = data.get("password", "")
        if not isinstance(email, str) or len(email) > 254 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email.strip()):
            raise ValueError("Escribe un correo válido.")
        if not isinstance(password, str) or not 10 <= len(password) <= 128:
            raise ValueError("Usa una contraseña de entre 10 y 128 caracteres.")
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    db = get_db()
    try:
        with current_app.extensions["password_slots"]:
            password_hash = generate_password_hash(password, method=current_app.config["PASSWORD_HASH_METHOD"])
        with db:
            cursor = db.execute(
                "INSERT INTO users (email,password_hash,alias,car,year,color,created_at) VALUES (?,?,?,?,?,?,?)",
                (email.strip().lower(), password_hash, profile["alias"], profile["car"],
                 profile["year"], profile["color"], time.time()),
            )
    except sqlite3.IntegrityError:
        return jsonify(error="Ese correo ya está registrado. Inicia sesión."), 409
    response = establish_session(cursor.lastrowid)
    response.status_code = 201
    return response


@bp.post("/api/login")
def login():
    if g.user is not None:
        return jsonify(error="Ya tienes una sesión abierta."), 409
    if throttled("login", 30, 300):
        return jsonify(error="Demasiados intentos. Espera cinco minutos."), 429
    data = json_body()
    email, password = data.get("email"), data.get("password")
    if not isinstance(email, str) or not isinstance(password, str) or len(email) > 254 or len(password) > 128:
        return jsonify(error="Correo o contraseña incorrectos."), 401
    user = get_db().execute("SELECT * FROM users WHERE email = ?", (email.strip().lower(),)).fetchone()
    password_hash = user["password_hash"] if user else current_app.extensions["dummy_password_hash"]
    with current_app.extensions["password_slots"]:
        valid = check_password_hash(password_hash, password)
    if not valid or user is None:
        return jsonify(error="Correo o contraseña incorrectos."), 401
    return establish_session(user["id"])


@bp.post("/api/logout")
@require_login
def logout():
    current_app.extensions["tracker"].logout(g.user["id"], g.user["token_hash"])
    session.clear()
    response = jsonify(ok=True)
    response.delete_cookie(COOKIE, secure=current_app.config["SESSION_COOKIE_SECURE"], samesite="Lax")
    return response


@bp.route("/api/me", methods=["GET", "PATCH"])
@require_login
def me():
    if request.method == "PATCH":
        try:
            profile = profile_fields(json_body())
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        with get_db() as db:
            db.execute("UPDATE users SET alias=?,car=?,year=?,color=? WHERE id=?",
                       (profile["alias"], profile["car"], profile["year"], profile["color"], g.user["id"]))
        current_app.extensions["tracker"].profile_updated(g.user["id"])
    user = get_db().execute("SELECT * FROM users WHERE id=?", (g.user["id"],)).fetchone()
    return jsonify(user={**public_user(user), "email": user["email"]}, csrf_token=csrf_token())

