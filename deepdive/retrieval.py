"""Bounded public retrieval. Connections pin validated IPs, preserving TLS SNI."""
import hashlib
import http.client
import ipaddress
import socket
import ssl
import threading
import time
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit
from bs4 import BeautifulSoup
from .guardrails import redact, validate_url

MAX_BYTES = 3_000_000
USER_AGENT = "DeepDiveResearch/0.1 (local research; public sources only)"


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, port, address, timeout):
        super().__init__(host, port=port, timeout=timeout, context=ssl.create_default_context())
        self.address = address

    def connect(self):
        sock = socket.create_connection((self.address, self.port), self.timeout)
        try:
            if ipaddress.ip_address(sock.getpeername()[0]) != ipaddress.ip_address(self.address):
                raise ValueError("Connection destination changed")
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except BaseException:
            sock.close()
            raise


class PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, port, address, timeout):
        super().__init__(host, port=port, timeout=timeout)
        self.address = address

    def connect(self):
        self.sock = socket.create_connection((self.address, self.port), self.timeout)
        if ipaddress.ip_address(self.sock.getpeername()[0]) != ipaddress.ip_address(self.address):
            self.sock.close()
            raise ValueError("Connection destination changed")


class PublicHTTP:
    def __init__(self):
        self.lock = threading.Lock()
        self.next_request = {}

    def get(self, url):
        for redirect in range(6):
            parsed, addresses = validate_url(url)
            host = parsed.hostname.encode("idna").decode("ascii")
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            with self.lock:
                now = time.monotonic()
                wait = max(0, self.next_request.get(host, now) - now)
                self.next_request[host] = now + wait + 0.5
            if wait:
                time.sleep(wait)
            connection_type = PinnedHTTPS if parsed.scheme == "https" else PinnedHTTP
            target = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
            for attempt in range(2):
                conn = connection_type(host, port, addresses[attempt % len(addresses)], timeout=12)
                try:
                    conn.request("GET", target, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity",
                                                          "Accept": "text/html,text/plain,application/json"})
                    response = conn.getresponse()
                    if response.status in {301, 302, 303, 307, 308}:
                        location = response.getheader("Location")
                        if not location:
                            raise ValueError("Redirect lacks a destination")
                        url = urljoin(url, location)
                        break
                    if response.status in {429, 500, 502, 503, 504} and not attempt:
                        time.sleep(0.5)
                        continue
                    if response.status != 200:
                        raise ValueError(f"Source returned HTTP {response.status}")
                    content_type = response.getheader("Content-Type", "").split(";")[0].lower()
                    if content_type not in {"text/html", "application/xhtml+xml", "text/plain", "application/json"}:
                        raise ValueError(f"Unsupported source content type: {content_type}")
                    if response.getheader("Content-Encoding", "identity").lower() != "identity":
                        raise ValueError("Compressed source was not requested")
                    length = response.getheader("Content-Length")
                    if length and int(length) > MAX_BYTES:
                        raise ValueError("Source exceeds size limit")
                    body = response.read(MAX_BYTES + 1)
                    if len(body) > MAX_BYTES:
                        raise ValueError("Source exceeds size limit")
                    return {"url": url, "body": body, "content_type": content_type,
                            "retrieved_at": time.time(), "sha256": hashlib.sha256(body).hexdigest()}
                except (OSError, http.client.HTTPException):
                    if attempt:
                        raise
                finally:
                    conn.close()
            else:
                raise ValueError("Retrieval retries exhausted")
        raise ValueError("Too many redirects")


def extract_page(response):
    body = response["body"]
    if response["content_type"] in {"text/plain", "application/json"}:
        text = body.decode("utf-8", errors="replace")
        return {"title": urlsplit(response["url"]).hostname, "text": redact(text[:120000]),
                "published_at": None, "canonical_url": response["url"], "tables": [], "media": []}
    soup = BeautifulSoup(body, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else urlsplit(response["url"]).hostname
    published = soup.select_one('meta[property="article:published_time"], meta[name="date"], time[datetime]')
    published_at = (published.get("content") or published.get("datetime")) if published else None
    canonical = soup.select_one('link[rel="canonical"]')
    canonical_url = urljoin(response["url"], canonical.get("href", "")) if canonical else response["url"]
    tables = []
    for index, table in enumerate(soup.find_all("table")[:8]):
        rows = [[redact(cell.get_text(" ", strip=True)[:500]) for cell in row.find_all(["th", "td"])[:15]]
                for row in table.find_all("tr")[:60]]
        if any(rows):
            tables.append({"index": index, "rows": rows, "caption": table.caption.get_text(" ", strip=True) if table.caption else ""})
    media = [{"alt": redact(img.get("alt", "")[:300]), "url": urljoin(response["url"], img.get("src", ""))}
             for img in soup.find_all("img")[:12]]
    for tag in soup(["script", "style", "nav", "footer", "header", "noscript", "form"]):
        tag.decompose()
    main = soup.find("main") or soup.find("article") or soup.body or soup
    text = "\n".join(line.strip() for line in main.get_text("\n", strip=True).splitlines() if line.strip())
    return {"title": redact(title[:500]), "text": redact(text[:120000]), "published_at": published_at,
            "canonical_url": canonical_url, "tables": tables, "media": media}


def split_passages(text, size=2200):
    """Bound passages at ingestion; verification always sees complete passages."""
    chunks, current = [], ""
    for line in text.splitlines():
        for start in range(0, len(line), size):
            part = line[start:start + size]
            if len(current) + len(part) + 1 > size:
                if current:
                    chunks.append(current)
                current = ""
            current = (current + "\n" + part).strip()
    if current:
        chunks.append(current)
    return chunks


def parse_search(body, base):
    soup = BeautifulSoup(body, "html.parser")
    results, seen = [], set()
    for anchor in soup.select("a.result__a, a.result-link"):
        target = urljoin(base, anchor.get("href", ""))
        wrapped = parse_qs(urlsplit(target).query).get("uddg")
        if wrapped:
            target = wrapped[0]
        if urlsplit(target).scheme not in {"http", "https"} or target in seen:
            continue
        if (urlsplit(target).hostname or "").endswith("duckduckgo.com"):
            continue
        seen.add(target)
        results.append({"url": target, "title": redact(anchor.get_text(" ", strip=True))})
    return results[:5]


def search_public(transport, query):
    failures = []
    for base in ("https://html.duckduckgo.com/html/", "https://lite.duckduckgo.com/lite/"):
        try:
            response = transport.get(base + "?" + urlencode({"q": query}))
            results = parse_search(response["body"], response["url"])
            if results:
                return results
            failures.append("No results or search challenge")
        except (ValueError, OSError, http.client.HTTPException) as exc:
            failures.append(str(exc))
    raise ValueError("Public discovery unavailable: " + "; ".join(failures))
