"""SQLite access: short transactions, per-context connections, no route history."""
import sqlite3
from pathlib import Path

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    alias TEXT NOT NULL,
    car TEXT NOT NULL,
    year INTEGER,
    color TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
CREATE TABLE IF NOT EXISTS locations (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    accuracy REAL NOT NULL,
    captured_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
"""


def get_db():
    if "db" not in g:
        connection = sqlite3.connect(current_app.config["DATABASE_PATH"], timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        g.db = connection
    return g.db


def close_db(_error=None):
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def init_db(app):
    Path(app.config["DATABASE_PATH"]).parent.mkdir(parents=True, exist_ok=True)
    app.teardown_appcontext(close_db)
    with app.app_context():
        db = get_db()
        db.execute("PRAGMA journal_mode = WAL")
        db.executescript(SCHEMA)
        # Sharing consent is tied to a live connection, never restored on a restart.
        db.execute("DELETE FROM locations")
        db.commit()


def public_user(user):
    return {key: user[key] for key in ("id", "alias", "car", "year", "color")}

