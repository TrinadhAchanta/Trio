"""Safe web fetching for research: blocks requests to private networks (SSRF), caps size and time,
and extracts readable text from HTML.

Only http/https on ports 80/443. Every hostname is resolved and rejected if any address is private,
loopback, link-local, multicast, reserved or unspecified; redirects are followed manually (max 3) and
re-checked. Note: DNS can change between the check and the connection (rebinding); for stronger
isolation run fetching through an egress proxy that enforces the same rules.
"""

import asyncio
import ipaddress
import re
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

MAX_BYTES = 2_000_000
TIMEOUT_S = 10.0
MAX_REDIRECTS = 3
USER_AGENT = "TrioResearchBot/0.1 (+https://example.invalid/bot)"


class FetchError(Exception):
    pass


@dataclass
class Page:
    url: str
    title: str
    text: str


def _ip_allowed(ip: str) -> bool:
    a = ipaddress.ip_address(ip)
    if isinstance(a, ipaddress.IPv6Address) and a.ipv4_mapped:
        a = a.ipv4_mapped
    return a.is_global and not (a.is_multicast or a.is_reserved or a.is_loopback or a.is_link_local or a.is_private)


async def check_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise FetchError("Only http and https URLs are allowed.")
    if parts.username or parts.password:
        raise FetchError("URLs with credentials are not allowed.")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    if port not in (80, 443):
        raise FetchError("Only ports 80 and 443 are allowed.")
    host = parts.hostname
    try:
        ipaddress.ip_address(host)
        addrs = [host]
    except ValueError:
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except socket.gaierror as e:
            raise FetchError(f"Could not resolve {host}.") from e
        addrs = list({i[4][0] for i in infos})
    if not addrs or not all(_ip_allowed(a) for a in addrs):
        raise FetchError("That address is not publicly reachable, so it was not fetched.")


class _TextExtractor(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer", "header", "form", "aside", "template"}
    BLOCK = {"p", "div", "section", "article", "li", "h1", "h2", "h3", "h4", "h5", "h6", "br", "tr", "pre", "blockquote"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> tuple[str, str]:
    p = _TextExtractor()
    p.feed(html)
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(p.parts))
    text = re.sub(r"\n\s*\n+", "\n\n", text).strip()
    return p.title.strip(), text


async def fetch(url: str, client: httpx.AsyncClient | None = None) -> Page:
    own = client is None
    client = client or httpx.AsyncClient(timeout=TIMEOUT_S, follow_redirects=False,
                                         headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain"})
    try:
        for _ in range(MAX_REDIRECTS + 1):
            await check_url(url)
            async with client.stream("GET", url) as r:
                if r.status_code in (301, 302, 303, 307, 308) and "location" in r.headers:
                    url = urljoin(url, r.headers["location"])
                    continue
                if r.status_code >= 400:
                    raise FetchError(f"The page returned HTTP {r.status_code}.")
                ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
                if ctype not in ("text/html", "text/plain", "application/xhtml+xml"):
                    raise FetchError(f"Unsupported content type {ctype or 'unknown'}.")
                body = b""
                async for chunk in r.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_BYTES:
                        break
                raw = body[:MAX_BYTES].decode(r.encoding or "utf-8", errors="replace")
                if ctype == "text/plain":
                    return Page(url, url, raw.strip())
                title, text = html_to_text(raw)
                return Page(url, title or url, text)
        raise FetchError("Too many redirects.")
    except httpx.HTTPError as e:
        raise FetchError(f"Could not fetch the page ({type(e).__name__}).") from e
    finally:
        if own:
            await client.aclose()
