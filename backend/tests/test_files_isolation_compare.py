import io
import struct
import zlib

from app.db import SessionLocal
from app.models import Message, UsageRecord
from app.providers.base import ChatMessage
from app.providers.registry import Registry, get_registry
from app.services.artifacts import detect_artifacts

from .conftest import make_client, parse_sse, register


def tiny_png() -> bytes:
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"\x00\xff\x00\x00"
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def tiny_pdf(text="Hello PDF") -> bytes:
    content = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + o + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs)+1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode())
    return out.getvalue()


def upload(c, name, data):
    return c.post("/api/files", files={"file": (name, data, "application/octet-stream")})


# ---------- files ----------

def test_upload_text_image_pdf(authed):
    r = upload(authed, "notes.md", b"# Notes\nremember the milk")
    assert r.status_code == 201 and r.json()["kind"] == "text"
    r = upload(authed, "pic.png", tiny_png())
    assert r.status_code == 201 and r.json()["content_type"] == "image/png"
    r = upload(authed, "doc.pdf", tiny_pdf())
    assert r.status_code == 201 and r.json()["kind"] == "pdf"
    assert len(authed.get("/api/files").json()) == 3


def test_upload_validation_rejects_bad_files(authed):
    assert upload(authed, "fake.png", b"<script>alert(1)</script>").status_code == 422
    assert upload(authed, "virus.exe", b"MZ\x90\x00").status_code == 422
    assert upload(authed, "empty.txt", b"").status_code == 422
    assert upload(authed, "bin.txt", b"abc\x00def").status_code == 422
    assert upload(authed, "latin1.txt", "caf\xe9".encode("latin-1")).status_code == 422
    assert upload(authed, "bad.pdf", b"not a pdf").status_code == 422


def test_upload_size_limit(authed, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "max_upload_mb", 1)
    assert upload(authed, "big.txt", b"a" * (1024 * 1024 + 1)).status_code == 422


def test_filename_is_sanitized(authed):
    r = upload(authed, "../../etc/passwd.txt", b"hi")
    assert r.status_code == 201 and r.json()["filename"] == "passwd.txt"


def test_attachment_text_reaches_model_context(authed):
    fid = upload(authed, "data.csv", b"name,score\nada,10").json()["id"]
    cid = authed.post("/api/conversations", json={}).json()["id"]
    r = authed.post(f"/api/conversations/{cid}/messages",
                    json={"content": "summarize", "model": "mock:mock-echo", "attachments": [fid]})
    assert parse_sse(r.text)[-1][0] == "done"
    from app.models import Conversation, User
    from app.routers.chat import build_context, path_to

    with SessionLocal() as db:
        conv = db.get(Conversation, cid)
        user = db.query(User).one()
        info = get_registry().resolve("mock:mock-echo")[1]
        ctx = build_context(db, user, path_to(conv, conv.messages[0].id), info, 1000)
        assert '<attached_file name="data.csv">' in ctx[0].content and "ada,10" in ctx[0].content
        msg = db.query(Message).filter(Message.role == "user").one()
        assert msg.attachments == [fid]


def test_image_attachment_passed_as_image(authed):
    fid = upload(authed, "pic.png", tiny_png()).json()["id"]
    cid = authed.post("/api/conversations", json={}).json()["id"]
    r = authed.post(f"/api/conversations/{cid}/messages",
                    json={"content": "what is this", "model": "mock:mock-echo", "attachments": [fid]})
    assert parse_sse(r.text)[-1][0] == "done"
    from app.models import Conversation, User
    from app.routers.chat import build_context, path_to

    with SessionLocal() as db:
        conv = db.get(Conversation, cid)
        user = db.query(User).one()
        info = get_registry().resolve("mock:mock-echo")[1]
        ctx = build_context(db, user, path_to(conv, conv.messages[0].id), info, 1000)
        assert len(ctx[0].images) == 1 and ctx[0].images[0].media_type == "image/png"


# ---------- tenant isolation ----------

def test_users_cannot_access_each_others_data():
    alice = make_client()
    register(alice, "alice@example.com")
    bob = make_client()
    register(bob, "bob@example.com")

    cid = alice.post("/api/conversations", json={}).json()["id"]
    events = parse_sse(alice.post(f"/api/conversations/{cid}/messages",
                                  json={"content": "make an html app", "model": "mock:mock-echo"}).text)
    art_id = events[-1][1]["artifacts"][0]["id"]
    msg_id = events[-1][1]["message"]["id"]
    fid = upload(alice, "secret.txt", b"alice secret").json()["id"]

    assert bob.get(f"/api/conversations/{cid}").status_code == 404
    assert bob.patch(f"/api/conversations/{cid}", json={"title": "pwned"}).status_code == 404
    assert bob.delete(f"/api/conversations/{cid}").status_code == 404
    assert bob.post(f"/api/conversations/{cid}/messages", json={"content": "x", "model": "mock:mock-echo"}).status_code == 404
    assert bob.get(f"/api/conversations/{cid}/export").status_code == 404
    assert bob.get(f"/api/artifacts/{art_id}").status_code == 404
    assert bob.post(f"/api/artifacts/{art_id}/versions", json={"content": "x"}).status_code == 404
    assert bob.get(f"/api/files/{fid}/content").status_code == 404
    assert bob.delete(f"/api/files/{fid}").status_code == 404
    assert bob.post(f"/api/messages/{msg_id}/rating", json={"rating": -1}).status_code == 404
    assert bob.get("/api/conversations").json() == []
    assert bob.get("/api/artifacts").json() == []
    assert bob.get("/api/files").json() == []
    assert bob.get("/api/usage/summary").json()["recent"] == []

    # Bob can't smuggle Alice's file into his own chat as an attachment.
    bcid = bob.post("/api/conversations", json={}).json()["id"]
    r = bob.post(f"/api/conversations/{bcid}/messages",
                 json={"content": "read it", "model": "mock:mock-echo", "attachments": [fid]})
    assert r.status_code == 400

    assert alice.get(f"/api/files/{fid}/content").text == "alice secret"


