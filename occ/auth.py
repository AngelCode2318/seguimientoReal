"""Participant entry with revocable sessions, without accounts or passwords."""
import functools
import hashlib
import secrets
import sqlite3
import time
from collections import defaultdict, deque
from threading import Lock

from flask import Blueprint, current_app, g, jsonify, request, session

from .db import get_db, public_user
from .profiles import identity_key, profile_fields

bp = Blueprint("auth", __name__)
COOKIE = "occ_sid"
ACTIVE_MESSAGE = "Ese participante ya está compartiendo ubicación. Si eres otra persona, cambia tu alias."


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


def require_session(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        # A waiting HTTP request cannot use an access revoked by another entrance.
        with current_app.extensions["tracker"].lock:
            g.user = current_identity()
            if g.user is None:
                return jsonify(error="Tu acceso terminó. Vuelve a entrar al mapa."), 401
            return view(*args, **kwargs)
    return wrapped


def json_body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def session_response(user_id, raw_token):
    session.clear()
    user = get_db().execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    response = jsonify(user=public_user(user), csrf_token=csrf_token())
    response.set_cookie(COOKIE, raw_token, max_age=86400 * 7, httponly=True,
                        secure=current_app.config["SESSION_COOKIE_SECURE"], samesite="Lax")
    return response


class AttemptLimiter:
    """Small bounded per-IP limiter for a single service instance."""
    def __init__(self):
        self.attempts = defaultdict(deque)
        self.lock = Lock()

    def allow(self, key, limit, period):
        now = time.monotonic()
        with self.lock:
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


@bp.post("/api/enter")
def enter():
    if not current_app.config["TESTING"] and not current_app.extensions["auth_limiter"].allow(
        ("enter", request.remote_addr), 120, 300
    ):
        return jsonify(error="Demasiados intentos. Espera unos minutos y vuelve a entrar."), 429
    try:
        profile = profile_fields(json_body())
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    key = identity_key(profile)
    tracker = current_app.extensions["tracker"]
    with tracker.lock:
        current = current_identity()
        if current is not None:
            if current["identity_key"] == key:
                # A retry after a lost HTTP response must not revoke its own access.
                return jsonify(user=public_user(current), csrf_token=csrf_token())
            return jsonify(error="Ya entraste al mapa. Sal de la rodada para usar otros datos."), 409
        db = get_db()
        raw_token = secrets.token_urlsafe(32)
        with db:
            db.execute("BEGIN IMMEDIATE")
            user = db.execute("SELECT * FROM users WHERE identity_key=?", (key,)).fetchone()
            created = user is None
            if user is not None:
                user_id = user["id"]
                if tracker.is_active(user_id):
                    return jsonify(error=ACTIVE_MESSAGE), 409
            else:
                cursor = db.execute(
                    "INSERT INTO users (identity_key,alias,car,year,color,created_at) VALUES (?,?,?,?,?,?)",
                    (key, profile["alias"], profile["car"], profile["year"], profile["color"], time.time()),
                )
                user_id = cursor.lastrowid
            db.execute("DELETE FROM sessions WHERE user_id=? OR expires_at<=?", (user_id, time.time()))
            db.execute("INSERT INTO sessions VALUES (?,?,?)",
                       (token_hash(raw_token), user_id, time.time() + 86400 * 7))
        # Commit revocation before disconnecting sockets so their callbacks see it.
        # The tracker lock prevents the previous access from starting a publisher.
        tracker.release_participant(user_id)
        response = session_response(user_id, raw_token)
        response.status_code = 201 if created else 200
        return response


@bp.post("/api/logout")
@require_session
def logout():
    current_app.extensions["tracker"].logout(g.user["id"], g.user["token_hash"])
    session.clear()
    response = jsonify(ok=True)
    response.delete_cookie(COOKIE, secure=current_app.config["SESSION_COOKIE_SECURE"], samesite="Lax")
    return response


@bp.route("/api/me", methods=["GET", "PATCH"])
@require_session
def me():
    if request.method == "PATCH":
        try:
            profile = profile_fields(json_body())
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        try:
            with get_db() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("UPDATE users SET identity_key=?,alias=?,car=?,year=?,color=? WHERE id=?",
                           (identity_key(profile), profile["alias"], profile["car"], profile["year"], profile["color"], g.user["id"]))
        except sqlite3.IntegrityError:
            return jsonify(error="Estos datos ya pertenecen a otro participante. Cambia tu alias."), 409
        current_app.extensions["tracker"].profile_updated(g.user["id"])
    user = get_db().execute("SELECT * FROM users WHERE id=?", (g.user["id"],)).fetchone()
    return jsonify(user=public_user(user), csrf_token=csrf_token())
