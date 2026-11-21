"""Personal agents and explicit memory."""

from .conftest import make_client, parse_sse, register


def reply_text(r):
    return "".join(d["text"] for e, d in parse_sse(r.text) if e == "delta")


def test_agent_crud_and_chat_uses_instructions_and_knowledge(authed):
    kb = authed.post("/api/knowledge", json={"name": "Recipes"}).json()["id"]
    authed.post(f"/api/knowledge/{kb}/documents", json={"title": "Pancakes", "text": "Pancakes need flour, eggs and milk."})
    r = authed.post("/api/agents", json={"name": "Chef", "instructions": "Answer like a friendly chef.",
                                         "model": "mock:mock-deep", "reasoning_mode": "deep",
                                         "knowledge_base_ids": [kb], "starters": ["What can I cook?", " "]})
    assert r.status_code == 201
    agent = r.json()
    assert agent["starters"] == ["What can I cook?"]
    conv = authed.post("/api/conversations", json={"agent_id": agent["id"]}).json()
    assert conv["agent_id"] == agent["id"] and conv["default_model"] == "mock:mock-deep" and conv["reasoning_mode"] == "deep"
    r = authed.post(f"/api/conversations/{conv['id']}/messages", json={"content": "What do pancakes need?",
                                                                      "model": "mock:mock-deep"})
    text = reply_text(r)
    assert "Acting as your agent **Chef**" in text
    assert "Source [1] (Pancakes)" in text  # the agent's knowledge base was searched automatically
    upd = dict(agent, name="Head chef", knowledge_base_ids=[], starters=[])
    assert authed.put(f"/api/agents/{agent['id']}", json=upd).json()["name"] == "Head chef"
    assert authed.delete(f"/api/agents/{agent['id']}").status_code == 204
    assert authed.get(f"/api/conversations/{conv['id']}").json()["agent_id"] is None


def test_agents_are_private_and_validate_knowledge_bases(authed):
    other = make_client()
    register(other, "pat@example.com")
    their_kb = other.post("/api/knowledge", json={"name": "Theirs"}).json()["id"]
    r = authed.post("/api/agents", json={"name": "Spy", "knowledge_base_ids": [their_kb]})
    assert r.status_code == 400
    mine = authed.post("/api/agents", json={"name": "Mine"}).json()
    assert other.get(f"/api/agents/{mine['id']}").status_code == 404
    assert other.post("/api/conversations", json={"agent_id": mine["id"]}).status_code == 404
    assert other.get("/api/agents").json() == []


def test_memory_is_explicit_used_in_chat_and_can_be_turned_off(authed):
    assert authed.get("/api/memory").json() == {"enabled": True, "items": []}
    authed.post("/api/memory", json={"content": "I am vegetarian."})
    cid = authed.post("/api/conversations", json={}).json()["id"]
    r = authed.post(f"/api/conversations/{cid}/messages", json={"content": "My name is Sam.", "model": "mock:mock-echo"})
    assert "1 item(s) from your memory" in reply_text(r)
    user_msg = parse_sse(r.text)[0][1]["user_message"]["id"]
    item = authed.post("/api/memory", json={"message_id": user_msg}).json()
    assert item["source"] == "message" and item["content"] == "My name is Sam."
    assert len(authed.get("/api/memory").json()["items"]) == 2
    authed.patch("/api/auth/me/preferences", json={"memory_enabled": False})
    r = authed.post(f"/api/conversations/{cid}/messages", json={"content": "hi", "model": "mock:mock-echo"})
    assert "from your memory" not in reply_text(r)
    assert authed.delete(f"/api/memory/{item['id']}").status_code == 204
    assert authed.delete("/api/memory").status_code == 204
    assert authed.get("/api/memory").json()["items"] == []
    other = make_client()
    register(other, "quinn@example.com")
    assert other.post("/api/memory", json={"message_id": user_msg}).status_code == 404
    assert authed.post("/api/memory", json={}).status_code == 422
