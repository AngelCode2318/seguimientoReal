from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from threading import Barrier

import pytest

from occ.auth import ACTIVE_MESSAGE
from occ.db import get_db
from conftest import csrf, enter, socket_client

PROFILE = {"alias": "alex", "car": "Mustang", "year": 1967, "color": "Rojo"}


def guest_entry(app, profile=None):
    client = app.test_client()
    response = client.post("/api/enter", json=PROFILE if profile is None else profile,
                           headers={"X-CSRF-Token": csrf(client)})
    return client, response


def test_entry_profile_exit_and_return(app):
    client, token, user = enter(app)
    assert set(user) == {"id", "alias", "car", "year", "color"}
    assert client.get("/").status_code == 200
    assert client.get("/api/me").json["user"] == user
    assert client.get("/api/participants").json["participants"] == []
    assert not app.extensions["tracker"].owners
    changed = {"alias": "Alex OCC", "car": "Chevelle", "year": "1970", "color": "Azul"}
    response = client.patch("/api/me", json=changed, headers={"X-CSRF-Token": token})
    assert response.status_code == 200
    assert response.json["user"]["year"] == 1970
    socket = socket_client(app, client, token)
    assert socket.is_connected()
    assert client.post("/api/logout", headers={"X-CSRF-Token": token}).status_code == 200
    assert not socket.is_connected()
    assert client.get("/api/me").status_code == 401
    _, returned = guest_entry(app, changed)
    assert returned.status_code == 200
    assert returned.json["user"]["id"] == user["id"]


def test_entry_form_has_only_four_fields_and_map_keeps_fullscreen(app):
    class Inputs(HTMLParser):
        def __init__(self):
            super().__init__()
            self.fields = {}

        def handle_starttag(self, tag, attrs):
            if tag == "input":
                values = dict(attrs)
                self.fields[values["name"]] = values

    client = app.test_client()
    html = client.get("/").text
    parser = Inputs()
    parser.feed(html)
    assert set(parser.fields) == {"alias", "car", "year", "color"}
    assert "required" in parser.fields["alias"] and "required" in parser.fields["car"]
    assert "required" not in parser.fields["year"] and "required" not in parser.fields["color"]
    assert "Entrar al mapa" in html
    assert "Crear cuenta" not in html and "Iniciar sesión" not in html
    client, _, _ = enter(app)
    html = client.get("/").text
    assert 'id="toggle-map-fullscreen"' in html and 'id="auth-form"' not in html
    assert "Salir de la rodada" in html and "Correo" not in html
    assert client.get("/static/fullscreen.js").status_code == 200


def test_legacy_pages_redirect_and_credential_endpoints_are_removed(app):
    client = app.test_client()
    for path in ("/login", "/register"):
        response = client.get(path)
        assert response.status_code == 302 and response.location == "/"
    token = csrf(client)
    for path in ("/api/login", "/api/register"):
        assert client.post(path, json={}, headers={"X-CSRF-Token": token}).status_code == 404


def test_same_browser_reuses_valid_access_without_replacement(app):
    client, token, user = enter(app)
    raw = client.get_cookie("occ_sid").value
    response = client.post("/api/enter", json=PROFILE, headers={"X-CSRF-Token": token})
    assert response.status_code == 200 and response.json["user"]["id"] == user["id"]
    assert client.get_cookie("occ_sid").value == raw


def test_inactive_viewer_is_recovered_and_old_access_is_revoked(app):
    old, token, user = enter(app)
    raw = old.get_cookie("occ_sid").value
    socket = socket_client(app, old, token)
    new, response = guest_entry(app)
    assert response.status_code == 200
    assert response.json["user"]["id"] == user["id"]
    assert not socket.is_connected()
    assert old.get("/api/me").status_code == 401
    assert old.get("/api/participants").status_code == 401
    assert old.patch("/api/me", json={**PROFILE, "alias": "changed"}, headers={"X-CSRF-Token": token}).status_code == 401
    assert old.post("/api/logout", headers={"X-CSRF-Token": token}).status_code == 401
    old.set_cookie("occ_sid", raw)
    assert not socket_client(app, old, token).is_connected()
    assert new.get("/api/participants").json["participants"] == []
    assert not app.extensions["tracker"].owners


