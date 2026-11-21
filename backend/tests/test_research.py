"""Deep research: SSRF guard, HTML extraction, query parsing, citation verification, full job via demo search."""

import httpx
import pytest

from app.config import get_settings
from app.db import SessionLocal
from app.models import UsageRecord
from app.services import webfetch
from app.services.research import best_excerpt, parse_queries, verify_citations
from app.services.search import BraveSearch, TavilySearch

from .conftest import make_client, register


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://localhost/admin", "http://10.0.0.5/", "http://192.168.1.1/", "http://169.254.169.254/latest",
    "http://[::1]/", "http://[::ffff:127.0.0.1]/", "file:///etc/passwd", "ftp://example.com/", "http://example.com:8080/",
    "http://user:pw@example.com/", "http://0.0.0.0/",
])
async def test_ssrf_guard_blocks_private_and_odd_urls(url):
    with pytest.raises(webfetch.FetchError):
        await webfetch.check_url(url)


async def test_public_ip_is_allowed():
    await webfetch.check_url("https://93.184.215.14/")


async def test_fetch_extracts_text_and_follows_checked_redirects():
    html = ("<html><head><title>Solar panels</title><script>evil()</script><style>p{}</style></head><body><nav>Menu</nav>"
            "<article><h1>How solar works</h1><p>Panels convert light into electricity.</p></article></body></html>")

    def handler(req: httpx.Request):
        if req.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new"})
        if req.url.path == "/to-internal":
            return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})
        return httpx.Response(200, text=html, headers={"content-type": "text/html; charset=utf-8"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        page = await webfetch.fetch("https://93.184.215.14/old", client=c)
        assert page.url.endswith("/new") and page.title == "Solar panels"
        assert "Panels convert light into electricity." in page.text
        assert "evil" not in page.text and "Menu" not in page.text
        with pytest.raises(webfetch.FetchError):
            await webfetch.fetch("https://93.184.215.14/to-internal", client=c)


def test_parse_queries_and_excerpt():
    assert parse_queries('Here you go: ["a b", "c d", "a b"]', "q") == ["a b", "c d"]
    assert parse_queries("no json here", "What is X?") == ["What is X?"]
    text = "\n\n".join(["Unrelated filler. " * 40, "Heat pumps have a COP of 3. " * 5, "More filler. " * 40])
    assert "COP" in best_excerpt(text, "what COP do heat pumps have", limit=1600)


def test_verify_citations():
    report = "# Title\n\nSummary [1][2].\n\nClaim with bad cite [7].\n\nUncited paragraph."
    fixed, stats = verify_citations(report, 2)
    assert "[?]" in fixed and "[7]" not in fixed
    assert stats["cited_sources"] == [1, 2] and stats["invalid_citations"] == [7]
    assert stats["paragraphs"] == 3 and stats["paragraphs_with_citations"] == 1  # an invalid cite does not count


async def test_search_providers_parse_responses():
    def brave(req):
        assert req.headers["X-Subscription-Token"] == "bk"
        return httpx.Response(200, json={"web": {"results": [{"title": "T", "url": "https://a.test", "description": "d"}]}})

    def tavily(req):
        assert req.headers["Authorization"] == "Bearer tk"
        return httpx.Response(200, json={"results": [{"title": "T2", "url": "https://b.test", "content": "c"}]})

    r = await BraveSearch("bk", httpx.AsyncClient(transport=httpx.MockTransport(brave))).search("q")
    assert r[0].url == "https://a.test"
    r = await TavilySearch("tk", httpx.AsyncClient(transport=httpx.MockTransport(tavily))).search("q")
    assert r[0].title == "T2"


def test_full_research_job_with_demo_search(authed):
    assert authed.get("/api/research/config").json() == {"available": True, "provider": "Demo search (no real web search)",
                                                         "demo": True}
    r = authed.post("/api/research", json={"question": "Are heat pumps cheaper to run than gas boilers?",
                                           "model": "mock:mock-deep"})
    assert r.status_code == 201
    job = authed.get(f"/api/research/{r.json()['id']}").json()  # background task already ran in the test client
    assert job["status"] == "done", job
    assert job["demo"] is True and job["used_model"] == "mock:mock-deep"
    assert len(job["sources"]) >= 2 and all(s["url"].startswith("https://") for s in job["sources"])
    assert "[1]" in job["report"] and job["stats"]["invalid_citations"] == []
    assert job["sources"][0]["cited"] is True
    assert any("Checked citations" in s["message"] for s in job["steps"])
    md = authed.get(f"/api/research/{job['id']}/export.md")
    assert md.status_code == 200 and "## Sources" in md.text and "Demo search" in md.text
    with SessionLocal() as db:
        assert {u.feature for u in db.query(UsageRecord).all()} == {"research"}
        assert db.query(UsageRecord).count() == 2  # plan + report


def test_research_without_results_and_isolation(authed):
    r = authed.post("/api/research", json={"question": "zzzz qqqq xxxx", "model": "mock:mock-deep"})
    job = authed.get(f"/api/research/{r.json()['id']}").json()
    assert job["status"] == "error" and "no results" in job["error"]
    other = make_client()
    register(other, "mallory@example.com")
    assert other.get(f"/api/research/{job['id']}").status_code == 404
    assert other.get("/api/research").json() == []


def test_research_unavailable_without_search(authed, monkeypatch):
    monkeypatch.setattr(get_settings(), "enable_mock_provider", False)
    assert authed.get("/api/research/config").json()["available"] is False
