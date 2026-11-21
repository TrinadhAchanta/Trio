"""Knowledge bases: chunking, ingestion, full-text retrieval, chat with citations, isolation."""

from app.db import SessionLocal
from app.models import KBChunk, Message
from app.services.knowledge import chunk_text

from .conftest import make_client, parse_sse, register
from .test_files_isolation_compare import upload

HANDBOOK = """Expense policy

Employees can claim travel expenses within 30 days. Receipts are required for any amount above 25 euros.

Vacation policy

Full-time employees receive 26 days of paid vacation per year. Unused days can be carried over until March 31.

Security policy

Laptops must use full-disk encryption. Report lost devices to the IT helpdesk within 24 hours."""


def make_kb(c, name="Handbook"):
    r = c.post("/api/knowledge", json={"name": name})
    assert r.status_code == 201
    return r.json()["id"]


def test_chunking_respects_size_and_keeps_everything():
    body = "\n\n".join(f"Paragraph {i} " + "word " * 50 for i in range(30))
    chunks = chunk_text(body, size=600, overlap=100)
    assert all(len(c) <= 600 for c in chunks)
    assert all(f"Paragraph {i} " in "".join(chunks) for i in range(30))
    long = "x" * 50 + " " + "lorem ipsum " * 400
    assert all(len(c) <= 600 for c in chunk_text(long, size=600, overlap=100))
    assert chunk_text("") == []


def test_create_add_text_and_file_then_search(authed):
    kb = make_kb(authed)
    r = authed.post(f"/api/knowledge/{kb}/documents", json={"title": "Employee handbook", "text": HANDBOOK})
    assert r.status_code == 201 and r.json()["chunk_count"] >= 1
    f = upload(authed, "onboarding.md", b"# Onboarding\n\nNew starters get their badge from reception on day one.").json()
    r = authed.post(f"/api/knowledge/{kb}/documents", json={"file_id": f["id"]})
    assert r.status_code == 201 and r.json()["title"] == "onboarding.md"
    detail = authed.get(f"/api/knowledge/{kb}").json()
    assert detail["document_count"] == 2
    res = authed.post(f"/api/knowledge/{kb}/search", json={"query": "How many vacation days do I get?"}).json()["results"]
    assert res and "26 days" in res[0]["content"]
    res = authed.post(f"/api/knowledge/{kb}/search", json={"query": "where do new starters collect badges"}).json()["results"]
    assert res[0]["title"] == "onboarding.md"
    assert authed.post(f"/api/knowledge/{kb}/search", json={"query": "quantum chromodynamics"}).json()["results"] == []


def test_document_validation(authed):
    kb = make_kb(authed)
    assert authed.post(f"/api/knowledge/{kb}/documents", json={"text": "no title"}).status_code == 422
    assert authed.post(f"/api/knowledge/{kb}/documents", json={}).status_code == 422
    from .test_files_isolation_compare import tiny_png
    img = upload(authed, "pic.png", tiny_png()).json()
    assert authed.post(f"/api/knowledge/{kb}/documents", json={"file_id": img["id"]}).status_code == 400


def test_delete_document_and_kb_removes_chunks(authed):
    kb = make_kb(authed)
    doc = authed.post(f"/api/knowledge/{kb}/documents", json={"title": "H", "text": HANDBOOK}).json()
    assert authed.delete(f"/api/knowledge/{kb}/documents/{doc['id']}").status_code == 204
    with SessionLocal() as db:
        assert db.query(KBChunk).count() == 0
    authed.post(f"/api/knowledge/{kb}/documents", json={"title": "H", "text": HANDBOOK})
    assert authed.delete(f"/api/knowledge/{kb}").status_code == 204
    with SessionLocal() as db:
        assert db.query(KBChunk).count() == 0
    assert authed.get(f"/api/knowledge/{kb}").status_code == 404


def test_chat_with_knowledge_base_cites_sources_and_regenerate_keeps_them(authed):
    kb = make_kb(authed)
    authed.post(f"/api/knowledge/{kb}/documents", json={"title": "Employee handbook", "text": HANDBOOK})
    cid = authed.post("/api/conversations", json={}).json()["id"]
    r = authed.post(f"/api/conversations/{cid}/messages", json={
        "content": "How long do I have to claim travel expenses?", "model": "mock:mock-echo", "knowledge_base_ids": [kb]})
    events = parse_sse(r.text)
    meta = events[0][1]
    sources = meta["user_message"]["sources"]
    assert sources and sources[0]["n"] == 1 and "30 days" in sources[0]["content"]
    text = "".join(d["text"] for e, d in events if e == "delta")
    assert "Source [1] (Employee handbook)" in text
    with SessionLocal() as db:
        um = db.query(Message).filter(Message.role == "user").one()
        assert um.sources[0]["title"] == "Employee handbook"
    r = authed.post(f"/api/conversations/{cid}/regenerate", json={"user_message_id": meta["user_message"]["id"],
                                                                   "model": "mock:mock-echo"})
    assert "Source [1]" in "".join(d["text"] for e, d in parse_sse(r.text) if e == "delta")


def test_knowledge_bases_are_private(authed):
    kb = make_kb(authed)
    authed.post(f"/api/knowledge/{kb}/documents", json={"title": "Secret plan", "text": "The launch code word is PELICAN."})
    eve = make_client()
    register(eve, "eve@example.com")
    assert eve.get("/api/knowledge").json() == []
    assert eve.get(f"/api/knowledge/{kb}").status_code == 404
    assert eve.post(f"/api/knowledge/{kb}/search", json={"query": "launch code"}).status_code == 404
    assert eve.post(f"/api/knowledge/{kb}/documents", json={"title": "x", "text": "y"}).status_code == 404
    # Passing someone else's knowledge base id in chat silently retrieves nothing.
    cid = eve.post("/api/conversations", json={}).json()["id"]
    r = eve.post(f"/api/conversations/{cid}/messages", json={"content": "what is the launch code word?",
                                                             "model": "mock:mock-echo", "knowledge_base_ids": [kb]})
    events = parse_sse(r.text)
    assert events[0][1]["user_message"]["sources"] == []
    assert "PELICAN" not in r.text