def test_active_sharer_blocks_identical_entrance_even_before_first_gps_fix(app):
    old, token, user = enter(app)
    socket = socket_client(app, old, token)
    assert socket.emit("share:start", callback=True)["ok"]
    _, response = guest_entry(app)
    assert response.status_code == 409 and response.json["error"] == ACTIVE_MESSAGE
    assert socket.is_connected()
    assert old.get("/api/me").status_code == 200
    with app.app_context():
        assert get_db().execute("SELECT count(*) FROM users").fetchone()[0] == 1
        assert get_db().execute("SELECT count(*) FROM sessions WHERE user_id=?", (user["id"],)).fetchone()[0] == 1


def test_stopping_sharing_allows_recovery_while_old_viewer_is_connected(app):
    old, token, user = enter(app)
    socket = socket_client(app, old, token)
    assert socket.emit("share:start", callback=True)["ok"]
    assert guest_entry(app)[1].status_code == 409
    assert socket.emit("share:stop", callback=True)["ok"]
    assert socket.is_connected()
    new, response = guest_entry(app)
    assert response.status_code == 200 and response.json["user"]["id"] == user["id"]
    assert not socket.is_connected()
    assert new.get("/api/participants").json["participants"] == []


def test_disconnected_sharer_can_be_recovered_without_waiting_for_marker_expiry(app):
    import time
    old, token, user = enter(app)
    socket = socket_client(app, old, token)
    socket.emit("share:start", callback=True)
    assert socket.emit("position:update", {"latitude": 19.43, "longitude": -99.13,
        "accuracy": 5, "captured_at": time.time()}, callback=True)["ok"]
    socket.disconnect()
    new, response = guest_entry(app)
    assert response.status_code == 200 and response.json["user"]["id"] == user["id"]
    assert new.get("/api/participants").json["participants"] == []


@pytest.mark.parametrize("profile", [
    {"alias": "  ALEX  ", "car": " MUSTANG ", "year": " 1967 ", "color": " rojo "},
    {"alias": "alex", "car": "Mustang", "year": 1967, "color": "ROJO"},
])
def test_normalized_four_fields_recover_same_id(app, profile):
    _, _, user = enter(app)
    _, response = guest_entry(app, profile)
    assert response.status_code == 200 and response.json["user"]["id"] == user["id"]


def test_unicode_accents_and_internal_spaces(app):
    _, first = guest_entry(app, {"alias": "José  Luis", "car": "  Ford   Mustang "})
    _, returned = guest_entry(app, {"alias": " JOSE\u0301 LUIS ", "car": "ford mustang", "year": " ", "color": " "})
    _, different = guest_entry(app, {"alias": "Jose Luis", "car": "Ford Mustang"})
    assert returned.status_code == 200 and returned.json["user"]["id"] == first.json["user"]["id"]
    assert different.status_code == 201


@pytest.mark.parametrize("change", [{"alias": "bea"}, {"car": "Vocho"}, {"year": 1970}, {"color": "Azul"}, {"year": ""}, {"color": ""}])
def test_every_identity_field_matters(app, change):
    _, _, user = enter(app)
    _, response = guest_entry(app, {**PROFILE, **change})
    assert response.status_code == 201 and response.json["user"]["id"] != user["id"]


def test_profile_edit_cannot_duplicate_another_participant(app):
    old, token, user = enter(app)
    enter(app, "bea")
    response = old.patch("/api/me", json={**PROFILE, "alias": " BEA "}, headers={"X-CSRF-Token": token})
    assert response.status_code == 409
    assert old.get("/api/me").json["user"] == user


def test_simultaneous_entries_create_one_participant_and_one_valid_access(app):
    barrier = Barrier(2)

    def submit(_):
        client = app.test_client()
        token = csrf(client)
        barrier.wait(timeout=5)
        response = client.post("/api/enter", json=PROFILE, headers={"X-CSRF-Token": token})
        return client, response

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(submit, range(2)))
    assert sorted(response.status_code for _, response in outcomes) == [200, 201]
    assert len({response.json["user"]["id"] for _, response in outcomes}) == 1
    assert sorted(client.get("/api/me").status_code for client, _ in outcomes) == [200, 401]
    with app.app_context():
        assert get_db().execute("SELECT count(*) FROM users").fetchone()[0] == 1
        assert get_db().execute("SELECT count(*) FROM sessions").fetchone()[0] == 1


