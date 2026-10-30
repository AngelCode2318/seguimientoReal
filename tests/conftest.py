import re

import pytest

from occ import create_app


@pytest.fixture
def app(tmp_path):
    return create_app({
        "TESTING": True,
        "SECRET_KEY": "test-only-secret",
        "DATABASE_PATH": str(tmp_path / "club.sqlite3"),
        "START_CLEANUP_TASK": False,
        "PASSWORD_HASH_METHOD": "pbkdf2:sha256:1000",
    })


def csrf(client):
    html = client.get("/login").get_data(as_text=True)
    if 'name="csrf-token"' not in html:
        html = client.get("/").get_data(as_text=True)
    return re.search(r'name="csrf-token" content="([^"]+)"', html)[1]


def register(app, name="alex"):
    client = app.test_client()
    response = client.post("/api/register", json={
        "email": f"{name}@example.com", "password": "correct-horse-67",
        "alias": name, "car": "Mustang", "year": 1967, "color": "Rojo",
    }, headers={"X-CSRF-Token": csrf(client)})
    assert response.status_code == 201, response.json
    return client, response.json["csrf_token"], response.json["user"]


def socket_client(app, client, token):
    return app.extensions["socketio"].test_client(app, flask_test_client=client, auth={"csrf_token": token})
