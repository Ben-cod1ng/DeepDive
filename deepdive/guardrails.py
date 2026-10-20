"""Deterministic boundaries; prompt classification is not an authorization mechanism."""
import ipaddress
import re
import socket
from urllib.parse import urlsplit
from .storage import digest

SECRET = re.compile(r"\b(?:rsk_|sk-)[A-Za-z0-9_\-]{12,}\b")

def redact(text):
    return SECRET.sub("[REDACTED]", text)


def anchor_quotes(quotes, passages):
    """Recover a unique original span when a model changed only whitespace.

    Words, punctuation, case and order must match literally. The recovered quote
    is an exact slice; existing validation and semantic support gates still run.
    """
    anchored, changes = dict(quotes), {}
    for pid, quote in quotes.items():
        text = passages.get(pid, {}).get("text", "")
        if not quote.strip() or quote in text:
            continue
        pattern = r"\s+".join(re.escape(word) for word in quote.split())
        matches = list(re.finditer(pattern, text))
        if len(matches) == 1:
            match = matches[0]
            anchored[pid] = text[match.start():match.end()]
            changes[pid] = {"submitted": quote, "start": match.start(), "end": match.end()}
    return anchored, changes

def validate_url(url, resolver=socket.getaddrinfo):
    if len(url) > 4096 or any(ord(c) < 32 for c in url):
        raise ValueError("Invalid URL length or control character")
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only public HTTP(S) sources are supported")
    if parsed.username or parsed.password or parsed.port not in {None, 80, 443}:
        raise ValueError("URL credentials and nonstandard ports are prohibited")
    addresses = {row[4][0] for row in resolver(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)}
    if not addresses or any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise ValueError("Source must resolve only to public addresses")
    return parsed, sorted(addresses)

def validate_claim(claim, passages):
    if not claim.passage_ids or any(pid not in passages for pid in claim.passage_ids):
        return "Claim references missing or inaccessible evidence"
    if SECRET.search(claim.text):
        return "Claim contains a credential-shaped string"
    if set(claim.quotes) != set(claim.passage_ids):
        return "Every cited passage requires an exact quotation"
    for pid, quote in claim.quotes.items():
        if not quote.strip() or quote not in passages[pid]["text"]:
            return "Candidate quotation does not exist in the cited passage"
    numbers = set(re.findall(r"(?<!\w)\d+(?:[.,]\d+)*(?:%|\b)", claim.text))
    evidence = " ".join(passages[p]["text"] for p in claim.passage_ids)
    evidence_numbers = set(re.findall(r"(?<!\w)\d+(?:[.,]\d+)*(?:%|\b)", evidence))
    if not numbers <= evidence_numbers:
        return "Numerical value absent from cited evidence; calculate derived values with the tool"
    return None

def validate_verdict(claim, verdict, passages):
    if verdict.claim_id != claim.id:
        return "Verifier returned the wrong claim ID"
    if verdict.status != "supported":
        return None
    if not verdict.quotes:
        return "Supported decision requires an exact source quotation"
    for pid, quote in verdict.quotes.items():
        if pid not in claim.passage_ids or pid not in passages or not quote.strip() or quote not in passages[pid]["text"]:
            return "Supporting quotation does not exist in the cited passage"
    return None

def scope_for(session):
    return "project:" + session["project_id"] if session.get("project_id") else "session:" + session["id"]

def cache_key(kind, **values):
    from .config import POLICY_VERSION
    return digest({"kind": kind, "policy": POLICY_VERSION, **values})