# ---------- compare ----------

def test_compare_streams_each_model(authed):
    r = authed.post("/api/compare", json={"prompt": "What is 2+2?", "models": ["mock:mock-echo", "mock:mock-deep"]})
    events = parse_sse(r.text)
    assert events[-1][0] == "end"
    starts = {d["model"] for e, d in events if e == "start"}
    dones = {d["model"]: d for e, d in events if e == "done"}
    assert starts == set(dones) == {"mock:mock-echo", "mock:mock-deep"}
    for d in dones.values():
        assert d["latency_ms"] >= 0 and d["output_tokens"] > 0 and d["cost_usd"] == 0.0
    text = {m: "".join(d["text"] for e, d in events if e == "delta" and d["model"] == m) for m in starts}
    assert "mock-deep" in text["mock:mock-deep"] and "Mock deep response" in text["mock:mock-deep"]
    with SessionLocal() as db:
        assert db.query(UsageRecord).filter(UsageRecord.feature == "compare").count() == 2


def test_compare_reports_per_model_errors(authed):
    r = authed.post("/api/compare", json={"prompt": "x [[mock-error]]", "models": ["mock:mock-echo", "mock:mock-deep"]})
    events = parse_sse(r.text)
    assert sorted(d["model"] for e, d in events if e == "error") == ["mock:mock-deep", "mock:mock-echo"]


def test_compare_validation(authed):
    assert authed.post("/api/compare", json={"prompt": "x", "models": ["mock:mock-echo"]}).status_code == 422
    assert authed.post("/api/compare", json={"prompt": "x", "models": ["mock:mock-echo", "mock:mock-echo"]}).status_code == 400
    assert authed.post("/api/compare", json={"prompt": "x", "models": ["mock:mock-echo", "openai:gpt-5"]}).status_code == 400


def test_synthesize_and_vote(authed):
    r = authed.post("/api/compare/synthesize", json={
        "prompt": "Q", "model": "mock:mock-echo",
        "answers": [{"model": "mock:mock-echo", "text": "A"}, {"model": "mock:mock-deep", "text": "B"}]})
    events = parse_sse(r.text)
    assert any(e == "done" for e, _ in events)
    r = authed.post("/api/compare/vote", json={"prompt": "Q", "preferred_model": "mock:mock-echo",
                                               "models": ["mock:mock-echo", "mock:mock-deep"]})
    assert r.status_code == 204


# ---------- routing & artifacts units ----------

def test_router_prefers_real_providers_over_mock():
    from app.config import Settings

    reg = Registry(Settings(anthropic_api_key="k", openai_api_key="", enable_mock_provider=True))
    assert reg.route("hi", "fast").model.id == "anthropic:claude-haiku-5-5"
    assert reg.route("hi", "balanced").model.id == "anthropic:claude-sonnet-5-5"
    assert reg.route("please debug this algorithm", "balanced").model.id == "anthropic:claude-opus-5-5"
    assert reg.route("hi", "deep").model.id == "anthropic:claude-opus-5-5"

    only_xai = Registry(Settings(xai_api_key="k", enable_mock_provider=False))
    d = only_xai.route("look at this", "balanced", needs_vision=True)
    assert d.model.id == "xai:grok-4"
    none = Registry(Settings(enable_mock_provider=False))
    assert none.route("hi", "fast") is None


def test_detect_artifacts():
    text = 'Intro\n```html title="Demo"\n<div>' + "x" * 50 + '</div>\n```\nand\n```svg\n<svg xmlns="http://www.w3.org/2000/svg"><circle r="5"/></svg>\n```\n```html\n<p>tiny</p>\n```'
    arts = detect_artifacts(text)
    assert [(a.kind, a.title) for a in arts] == [("html", "Demo"), ("svg", "Untitled SVG")]


def test_context_trimming_drops_oldest():
    from app.providers.base import ModelInfo
    from app.routers.chat import build_context

    class FakeMsg:
        def __init__(self, role, content):
            self.role, self.content, self.status, self.attachments = role, content, "complete", []

    info = ModelInfo("mock", "m", "M", "fast", context_window=5_000)
    path = [FakeMsg("user", "a" * 20_000), FakeMsg("assistant", "b" * 4_000), FakeMsg("user", "latest")]
    ctx = build_context(None, None, path, info, max_tokens=1_000)
    assert [c.content for c in ctx] == ["latest"]
    assert isinstance(ctx[0], ChatMessage)
