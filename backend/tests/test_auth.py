from app.db import SessionLocal
from app.models import AuditEvent

from .conftest import make_client, register


def test_register_sets_session_and_me_works(client):
    user = register(client)
    assert user["email"] == "ada@example.com"
    assert "password_hash" not in user
    me = client.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["display_name"] == "Ada"
    cookie = client.cookies.get("trio_session")
    assert cookie


def test_session_cookie_is_httponly(client):
    r = client.post("/api/auth/register", json={"email": "c@example.com", "password": "abcdefgh12", "display_name": "C"})
    sc = r.headers["set-cookie"].lower()
    assert "httponly" in sc and "samesite=lax" in sc


def test_duplicate_email_rejected(client):
    register(client)
    r = make_client().post("/api/auth/register",
                           json={"email": "ADA@example.com", "password": "another-pass-9", "display_name": "X"})
    assert r.status_code == 409


def test_weak_password_rejected(client):
    r = client.post("/api/auth/register", json={"email": "b@example.com", "password": "short", "display_name": "B"})
    assert r.status_code == 422
    r = client.post("/api/auth/register", json={"email": "b@example.com", "password": "onlyletters", "display_name": "B"})
    assert r.status_code == 422


def test_invalid_email_rejected(client):
    r = client.post("/api/auth/register", json={"email": "not-an-email", "password": "abcdefgh12", "display_name": "B"})
    assert r.status_code == 422


def test_login_logout_flow():
    register(make_client())
    c = make_client()
    assert c.get("/api/auth/me").status_code == 401
    bad = c.post("/api/auth/login", json={"email": "ada@example.com", "password": "wrong-password-1"})
    assert bad.status_code == 401
    unknown = c.post("/api/auth/login", json={"email": "nobody@example.com", "password": "wrong-password-1"})
    assert unknown.status_code == 401 and unknown.json()["detail"] == bad.json()["detail"]
    ok = c.post("/api/auth/login", json={"email": "Ada@Example.com", "password": "correct-horse-42"})
    assert ok.status_code == 200
    assert c.get("/api/auth/me").status_code == 200
    c.post("/api/auth/logout")
    assert c.get("/api/auth/me").status_code == 401
    with SessionLocal() as db:
        actions = {a.action for a in db.query(AuditEvent).all()}
    assert {"auth.register", "auth.login", "auth.login_failed"} <= actions


def test_logout_all_revokes_existing_tokens():
    a = make_client()
    register(a)
    token = a.cookies.get("trio_session")
    b = make_client()
    b.cookies.set("trio_session", token)
    assert b.get("/api/auth/me").status_code == 200
    assert a.post("/api/auth/logout-all").status_code == 204
    assert b.get("/api/auth/me").status_code == 401


def test_tampered_token_rejected(client):
    register(client)
    token = client.cookies.get("trio_session")
    c = make_client()
    c.cookies.set("trio_session", token[:-2] + ("AA" if not token.endswith("AA") else "BB"))
    assert c.get("/api/auth/me").status_code == 401


def test_auth_rate_limit(monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "rate_limit_auth_per_minute", 3)
    c = make_client()
    codes = [c.post("/api/auth/login", json={"email": "x@example.com", "password": "nope-nope-1"}).status_code
             for _ in range(5)]
    assert codes[:3] == [401, 401, 401] and codes[3:] == [429, 429]


def test_preferences(authed):
    r = authed.patch("/api/auth/me/preferences", json={"default_model": "mock:mock-echo", "theme": "dark"})
    assert r.status_code == 200
    assert r.json()["preferences"] == {"default_model": "mock:mock-echo", "theme": "dark"}
