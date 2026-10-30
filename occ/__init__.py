import os
import secrets
from pathlib import Path

from flask import Flask, g, jsonify, redirect, render_template, request, session, url_for
from flask_socketio import SocketIO
from werkzeug.middleware.proxy_fix import ProxyFix

from . import auth, db


def create_app(config=None):
    app = Flask(__name__)
    production = os.environ.get("APP_ENV") == "production"
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY"),
        DATABASE_PATH=os.environ.get("DATABASE_PATH", str(Path(app.root_path).parent / "instance" / "oldclassic.sqlite3")),
        SESSION_COOKIE_NAME="occ_csrf",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=production,
        MAX_CONTENT_LENGTH=16 * 1024,
        START_CLEANUP_TASK=True,
        POSITION_INTERVAL=5,
    )
    if config:
        app.config.update(config)
    if not app.config["SECRET_KEY"]:
        if production:
            raise RuntimeError("Configura SECRET_KEY antes de iniciar en producción.")
        app.config["SECRET_KEY"] = secrets.token_hex(32)
    db.init_db(app)
    app.extensions["auth_limiter"] = auth.AttemptLimiter()

    @app.before_request
    def identity_and_csrf():
        g.user = auth.current_identity()
        if request.method in ("POST", "PATCH", "PUT", "DELETE"):
            expected = session.get("csrf_token", "")
            provided = request.headers.get("X-CSRF-Token", "")
            if not expected or not secrets.compare_digest(expected.encode(), provided.encode()):
                return jsonify(error="La página expiró. Recárgala e intenta de nuevo."), 403
            origin = request.headers.get("Origin")
            if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
                return jsonify(error="Origen no permitido."), 403

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(self), camera=(), microphone=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: https://tile.openstreetmap.org; "
            "connect-src 'self' ws: wss:; font-src 'self'; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        if request.path.startswith("/api/") or response.mimetype == "text/html":
            response.headers["Cache-Control"] = "no-store"
        if production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.errorhandler(413)
    def too_large(_error):
        return jsonify(error="El contenido enviado es demasiado grande."), 413

    @app.get("/")
    def index():
        if not g.user:
            return render_template("auth.html", csrf_token=auth.csrf_token())
        return render_template("map.html", user=db.public_user(g.user), csrf_token=auth.csrf_token())

    @app.get("/login")
    @app.get("/register")
    def legacy_entry():
        return redirect(url_for("index"))

    @app.get("/healthz")
    def health():
        db.get_db().execute("SELECT 1").fetchone()
        return jsonify(status="ok")

    app.register_blueprint(auth.bp)
    socketio = SocketIO(app, async_mode="threading", max_http_buffer_size=16384,
                        ping_interval=15, ping_timeout=20, always_connect=True)
    from .realtime import Tracker
    tracker = Tracker(app, socketio)
    app.extensions["tracker"] = tracker

    @app.get("/api/participants")
    @auth.require_session
    def participants():
        return jsonify(tracker.snapshot())

    tracker.register_events()
    if production:
        # Wrap Socket.IO too, so its request contexts see the original HTTPS origin.
        # Render terminates TLS at one trusted reverse proxy.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
    if app.config["START_CLEANUP_TASK"]:
        socketio.start_background_task(tracker.cleanup_loop)
    return app
