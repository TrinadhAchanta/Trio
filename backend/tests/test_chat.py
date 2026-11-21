from app.db import SessionLocal
from app.models import Artifact, Message, UsageRecord

from .conftest import parse_sse


def new_conv(c):
    r = c.post("/api/conversations", json={})
    assert r.status_code == 201
    return r.json()["id"]


def send(c, conv_id, content, model="mock:mock-echo", **extra):
    r = c.post(f"/api/conversations/{conv_id}/messages", json={"content": content, "model": model, **extra})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("text/event-stream")
    return parse_sse(r.text)


def test_streaming_reply_persists(authed):
    cid = new_conv(authed)
    events = send(authed, cid, "Hello there, how are you?")
    kinds = [e for e, _ in events]
    assert kinds[0] == "meta" and kinds[-1] == "done"
    assert kinds.count("delta") > 5, "reply should arrive in many chunks"
    meta = events[0][1]
    assert meta["mock"] is True and meta["provider"] == "mock"
    streamed = "".join(d["text"] for e, d in events if e == "delta")
    done = events[-1][1]
    assert done["message"]["content"] == streamed
    assert "Mock response" in streamed
    assert done["usage"]["output_tokens"] > 0

    conv = authed.get(f"/api/conversations/{cid}").json()
    assert conv["title"] == "Hello there, how are you?"
    roles = [m["role"] for m in conv["messages"]]
    assert roles == ["user", "assistant"]
    assert conv["messages"][1]["parent_id"] == conv["messages"][0]["id"]
    assert conv["messages"][1]["status"] == "complete"

    with SessionLocal() as db:
        u = db.query(UsageRecord).one()
        assert (u.provider, u.model, u.status, u.feature) == ("mock", "mock-echo", "ok", "chat")


def test_history_is_sent_back_to_model(authed):
    cid = new_conv(authed)
    send(authed, cid, "first")
    events = send(authed, cid, "second")
    text = "".join(d["text"] for e, d in events if e == "delta")
    assert "2 earlier message(s)" in text
    conv = authed.get(f"/api/conversations/{cid}").json()
    assert len(conv["messages"]) == 4
    assert conv["messages"][2]["parent_id"] == conv["messages"][1]["id"]


def test_auto_routing_uses_configured_provider(authed):
    cid = new_conv(authed)
    events = send(authed, cid, "hi", model="auto", reasoning_mode="deep")
    meta = events[0][1]
    assert meta["model"] == "mock-deep"
    assert meta["route_reason"].startswith("Chat task: chose Mock Deep")
    assert meta["task_type"] == "chat"


def test_unconfigured_and_unknown_models_rejected(authed):
    cid = new_conv(authed)
    r = authed.post(f"/api/conversations/{cid}/messages", json={"content": "hi", "model": "openai:gpt-5"})
    assert r.status_code == 400 and "not configured" in r.json()["detail"]
    r = authed.post(f"/api/conversations/{cid}/messages", json={"content": "hi", "model": "nope:model"})
    assert r.status_code == 400


def test_provider_failure_is_reported_and_recorded(authed):
    authed.patch("/api/auth/me/preferences", json={"allow_fallback": False})
    cid = new_conv(authed)
    events = send(authed, cid, "please fail [[mock-error]]")
    assert events[-1][0] == "error"
    err = events[-1][1]
    assert err["code"] == "provider_unavailable" and err["retryable"] is True
    with SessionLocal() as db:
        msg = db.query(Message).filter(Message.role == "assistant").one()
        assert msg.status == "error" and "provider_unavailable" in msg.error
        u = db.query(UsageRecord).one()
        assert u.status == "error" and u.error_code == "provider_unavailable"


def test_mid_stream_failure_keeps_partial_text(authed):
    cid = new_conv(authed)
    events = send(authed, cid, "go [[mock-error-mid]]")
    assert "delta" in [e for e, _ in events] and events[-1][0] == "error"
    with SessionLocal() as db:
        msg = db.query(Message).filter(Message.role == "assistant").one()
        assert msg.status == "error" and len(msg.content) > 0


