import pytest

from occ.auth import token_hash
from occ.db import get_db
from conftest import csrf, register, socket_client


def test_registration_login_profile_and_logout(app):
    client, token, user = register(app)
    assert user["car"] == "Mustang"
    assert client.get("/").status_code == 200
    assert client.get("/api/me").json["user"]["email"] == "alex@example.com"
    with app.app_context():
        stored = get_db().execute("SELECT password_hash FROM users").fetchone()[0]
        assert stored != "correct-horse-67"
    response = client.patch("/api/me", json={"alias": "Alex OCC", "car": "Chevelle", "year": "1970", "color": "Azul"}, headers={"X-CSRF-Token": token})
    assert response.status_code == 200
    assert response.json["user"]["year"] == 1970
    socket = socket_client(app, client, token)
    assert socket.is_connected()
    assert client.post("/api/logout", headers={"X-CSRF-Token": token}).status_code == 200
    assert not socket.is_connected()
    assert client.get("/api/me").status_code == 401
    response = client.post("/api/login", json={"email": "ALEX@example.com", "password": "correct-horse-67"}, headers={"X-CSRF-Token": csrf(client)})
    assert response.status_code == 200
    assert response.json["user"]["alias"] == "Alex OCC"


def test_duplicate_and_incorrect_password(app):
    register(app)
    client = app.test_client()
    token = csrf(client)
    data = {"email": "alex@example.com", "password": "some-other-password", "alias": "Otro", "car": "Vocho"}
    assert client.post("/api/register", json=data, headers={"X-CSRF-Token": token}).status_code == 409
    assert client.post("/api/login", json=data, headers={"X-CSRF-Token": token}).status_code == 401


@pytest.mark.parametrize("path", ["/api/me", "/api/participants"])
def test_guest_cannot_read(app, path):
    assert app.test_client().get(path).status_code == 401


def test_csrf_and_origins(app):
    client, token, _ = register(app)
    assert client.post("/api/logout").status_code == 403
    assert client.post("/api/logout", headers={"X-CSRF-Token": "é"}).status_code == 403
    assert client.post("/api/logout", headers={"X-CSRF-Token": token, "Origin": "https://evil.example"}).status_code == 403
    assert not app.extensions["socketio"].test_client(app, flask_test_client=client, auth={"csrf_token": "wrong"}).is_connected()
    assert not app.extensions["socketio"].test_client(app, flask_test_client=client, auth={"csrf_token": "é"}).is_connected()
    assert not app.extensions["socketio"].test_client(app, flask_test_client=client, auth={"csrf_token": token}, headers={"Origin": "https://evil.example"}).is_connected()
    assert not app.extensions["socketio"].test_client(app).is_connected()


def test_revoked_token_cannot_be_replayed(app):
    client, token, _ = register(app)
    raw = client.get_cookie("occ_sid").value
    assert client.post("/api/logout", headers={"X-CSRF-Token": token}).status_code == 200
    client.set_cookie("occ_sid", raw)
    assert client.get("/api/me").status_code == 401


def test_expired_session_disconnects(app):
    client, token, _ = register(app)
    socket = socket_client(app, client, token)
    with app.app_context():
        with get_db() as db:
            db.execute("UPDATE sessions SET expires_at=0 WHERE token_hash=?", (token_hash(client.get_cookie("occ_sid").value),))
    assert client.get("/api/me").status_code == 401
    result = socket.emit("share:start", callback=True)
    assert not result["ok"]
    assert not socket.is_connected()


@pytest.mark.parametrize("changes", [
    {"year": "1967.5"}, {"year": True}, {"year": "1800"},
    {"alias": ""}, {"alias": []}, {"car": "a" * 81},
    {"password": "short"}, {"email": "invalid"},
])
def test_registration_validation(app, changes):
    client = app.test_client()
    data = {"email": "valid@example.com", "password": "correct-password", "alias": "Alex", "car": "Mustang", **changes}
    assert client.post("/api/register", json=data, headers={"X-CSRF-Token": csrf(client)}).status_code == 400


def test_security_headers_and_static_assets(app):
    client = app.test_client()
    response = client.get("/login")
    assert response.headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "no-referrer" not in response.headers["Referrer-Policy"]
    for path in ("leaflet/leaflet.js", "leaflet/leaflet.css", "socket.io.min.js"):
        assert client.get("/static/vendor/" + path).status_code == 200
    assert client.get("/healthz").json == {"status": "ok"}


def test_oversized_payload(app):
    client = app.test_client()
    assert client.post("/api/login", json={"email": "a" * 20000}, headers={"X-CSRF-Token": csrf(client)}).status_code == 413
