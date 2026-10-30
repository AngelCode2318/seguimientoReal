"""SQLite access: short transactions, per-context connections, no route history."""
import sqlite3
from pathlib import Path

from flask import current_app, g

from .profiles import identity_key, profile_fields

SCHEMA_VERSION = 2
USER_SCHEMA = """
CREATE TABLE {table} (
    id INTEGER PRIMARY KEY,
    identity_key TEXT NOT NULL UNIQUE,
    alias TEXT NOT NULL,
    car TEXT NOT NULL,
    year INTEGER,
    color TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
)
"""
SESSION_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at REAL NOT NULL
)
"""
LOCATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS locations (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    accuracy REAL NOT NULL,
    captured_at REAL NOT NULL,
    updated_at REAL NOT NULL
)
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
        upgrade_db(db)


def upgrade_db(db):
    """Atomically upgrade legacy accounts, preserving canonical participant IDs."""
    db.execute("PRAGMA foreign_keys = OFF")
    try:
        db.execute("BEGIN IMMEDIATE")
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError("Esta base de datos requiere una versión más reciente de la app.")
        columns = {row["name"] for row in db.execute("PRAGMA table_info(users)")}
        if not columns:
            db.execute(USER_SCHEMA.format(table="users"))
        elif "email" in columns or "password_hash" in columns:
            db.execute(USER_SCHEMA.format(table="users_v2"))
            seen = set()
            for row in db.execute("SELECT * FROM users ORDER BY id").fetchall():
                profile = profile_fields(dict(row))
                key = identity_key(profile)
                if key in seen:
                    continue
                seen.add(key)
                db.execute(
                    "INSERT INTO users_v2 (id,identity_key,alias,car,year,color,created_at) VALUES (?,?,?,?,?,?,?)",
                    (row["id"], key, profile["alias"], profile["car"], profile["year"], profile["color"], row["created_at"]),
                )
            db.execute("DROP TABLE IF EXISTS locations")
            db.execute("DROP TABLE IF EXISTS sessions")
            db.execute("DROP TABLE users")
            db.execute("ALTER TABLE users_v2 RENAME TO users")
        elif "identity_key" not in columns:
            raise RuntimeError("El esquema de participantes no es compatible.")
        db.execute(SESSION_SCHEMA)
        db.execute("CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id)")
        db.execute(LOCATION_SCHEMA)
        # Consent belongs to a live connection, never to a previously saved position.
        db.execute("DELETE FROM locations")
        if db.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("No se pudo comprobar la integridad de los participantes.")
        db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.execute("PRAGMA foreign_keys = ON")


def public_user(user):
    return {key: user[key] for key in ("id", "alias", "car", "year", "color")}

