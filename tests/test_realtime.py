import time

import pytest

from conftest import register, socket_client
from occ import create_app
from occ.db import get_db


def sample(**changes):
    return {"latitude": 19.4326, "longitude": -99.1332, "accuracy": 8, "captured_at": time.time(), **changes}


def start(socket):
    assert socket.emit("share:start", callback=True)["ok"]


def publish(socket, **changes):
    return socket.emit("position:update", sample(**changes), callback=True)


def test_broadcast_privacy_stop_and_logout(app):
    a, a_csrf, a_user = register(app, "alex")
    b, b_csrf, _ = register(app, "bea")
    first, second = socket_client(app, a, a_csrf), socket_client(app, b, b_csrf)
    assert a.get("/api/participants").json["participants"] == []
    assert not publish(first)["ok"]  # Login alone is not consent.
    start(first)
    assert publish(first)["ok"]
    events = second.get_received()
    point = next(event["args"][0] for event in events if event["name"] == "participant:updated")
    assert point["user_id"] == a_user["id"]
    assert "email" not in point and "password_hash" not in point
    assert a.get("/api/participants").json["participants"][0]["latitude"] == 19.4326
    assert first.emit("share:stop", callback=True)["ok"]
    assert any(event["name"] == "participant:removed" for event in second.get_received())
    assert b.get("/api/participants").json["participants"] == []
    start(first)
    assert publish(first)["ok"]
    a.post("/api/logout", headers={"X-CSRF-Token": a_csrf})
    assert b.get("/api/participants").json["participants"] == []
    assert not first.is_connected()


def test_rate_limit_and_new_sample_moves_car(app):
    client, token, _ = register(app)
    socket = socket_client(app, client, token)
    start(socket)
    assert publish(socket)["ok"]
    assert publish(socket)["code"] == "rate_limited"
    with app.app_context():
        with get_db() as db:
            db.execute("UPDATE locations SET updated_at=updated_at-6")
    assert publish(socket, latitude=19.5)["ok"]
    participants = client.get("/api/participants").json["participants"]
    assert len(participants) == 1 and participants[0]["latitude"] == 19.5


def test_single_emitter_and_old_disconnect_does_not_affect_new_owner(app):
    client, token, _ = register(app)
    old, new = socket_client(app, client, token), socket_client(app, client, token)
    start(old)
    assert publish(old)["ok"]
    start(new)
    assert any(event["name"] == "share:replaced" for event in old.get_received())
    assert publish(old)["code"] == "not_owner"
    assert publish(new)["ok"]
    old.emit("share:stop", callback=True)
    old.disconnect()
    participants = client.get("/api/participants").json["participants"]
    assert len(participants) == 1 and participants[0]["connected"]


def test_stale_expiry_reconnection_and_no_cached_replay(app):
    client, token, _ = register(app)
    socket = socket_client(app, client, token)
    start(socket)
    assert publish(socket)["ok"]
    with app.app_context():
        with get_db() as db:
            db.execute("UPDATE locations SET updated_at=updated_at-31")
    assert client.get("/api/participants").json["participants"][0]["stale"]
    socket.disconnect()
    assert not client.get("/api/participants").json["participants"][0]["connected"]
    reconnected = socket_client(app, client, token)
    assert not publish(reconnected)["ok"]
    start(reconnected)
    assert publish(reconnected, captured_at=time.time()-40)["code"] == "old_sample"
    assert publish(reconnected)["ok"]
    with app.app_context():
        with get_db() as db:
            db.execute("UPDATE locations SET updated_at=updated_at-61")
        app.extensions["tracker"].cleanup_once()
    assert client.get("/api/participants").json["participants"] == []
    # Still connected and consented: a new GPS fix returns the car to the map.
    assert publish(reconnected)["ok"]


@pytest.mark.parametrize("changes", [
    {"latitude": 91}, {"longitude": -181}, {"accuracy": -1},
    {"latitude": "19"}, {"latitude": True}, {"accuracy": float("inf")}, {"latitude": 10**50},
    {"captured_at": 1}, {"captured_at": time.time()+10000},
])
def test_rejects_invalid_positions(app, changes):
    client, token, _ = register(app)
    socket = socket_client(app, client, token)
    start(socket)
    assert not publish(socket, **changes)["ok"]
    assert client.get("/api/participants").json["participants"] == []


def test_profile_changes_are_broadcast_without_private_email(app):
    client, token, _ = register(app)
    socket = socket_client(app, client, token)
    start(socket)
    assert publish(socket)["ok"]
    socket.get_received()
    client.patch("/api/me", json={"alias": "<b>Alex</b>", "car": "Vocho"}, headers={"X-CSRF-Token": token})
    point = next(event["args"][0] for event in socket.get_received() if event["name"] == "participant:updated")
    assert point["alias"] == "<b>Alex</b>" and "email" not in point
    assert "&lt;b&gt;Alex&lt;/b&gt;" in client.get("/").get_data(as_text=True)


def test_expired_viewer_receives_no_coordinates(app):
    a, a_token, _ = register(app, "alex")
    b, b_token, b_user = register(app, "bea")
    first, expired = socket_client(app, a, a_token), socket_client(app, b, b_token)
    with app.app_context():
        with get_db() as db:
            db.execute("UPDATE sessions SET expires_at=0 WHERE user_id=?", (b_user["id"],))
    start(first)
    assert publish(first)["ok"]
    assert not expired.is_connected()


def test_restart_keeps_accounts_but_never_restores_sharing(app):
    client, token, _ = register(app)
    socket = socket_client(app, client, token)
    start(socket)
    assert publish(socket)["ok"]
    restarted = create_app(dict(app.config))
    with restarted.app_context():
        assert get_db().execute("SELECT count(*) FROM users").fetchone()[0] == 1
        assert get_db().execute("SELECT count(*) FROM locations").fetchone()[0] == 0


def test_thirty_clients_receive_one_marker_per_car(app):
    # Protocol-level regression test; tools/load_test.py exercises real WebSockets.
    clients = []
    for index in range(30):
        http, token, _ = register(app, f"car{index:02}")
        clients.append((http, socket_client(app, http, token)))
    for _, socket in clients:
        start(socket)
        assert publish(socket)["ok"]
    for http, socket in clients:
        people = http.get("/api/participants").json["participants"]
        assert len(people) == len({person["user_id"] for person in people}) == 30
        assert len([event for event in socket.get_received() if event["name"] == "participant:updated"]) == 30
