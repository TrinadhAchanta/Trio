"""Settings and storage used for hosted deployments."""

from app.config import Settings, get_settings
from app.db import SessionLocal
from app.models import StoredBlob

from .test_files_isolation_compare import upload


def test_database_url_is_normalized_for_psycopg():
    assert Settings(database_url="postgres://u:p@h:5432/d").database_url == "postgresql+psycopg://u:p@h:5432/d"
    assert Settings(database_url="postgresql://u:p@h/d").database_url == "postgresql+psycopg://u:p@h/d"
    assert Settings(database_url="postgresql+psycopg://u@h/d").database_url == "postgresql+psycopg://u@h/d"


def test_db_storage_round_trip(authed, monkeypatch):
    monkeypatch.setattr(get_settings(), "storage_backend", "db")
    f = upload(authed, "notes.md", b"# stored in postgres").json()
    with SessionLocal() as db:
        assert db.query(StoredBlob).count() == 1
    assert authed.get(f"/api/files/{f['id']}/content").content == b"# stored in postgres"
    assert authed.delete(f"/api/files/{f['id']}").status_code == 204
    with SessionLocal() as db:
        assert db.query(StoredBlob).count() == 0



def test_site_wide_spend_cap(authed, monkeypatch):
    from app.models import UsageRecord

    from .conftest import make_client, register
    other = make_client()
    register(other, "big.spender@example.com")
    cid = other.post("/api/conversations", json={}).json()["id"]
    other.post(f"/api/conversations/{cid}/messages", json={"content": "hi", "model": "mock:mock-echo"})
    with SessionLocal() as db:  # someone else spent $3 today
        db.query(UsageRecord).update({"cost_usd": 3.0})
        db.commit()
    monkeypatch.setattr(get_settings(), "site_daily_cost_limit_usd", 2.0)
    cid = authed.post("/api/conversations", json={}).json()["id"]
    r = authed.post(f"/api/conversations/{cid}/messages", json={"content": "hi", "model": "mock:mock-echo"})
    assert r.status_code == 503 and "daily AI usage limit" in r.json()["detail"]
    monkeypatch.setattr(get_settings(), "site_daily_cost_limit_usd", 0.0)
    r = authed.post(f"/api/conversations/{cid}/messages", json={"content": "hi", "model": "mock:mock-echo"})
    assert r.status_code == 200