def test_reentry_and_start_sharing_are_coordinated(app):
    old, token, _ = enter(app)
    socket = socket_client(app, old, token)
    new = app.test_client()
    new_token = csrf(new)
    barrier = Barrier(2)

    def start():
        barrier.wait(timeout=5)
        try:
            result = socket.emit("share:start", callback=True)
            return bool(result and result["ok"])
        except RuntimeError:
            return False  # Re-entry won and disconnected the previous socket.

    def recover():
        barrier.wait(timeout=5)
        return new.post("/api/enter", json=PROFILE, headers={"X-CSRF-Token": new_token})

    with ThreadPoolExecutor(max_workers=2) as pool:
        sharing = pool.submit(start)
        recovering = pool.submit(recover)
        result, response = sharing.result(), recovering.result()
    if response.status_code == 409:
        assert result and socket.is_connected() and old.get("/api/me").status_code == 200
    else:
        assert response.status_code == 200 and not result
        assert not socket.is_connected() and old.get("/api/me").status_code == 401


@pytest.mark.parametrize("path", ["/api/me", "/api/participants"])
def test_guest_cannot_read(app, path):
    assert app.test_client().get(path).status_code == 401


def test_csrf_and_origins(app):
    client, token, _ = enter(app)
    assert client.post("/api/logout").status_code == 403
    assert client.post("/api/logout", headers={"X-CSRF-Token": "é"}).status_code == 403
    assert client.post("/api/logout", headers={"X-CSRF-Token": token, "Origin": "https://evil.example"}).status_code == 403
    for invalid in ("wrong", "é"):
        assert not socket_client(app, client, invalid).is_connected()
    assert not app.extensions["socketio"].test_client(app, flask_test_client=client,
        auth={"csrf_token": token}, headers={"Origin": "https://evil.example"}).is_connected()
    assert not app.extensions["socketio"].test_client(app).is_connected()
    guest = app.test_client()
    assert guest.post("/api/enter", json=PROFILE).status_code == 403


def test_revoked_token_cannot_be_replayed(app):
    client, token, _ = enter(app)
    raw = client.get_cookie("occ_sid").value
    assert client.post("/api/logout", headers={"X-CSRF-Token": token}).status_code == 200
    client.set_cookie("occ_sid", raw)
    assert client.get("/api/me").status_code == 401


def test_expired_session_can_be_recovered_but_cannot_publish(app):
    client, token, user = enter(app)
    socket = socket_client(app, client, token)
    socket.emit("share:start", callback=True)
    with app.app_context():
        with get_db() as db:
            db.execute("UPDATE sessions SET expires_at=0")
    assert client.get("/api/me").status_code == 401
    new, response = guest_entry(app)
    assert response.status_code == 200 and response.json["user"]["id"] == user["id"]
    assert not socket.is_connected() and new.get("/api/me").status_code == 200


@pytest.mark.parametrize("changes", [
    {"year": "1967.5"}, {"year": True}, {"year": "1800"},
    {"alias": ""}, {"alias": []}, {"car": "a" * 81}, {"color": None},
])
def test_entry_validation(app, changes):
    assert guest_entry(app, {**PROFILE, **changes})[1].status_code == 400


def test_security_headers_and_static_assets(app):
    client = app.test_client()
    response = client.get("/")
    assert response.headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert "no-referrer" not in response.headers["Referrer-Policy"]
    for path in ("leaflet/leaflet.js", "leaflet/leaflet.css", "socket.io.min.js"):
        assert client.get("/static/vendor/" + path).status_code == 200
    assert client.get("/healthz").json == {"status": "ok"}


def test_oversized_payload(app):
    client = app.test_client()
    assert client.post("/api/enter", json={"alias": "a" * 20000}, headers={"X-CSRF-Token": csrf(client)}).status_code == 413
