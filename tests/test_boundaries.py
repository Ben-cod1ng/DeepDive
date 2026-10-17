import socket
from concurrent.futures import ThreadPoolExecutor
import pytest
from fastapi.testclient import TestClient

from deepdive.api import create_app
from deepdive.config import Settings
from deepdive.guardrails import validate_url, validate_claim, validate_verdict, cache_key
from deepdive.models import Claim, Verdict, ReviewRequest, RunRequest
from deepdive.retrieval import extract_page, parse_search, split_passages
from deepdive.storage import Store
from .test_workflow import service


def resolver(ip):
    return lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]


@pytest.mark.parametrize("url,ip", [
    ("file:///etc/passwd", "8.8.8.8"), ("http://localhost", "127.0.0.1"),
    ("https://internal.example", "10.0.0.1"), ("http://metadata.example", "169.254.169.254"),
    ("http://example.org:8080", "8.8.8.8"), ("https://user:password@example.org", "8.8.8.8"),
    ("https://example.org", "::1"), ("https://example.org", "::ffff:127.0.0.1"),
])
def test_unsafe_urls_rejected(url, ip):
    with pytest.raises(ValueError):
        validate_url(url, resolver(ip))


def test_public_url_and_mixed_dns():
    parsed, addresses = validate_url("https://example.org/a", resolver("8.8.8.8"))
    assert parsed.hostname == "example.org" and addresses == ["8.8.8.8"]
    mixed = lambda *a, **k: resolver("8.8.8.8")() + resolver("127.0.0.1")()
    with pytest.raises(ValueError):
        validate_url("https://example.org", mixed)


def test_quotes_ids_numbers_and_verdicts():
    p = {"p": {"id": "p", "text": "There were 20 adults.", "source_id": "s"}}
    claim = Claim(id="c", task_id="t", text="There were 20 adults.", passage_ids=["p"], quotes={"p": "20 adults"})
    assert validate_claim(claim, p) is None
    assert validate_claim(claim.model_copy(update={"text": "There were 2 adults."}), p)
    assert validate_claim(claim.model_copy(update={"quotes": {"p": "invented"}}), p)
    assert validate_claim(claim, {})
    decision = Verdict(claim_id="c", status="supported", reason="match", quotes={"p": "20 adults"})
    assert validate_verdict(claim, decision, p) is None
    assert validate_verdict(claim, decision.model_copy(update={"quotes": {"p": "invented"}}), p)


def test_invalid_revision_and_blank_question():
    with pytest.raises(ValueError):
        ReviewRequest(decision="revise", feedback="  ")
    with pytest.raises(ValueError):
        RunRequest(question=" ")


def test_immutable_evidence_and_cache_isolation(tmp_path):
    store = Store(tmp_path)
    source = store.put("source", {"id": "s", "text": "original"}, "scope-a")
    with pytest.raises(ValueError):
        store.put("source", {**source, "text": "tampered"}, "scope-a")
    with pytest.raises(ValueError):
        store.put("source", source, "scope-b")
    assert store.get("source", "s", "scope-b") is None
    store.cache("scope-a", "key", {"answer": "a"}, 1)
    assert store.cached("scope-b", "key") is None
    store.cache("scope-a", "expired", {}, -1)
    assert store.cached("scope-a", "expired") is None
    assert cache_key("answer", question="not 2025") != cache_key("answer", question="2025")


def test_scoped_memory_edit_forget_and_secrets(tmp_path):
    s = service(tmp_path)
    try:
        a = s.create_session("a")
        b = s.create_session("b")
        scope = "session:" + a["id"]
        m = s.memory.preference(scope, "Use short explanations rsk_abcdefghijklmnopqrst")
        assert "rsk_" not in m["text"]
        assert s.memory.recall("session:" + b["id"], "short") == []
        s.store.cache(scope, "cache", {}, 30)
        s.memory.preference(scope, "Use comparisons", m["id"])
        assert s.store.cached(scope, "cache") is None
        assert s.memory.forget(scope, m["id"])
        assert s.memory.recall(scope, "comparisons") == []
    finally:
        s.close()


def test_tools_role_scope_market_and_coalescing(tmp_path):
    s = service(tmp_path)
    try:
        run = s.create_run(RunRequest(question="Study adults"), enqueue=False)
        _, context, tools = s.runtime(run["id"])
        assert "error" in tools.call("verifier", "fetch_source", {"url": "https://example.org/study"})
        assert "error" in tools.call("researcher", "fetch_market_series", {"symbol": "AAPL"})
        assert "error" in tools.call("researcher", "read_passages", {"passage_ids": ["other-scope"]})
        with ThreadPoolExecutor(3) as pool:
            responses = list(pool.map(lambda _: tools.call("researcher", "fetch_source", {"url": "https://example.org/study"}), range(3)))
        assert len({r["source_id"] for r in responses}) == 1
        assert s.http.calls.count("https://example.org/study") == 1
        assert len(s.evidence(run["id"])["sources"]) == 1
    finally:
        s.close()


def test_extract_and_search_parsers():
    page = {"url": "https://example.org", "content_type": "text/html",
            "body": b'<html><title>Title</title><script>steal()</script><main>Readable text<table><tr><th>Year</th><th>USD</th></tr><tr><td>2025</td><td>12</td></tr></table></main></html>'}
    result = extract_page(page)
    assert "steal" not in result["text"]
    assert result["tables"][0]["rows"][1] == ["2025", "12"]
    assert "".join(split_passages("a" * 5000)) == "a" * 5000
    assert all(len(p) <= 2200 for p in split_passages("a" * 5000))
    html = b'<a class="result-link" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fa">A</a>'
    assert parse_search(html, "https://lite.duckduckgo.com/lite/")[0]["url"] == "https://example.org/a"


def test_api_errors_token_and_idempotency(tmp_path):
    s = service(tmp_path)
    try:
        app = create_app(s, Settings(tmp_path, api_token="local-test"))
        with TestClient(app) as client:
            assert client.get("/health").status_code == 200
            assert client.get("/sessions").status_code == 401
            client.headers["Authorization"] = "Bearer local-test"
            assert client.get("/sessions/missing").status_code == 404
            assert client.post("/runs", json={"question": ""}).status_code == 422
            assert client.get("/sessions", headers={"Origin": "https://evil.example"}).status_code == 403
            session = client.post("/sessions", json={"name": "Session"}).json()
            s.create_run(RunRequest(question="Study", session_id=session["id"]), "same", enqueue=False)
            same = client.post("/runs", json={"question": "Study", "session_id": session["id"]}, headers={"Idempotency-Key": "same"})
            assert same.status_code == 202
            conflict = client.post("/runs", json={"question": "Different"}, headers={"Idempotency-Key": "same"})
            assert conflict.status_code == 409
            run = same.json()
            assert client.post(f"/runs/{run['id']}/review", json={"decision": "approve"}).status_code == 409
            assert client.get(f"/runs/{run['id']}/artifacts/other").status_code == 404
    finally:
        s.close()
