import sqlite3
import time

import pytest

from occ import create_app
from occ.db import SCHEMA_VERSION, get_db, upgrade_db
from conftest import csrf


@pytest.fixture
def legacy_path(tmp_path):
    path = tmp_path / "legacy.sqlite3"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL, alias TEXT NOT NULL, car TEXT NOT NULL,
            year INTEGER, color TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL);
        CREATE TABLE sessions (token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, expires_at REAL NOT NULL);
        CREATE INDEX sessions_user ON sessions(user_id);
        CREATE TABLE locations (user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            latitude REAL NOT NULL, longitude REAL NOT NULL, accuracy REAL NOT NULL,
            captured_at REAL NOT NULL, updated_at REAL NOT NULL);
    """)
    db.executemany("INSERT INTO users VALUES (?,?,?,?,?,?,?,?)", [
        (3, "one@example.com", "legacy-hash-one", "José Luis", "Mustang", 1967, "Rojo", 1),
        (9, "two@example.com", "legacy-hash-two", " JOSE\u0301  LUIS ", " MUSTANG ", 1967, "ROJO", 2),
        (20, "three@example.com", "legacy-hash-three", "Beto", "Vocho", None, "", 3),
    ])
    db.executemany("INSERT INTO sessions VALUES (?,?,?)", [("old-one", 3, time.time()+1000), ("old-two", 9, time.time()+1000)])
    db.execute("INSERT INTO locations VALUES (9,19.4,-99.1,5,?,?)", (time.time(), time.time()))
    db.commit()
    db.close()
    return path


def test_legacy_migration_preserves_ids_merges_duplicates_and_removes_credentials(legacy_path):
    config = {"TESTING": True, "SECRET_KEY": "migration-test", "DATABASE_PATH": str(legacy_path), "START_CLEANUP_TASK": False}
    app = create_app(config)
    with app.app_context():
        db = get_db()
        assert db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert "email" not in {row["name"] for row in db.execute("PRAGMA table_info(users)")}
        assert "password_hash" not in {row["name"] for row in db.execute("PRAGMA table_info(users)")}
        assert [row["id"] for row in db.execute("SELECT id FROM users ORDER BY id")] == [3, 20]
        assert db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM locations").fetchone()[0] == 0
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    client = app.test_client()
    response = client.post("/api/enter", json={"alias": "josé luis", "car": "mustang", "year": "1967", "color": "rojo"},
                           headers={"X-CSRF-Token": csrf(client)})
    assert response.status_code == 200 and response.json["user"]["id"] == 3
    raw = client.get_cookie("occ_sid").value
    restarted = create_app(config)
    new_client = restarted.test_client()
    new_client.set_cookie("occ_sid", raw)
    assert new_client.get("/api/me").status_code == 200  # No repeat invalidation.
    with restarted.app_context():
        assert get_db().execute("SELECT count(*) FROM users").fetchone()[0] == 2
        assert get_db().execute("SELECT count(*) FROM sessions").fetchone()[0] == 1


def test_failed_migration_rolls_back_every_change(legacy_path, monkeypatch):
    import occ.db as module
    original = module.identity_key
    calls = 0

    def fail_during_copy(profile):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated copy failure")
        return original(profile)

    monkeypatch.setattr(module, "identity_key", fail_during_copy)
    db = sqlite3.connect(legacy_path)
    db.row_factory = sqlite3.Row
    with pytest.raises(RuntimeError, match="simulated copy failure"):
        upgrade_db(db)
    assert db.execute("SELECT count(*) FROM users").fetchone()[0] == 3
    assert db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 2
    assert db.execute("SELECT count(*) FROM locations").fetchone()[0] == 1
    assert "email" in {row["name"] for row in db.execute("PRAGMA table_info(users)")}
    assert db.execute("PRAGMA user_version").fetchone()[0] == 0
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert db.execute("SELECT 1 FROM sqlite_master WHERE name='users_v2'").fetchone() is None
    db.close()
