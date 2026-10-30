import re

import pytest
from werkzeug.middleware.proxy_fix import ProxyFix

from occ import create_app


def test_production_requires_a_secret(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        create_app()


def test_https_proxy_wraps_socketio_and_cookies_are_secure(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    app = create_app({"TESTING": True, "SECRET_KEY": "production-test-only",
        "DATABASE_PATH": str(tmp_path / "production.sqlite3"), "START_CLEANUP_TASK": False})
    assert isinstance(app.wsgi_app, ProxyFix)
    assert "SocketIO" in type(app.wsgi_app.app).__name__
    client = app.test_client()
    headers = {"X-Forwarded-Proto": "https", "Origin": "https://localhost"}
    page = client.get("/", headers=headers)
    csrf = re.search(r'name="csrf-token" content="([^"]+)"', page.text)[1]
    response = client.post("/api/enter", json={"alias": "Alex", "car": "Mustang"},
        headers={**headers, "X-CSRF-Token": csrf})
    assert response.status_code == 201
    cookies = response.headers.getlist("Set-Cookie")
    assert len(cookies) == 2
    assert all("Secure" in cookie and "HttpOnly" in cookie and "SameSite=Lax" in cookie for cookie in cookies)
