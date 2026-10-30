"""Single-process live state. SQLite stores only each car's latest position."""
import math
import secrets
import time
from threading import RLock

from flask import request, session
from flask_socketio import join_room

from .auth import current_identity
from .db import get_db


class Tracker:
    def __init__(self, app, socketio):
        self.app = app
        self.io = socketio
        self.lock = RLock()
        self.sockets = {}  # sid -> (user_id, session hash)
        self.owners = {}  # user_id -> sole publishing sid

    def snapshot(self):
        with self.lock:
            now = time.time()
            rows = get_db().execute(
                "SELECT l.*,u.alias,u.car,u.year,u.color FROM locations l "
                "JOIN users u ON u.id=l.user_id WHERE l.updated_at > ? ORDER BY u.alias COLLATE NOCASE",
                (now - 60,),
            ).fetchall()
            return {"participants": [self.serialize(row, now) for row in rows], "server_time": now}

    def serialize(self, row, now=None):
        now = time.time() if now is None else now
        connected = self.owners.get(row["user_id"]) in self.sockets
        return {**{key: row[key] for key in (
            "user_id", "alias", "car", "year", "color", "latitude", "longitude",
            "accuracy", "captured_at", "updated_at"
        )}, "connected": connected, "stale": not connected or now - row["updated_at"] >= 30}

    def broadcast_user(self, user_id):
        self.prune_sessions()
        row = get_db().execute(
            "SELECT l.*,u.alias,u.car,u.year,u.color FROM locations l "
            "JOIN users u ON u.id=l.user_id WHERE l.user_id=?", (user_id,),
        ).fetchone()
        if row:
            self.io.emit("participant:updated", self.serialize(row), to="members")

    def prune_sessions(self):
        valid = {row["token_hash"] for row in get_db().execute(
            "SELECT token_hash FROM sessions WHERE expires_at>?", (time.time(),)
        ).fetchall()}
        for sid, (_user_id, session_hash) in list(self.sockets.items()):
            if session_hash not in valid:
                self.io.emit("session:expired", {}, to=sid)
                self.io.server.disconnect(sid, namespace="/")

    def remove(self, user_id, forget_owner=True):
        if forget_owner:
            self.owners.pop(user_id, None)
        with get_db() as db:
            db.execute("DELETE FROM locations WHERE user_id=?", (user_id,))
        self.io.emit("participant:removed", {"user_id": user_id}, to="members")

    def profile_updated(self, user_id):
        with self.lock:
            self.broadcast_user(user_id)

    def is_active(self, user_id):
        """Sharing consent plus a connected socket and a non-revoked session."""
        owner = self.owners.get(user_id)
        identity = self.sockets.get(owner)
        return identity is not None and get_db().execute(
            "SELECT 1 FROM sessions WHERE token_hash=? AND user_id=? AND expires_at>?",
            (identity[1], user_id, time.time()),
        ).fetchone() is not None

    def release_participant(self, user_id):
        """Called with the tracker lock held after old sessions are revoked."""
        self.remove(user_id)
        for sid, identity in list(self.sockets.items()):
            if identity[0] == user_id:
                self.io.emit("session:expired", {}, to=sid)
                self.io.server.disconnect(sid, namespace="/")

    def logout(self, user_id, session_hash):
        with self.lock:
            with get_db() as db:
                db.execute("DELETE FROM sessions WHERE token_hash=?", (session_hash,))
            owner = self.owners.get(user_id)
            if owner:
                self.io.emit("share:replaced", {"message": "Saliste de la rodada desde otra pestaña."}, to=owner)
            self.remove(user_id)
            for sid, identity in list(self.sockets.items()):
                if identity[1] == session_hash:
                    self.io.emit("session:expired", {}, to=sid)
                    self.io.server.disconnect(sid, namespace="/")

    def identity(self):
        user = current_identity()
        if user is None or self.sockets.get(request.sid) != (user["id"], user["token_hash"]):
            self.io.emit("session:expired", {}, to=request.sid)
            self.io.server.disconnect(request.sid, namespace="/")
            return None
        return user

    def cleanup_once(self):
        with self.lock:
            now = time.time()
            # Remove expired viewers from the room before broadcasting coordinates.
            for sid, (_user_id, session_hash) in list(self.sockets.items()):
                valid = get_db().execute(
                    "SELECT 1 FROM sessions WHERE token_hash=? AND expires_at>?", (session_hash, now)
                ).fetchone()
                if not valid:
                    self.io.emit("session:expired", {}, to=sid)
                    self.io.server.disconnect(sid, namespace="/")
            expired = get_db().execute("SELECT user_id FROM locations WHERE updated_at<=?", (now - 60,)).fetchall()
            for row in expired:
                # Keep consent for connected owners: a fresh GPS sample may reappear.
                self.remove(row["user_id"], forget_owner=False)
                owner = self.owners.get(row["user_id"])
                if owner not in self.sockets:
                    self.owners.pop(row["user_id"], None)
            for user_id, owner in list(self.owners.items()):
                if owner not in self.sockets:
                    exists = get_db().execute("SELECT 1 FROM locations WHERE user_id=?", (user_id,)).fetchone()
                    if not exists:
                        self.owners.pop(user_id, None)
            with get_db() as db:
                db.execute("DELETE FROM sessions WHERE expires_at<=?", (now,))

    def cleanup_loop(self):
        while True:
            self.io.sleep(5)
            try:
                with self.app.app_context():
                    self.cleanup_once()
            except Exception:
                self.app.logger.exception("No se pudo limpiar el estado de participantes")

    def register_events(self):
        @self.io.on("connect")
        def connect(auth=None):
            user = current_identity()
            expected = session.get("csrf_token", "")
            supplied = auth.get("csrf_token", "") if isinstance(auth, dict) else ""
            if not user or not isinstance(supplied, str) or not expected or not secrets.compare_digest(expected.encode(), supplied.encode()):
                return False
            origin = request.headers.get("Origin")
            if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
                return False
            with self.lock:
                self.sockets[request.sid] = (user["id"], user["token_hash"])
                join_room("members")
                self.io.emit("participants:snapshot", self.snapshot(), to=request.sid)

        @self.io.on("share:start")
        def start(_data=None):
            with self.lock:
                user = self.identity()
                if user is None:
                    return {"ok": False, "error": "Sesión finalizada."}
                previous = self.owners.get(user["id"])
                if previous and previous != request.sid:
                    self.io.emit("share:replaced", {"message": "Tu auto se está compartiendo desde otra pestaña o dispositivo."}, to=previous)
                    self.remove(user["id"])
                self.owners[user["id"]] = request.sid
                return {"ok": True}

        @self.io.on("share:stop")
        def stop(_data=None):
            with self.lock:
                user = self.identity()
                if user is None:
                    return {"ok": False, "error": "Sesión finalizada."}
                owner = self.owners.get(user["id"])
                if owner == request.sid or owner not in self.sockets:
                    self.remove(user["id"])
                return {"ok": True}

        @self.io.on("position:update")
        def position(data):
            with self.lock:
                user = self.identity()
                if user is None:
                    return {"ok": False, "error": "Sesión finalizada."}
                if self.owners.get(user["id"]) != request.sid:
                    return {"ok": False, "code": "not_owner", "error": "Activa compartir desde esta pestaña."}
                fields = ("latitude", "longitude", "accuracy", "captured_at")
                if not isinstance(data, dict) or any(
                    isinstance(data.get(key), bool) or not isinstance(data.get(key), (int, float))
                    or (isinstance(data[key], float) and not math.isfinite(data[key])) for key in fields
                ):
                    return {"ok": False, "error": "Posición inválida."}
                lat, lon, accuracy, captured = (data[key] for key in fields)
                now = time.time()
                if not (-90 <= lat <= 90 and -180 <= lon <= 180 and 0 <= accuracy <= 100000):
                    return {"ok": False, "error": "Coordenadas inválidas."}
                if not now - 15 <= captured <= now + 5:
                    return {"ok": False, "code": "old_sample", "error": "Esperando una ubicación nueva del GPS."}
                previous = get_db().execute("SELECT * FROM locations WHERE user_id=?", (user["id"],)).fetchone()
                if previous and captured <= previous["captured_at"]:
                    return {"ok": False, "code": "old_sample", "error": "La muestra del GPS ya se recibió."}
                if previous and now - previous["updated_at"] < self.app.config["POSITION_INTERVAL"]:
                    return {"ok": False, "code": "rate_limited", "error": "Espera al siguiente envío."}
                with get_db() as db:
                    db.execute(
                        "INSERT INTO locations VALUES (?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
                        "latitude=excluded.latitude,longitude=excluded.longitude,accuracy=excluded.accuracy,"
                        "captured_at=excluded.captured_at,updated_at=excluded.updated_at",
                        (user["id"], lat, lon, accuracy, captured, now),
                    )
                self.broadcast_user(user["id"])
                return {"ok": True, "updated_at": now}

        @self.io.on("disconnect")
        def disconnected(_reason=None):
            with self.lock:
                identity = self.sockets.pop(request.sid, None)
                if identity and self.owners.get(identity[0]) == request.sid:
                    self.broadcast_user(identity[0])
