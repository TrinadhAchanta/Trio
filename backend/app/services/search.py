"""Web search providers for deep research.

- Brave Search API (``BRAVE_SEARCH_API_KEY``) and Tavily (``TAVILY_API_KEY``): real web search.
- Demo search: used only when no search key is set and the mock provider is enabled. It searches a
  small built-in set of example pages (``*.example`` domains, never fetched from the network) so the
  research flow can be tried and tested without keys. Results are labeled as demo everywhere.
"""

from dataclasses import dataclass

import httpx

from ..config import get_settings
from .webfetch import Page


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


class SearchProvider:
    name = "base"
    label = "Search"
    demo = False

    async def search(self, query: str, n: int = 5) -> list[SearchResult]:  # pragma: no cover - interface
        raise NotImplementedError


class BraveSearch(SearchProvider):
    name, label = "brave", "Brave Search"

    def __init__(self, key: str, client: httpx.AsyncClient | None = None):
        self.key, self.client = key, client

    async def search(self, query: str, n: int = 5) -> list[SearchResult]:
        async with (self.client or httpx.AsyncClient(timeout=15)) as c:
            r = await c.get("https://api.search.brave.com/res/v1/web/search", params={"q": query, "count": n},
                            headers={"X-Subscription-Token": self.key, "Accept": "application/json"})
            r.raise_for_status()
            items = r.json().get("web", {}).get("results", [])
        return [SearchResult(i.get("title", ""), i["url"], i.get("description", "")) for i in items[:n] if i.get("url")]


class TavilySearch(SearchProvider):
    name, label = "tavily", "Tavily"

    def __init__(self, key: str, client: httpx.AsyncClient | None = None):
        self.key, self.client = key, client

    async def search(self, query: str, n: int = 5) -> list[SearchResult]:
        async with (self.client or httpx.AsyncClient(timeout=20)) as c:
            r = await c.post("https://api.tavily.com/search", json={"query": query, "max_results": n},
                             headers={"Authorization": f"Bearer {self.key}"})
            r.raise_for_status()
            items = r.json().get("results", [])
        return [SearchResult(i.get("title", ""), i["url"], i.get("content", "")[:300]) for i in items[:n] if i.get("url")]


DEMO_PAGES = {
    "https://heat-pumps.example/basics": Page(
        "https://heat-pumps.example/basics", "Heat pump basics (demo page)",
        "A heat pump moves heat instead of generating it. In heating mode it extracts heat from outdoor air or the "
        "ground and releases it indoors.\n\nTypical air-source heat pumps deliver 2.5 to 4 units of heat per unit of "
        "electricity (a coefficient of performance of 2.5 to 4), with lower efficiency in very cold weather."),
    "https://energy-costs.example/heat-pump-vs-gas": Page(
        "https://energy-costs.example/heat-pump-vs-gas", "Running costs: heat pump vs gas boiler (demo page)",
        "Running costs depend on the ratio between electricity and gas prices. When electricity costs less than about "
        "three times the price of gas per kWh, a heat pump with a COP of 3 is usually cheaper to run.\n\nInstallation "
        "costs are higher for heat pumps, so payback periods of 7 to 15 years are common."),
    "https://climate-facts.example/emissions": Page(
        "https://climate-facts.example/emissions", "Emissions from home heating (demo page)",
        "Heat pumps reduce emissions most where the electricity grid has a low carbon intensity. Even on an average "
        "grid, an efficient heat pump typically emits less CO2 per unit of heat than a gas boiler."),
    "https://remote-work.example/productivity": Page(
        "https://remote-work.example/productivity", "Remote work and productivity (demo page)",
        "Studies of remote work show mixed productivity effects: focused individual tasks often improve, while "
        "collaboration and mentoring can suffer without deliberate practices."),
}


class DemoSearch(SearchProvider):
    name, label, demo = "demo", "Demo search (no real web search)", True

    async def search(self, query: str, n: int = 5) -> list[SearchResult]:
        words = {w for w in query.lower().replace("-", " ").split() if len(w) > 2}
        scored = []
        for url, p in DEMO_PAGES.items():
            hay = f"{p.title} {p.text}".lower()
            score = sum(1 for w in words if w in hay)
            if score:
                scored.append((score, url, p))
        scored.sort(key=lambda x: -x[0])
        return [SearchResult(p.title, url, p.text[:200]) for _, url, p in scored[:n]]


def get_search_provider() -> SearchProvider | None:
    s = get_settings()
    if s.brave_search_api_key:
        return BraveSearch(s.brave_search_api_key)
    if s.tavily_api_key:
        return TavilySearch(s.tavily_api_key)
    if s.enable_mock_provider:
        return DemoSearch()
    return None