def test_failed_turn_does_not_break_next_turn(authed):
    cid = new_conv(authed)
    send(authed, cid, "x [[mock-error]]")
    events = send(authed, cid, "try again")
    assert events[-1][0] == "done"


def test_artifact_detected_and_versioned(authed):
    cid = new_conv(authed)
    events = send(authed, cid, "Build me a small html counter app")
    done = events[-1][1]
    assert len(done["artifacts"]) == 1
    art = done["artifacts"][0]
    assert art["kind"] == "html" and art["title"] == "Counter app"
    aid = art["id"]
    detail = authed.get(f"/api/artifacts/{aid}").json()
    assert detail["versions"][0]["content"].startswith("<!doctype html>")

    r = authed.post(f"/api/artifacts/{aid}/versions", json={"content": "<h1>Edited</h1>"})
    assert r.json()["current_version"] == 2
    r = authed.post(f"/api/artifacts/{aid}/restore", json={"version": 1})
    body = r.json()
    assert body["current_version"] == 3 and body["versions"][2]["source"] == "restore"
    assert body["versions"][2]["content"] == body["versions"][0]["content"]
    dl = authed.get(f"/api/artifacts/{aid}/download?version=2")
    assert dl.text == "<h1>Edited</h1>" and "attachment" in dl.headers["content-disposition"]
    listed = authed.get(f"/api/artifacts?conversation_id={cid}").json()
    assert [a["id"] for a in listed] == [aid]


def test_regenerate_and_edit_create_branches(authed):
    cid = new_conv(authed)
    send(authed, cid, "original question")
    conv = authed.get(f"/api/conversations/{cid}").json()
    user_id = conv["messages"][0]["id"]

    r = authed.post(f"/api/conversations/{cid}/regenerate", json={"user_message_id": user_id, "model": "mock:mock-deep"})
    events = parse_sse(r.text)
    assert events[-1][0] == "done"
    conv = authed.get(f"/api/conversations/{cid}").json()
    replies = [m for m in conv["messages"] if m["role"] == "assistant"]
    assert len(replies) == 2 and all(m["parent_id"] == user_id for m in replies)
    assert {m["model"] for m in replies} == {"mock-echo", "mock-deep"}

    # Edit = new user message that branches from the same parent as the original (root here).
    events = send(authed, cid, "edited question", branch_from_root=True)
    assert events[-1][0] == "done"
    conv = authed.get(f"/api/conversations/{cid}").json()
    roots = [m for m in conv["messages"] if m["parent_id"] is None]
    assert len(roots) == 2


def test_parent_must_belong_to_conversation(authed):
    a, b = new_conv(authed), new_conv(authed)
    send(authed, a, "hello")
    msg_in_a = authed.get(f"/api/conversations/{a}").json()["messages"][0]["id"]
    r = authed.post(f"/api/conversations/{b}/messages", json={"content": "x", "model": "mock:mock-echo",
                                                                "parent_id": msg_in_a})
    assert r.status_code == 400


def test_rating_search_export_delete(authed):
    cid = new_conv(authed)
    send(authed, cid, "Tell me about zebras")
    other = new_conv(authed)
    send(authed, other, "Something unrelated")

    conv = authed.get(f"/api/conversations/{cid}").json()
    aid = conv["messages"][1]["id"]
    assert authed.post(f"/api/messages/{aid}/rating", json={"rating": 1}).json()["rating"] == 1
    assert authed.post(f"/api/messages/{conv['messages'][0]['id']}/rating", json={"rating": 1}).status_code == 404

    found = authed.get("/api/conversations", params={"q": "zebra"}).json()
    assert [c["id"] for c in found] == [cid]
    assert len(authed.get("/api/conversations").json()) == 2
    assert authed.get("/api/conversations", params={"q": "100%_"}).json() == []

    md = authed.get(f"/api/conversations/{cid}/export?format=md")
    assert md.status_code == 200 and "# Tell me about zebras" in md.text
    js = authed.get(f"/api/conversations/{cid}/export?format=json").json()
    assert len(js["messages"]) == 2

    r = authed.patch(f"/api/conversations/{cid}", json={"title": "Zebras", "folder": "Animals"})
    assert r.json()["title"] == "Zebras" and r.json()["folder"] == "Animals"
    assert [c["id"] for c in authed.get("/api/conversations", params={"folder": "Animals"}).json()] == [cid]

    assert authed.delete(f"/api/conversations/{cid}").status_code == 204
    assert authed.get(f"/api/conversations/{cid}").status_code == 404
    with SessionLocal() as db:
        assert db.query(Message).filter(Message.conversation_id == cid).count() == 0
        assert db.query(Artifact).count() == 0


def test_daily_budget_blocks_requests(authed, monkeypatch):
    from app.config import get_settings

    cid = new_conv(authed)
    send(authed, cid, "hi")
    monkeypatch.setattr(get_settings(), "daily_cost_limit_usd", 0.0)
    r = authed.post(f"/api/conversations/{cid}/messages", json={"content": "x", "model": "mock:mock-echo"})
    assert r.status_code == 429


def test_usage_summary(authed):
    cid = new_conv(authed)
    send(authed, cid, "hi")
    send(authed, cid, "boom [[mock-error]]")  # mock-echo fails, then its fallback mock-deep fails too
    s = authed.get("/api/usage/summary").json()
    statuses = sorted(r["status"] for r in s["by_model"])
    assert statuses == ["error", "error", "ok"]
    assert len(s["recent"]) == 3 and s["recent"][0]["error_code"] == "provider_unavailable"


def test_models_endpoint_labels_configuration(authed):
    data = authed.get("/api/models").json()
    by = {p["name"]: p for p in data["providers"]}
    assert by["mock"]["configured"] and by["mock"]["mock"]
    assert not by["openai"]["configured"] and not by["anthropic"]["configured"]
    assert data["auto_available"] is True
    anth = {m["model"]: m for m in by["anthropic"]["models"]}
    assert anth["claude-opus-5-5"]["source"] == "documented"


def test_requires_auth(client):
    for method, path in [("get", "/api/conversations"), ("post", "/api/conversations"), ("get", "/api/models"),
                         ("get", "/api/usage/summary"), ("get", "/api/artifacts")]:
        assert getattr(client, method)(path).status_code == 401


async def test_client_disconnect_saves_partial_reply_as_stopped(authed):
    from app.models import Conversation, User
    from app.providers.base import ChatMessage
    from app.providers.registry import get_registry
    from app.routers.chat import RoutePlan, stream_reply

    cid = new_conv(authed)
    with SessionLocal() as db:
        conv = db.get(Conversation, cid)
        user = db.query(User).one()
        um = Message(conversation_id=conv.id, role="user", content="hello")
        db.add(um)
        db.flush()
        am = Message(conversation_id=conv.id, parent_id=um.id, role="assistant", status="streaming",
                     provider="mock", model="mock-echo")
        db.add(am)
        db.commit()
        ids = (user.id, conv.org_id, conv.id, am.id)
    plan = RoutePlan([get_registry().resolve("mock:mock-echo")], "test", "chat")
    gen = stream_reply(user_id=ids[0], org_id=ids[1], conv_id=ids[2], assistant_id=ids[3], plan=plan,
                       chat_messages=[ChatMessage("user", "hello")], mode="fast", user_message=None)
    assert (await gen.__anext__()).startswith("event: meta")
    assert (await gen.__anext__()).startswith("event: delta")
    await gen.aclose()  # what Starlette does when the browser disconnects / user presses Stop
    with SessionLocal() as db:
        msg = db.get(Message, ids[3])
        assert msg.status == "stopped" and msg.content
        assert db.query(UsageRecord).one().status == "cancelled"
