"""Explicit offline fixtures; never used in the production gateway."""
import hashlib
import threading
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit


class FixtureHTTP:
    def __init__(self, blocked=False):
        self.calls = []
        self.blocked = blocked
        self.lock = threading.Lock()

    def get(self, url):
        with self.lock:
            self.calls.append(url)
        if self.blocked:
            raise ValueError("Fixture: blocked source")
        if "duckduckgo.com" in url:
            query = parse_qs(urlsplit(url).query).get("q", [""])[0]
            target = "https://example.org/repair" if "repair" in query else "https://example.org/study"
            body = f'<a class="result__a" href="{target}">A controlled study</a>'
        elif url.endswith("/repair"):
            body = "<html><title>Study follow-up</title><main>Follow-up lasted 12 months.</main></html>"
        else:
            body = """<html><title>Controlled study</title><main>
Study enrolled 20 adults in 2025.
The study did not measure survival.
<table><tr><th>Year</th><th>Participants (people)</th></tr><tr><td>2024</td><td>10</td></tr><tr><td>2025</td><td>20</td></tr></table>
</main></html>"""
        content = body.encode()
        return {"url": url, "body": content, "content_type": "text/html",
                "retrieved_at": time.time(), "sha256": hashlib.sha256(content).hexdigest()}


class FixtureGateway:
    def __init__(self):
        self.settings = SimpleNamespace(model="offline-fixture")
        self.roles = []
        self.lock = threading.Lock()

    def ask(self, context, role, instruction, payload, schema):
        context.reserve()
        with self.lock:
            self.roles.append(role)
        context.store.event(context.run_id, "fixture_model_response", role=role, tokens=0)
        if role == "planner":
            result = {"resolved_question": payload["question"], "time_sensitive": False, "tasks": [
                {"id": "a", "question": "Study adults", "facets": ["participants"], "queries": ["study adults"], "role": "primary"},
                {"id": "b", "question": "Study follow-up", "facets": ["duration"], "queries": ["study follow-up"], "role": "reporting"}]}
        elif role == "researcher":
            result = {"done": True, "calls": []}
        elif role == "analyst":
            claims = []
            for p in payload["passages"]:
                if "Study enrolled" in p["text"]:
                    claims.extend([
                        {"id": "valid", "task_id": "a", "text": "Study enrolled 20 adults in 2025.", "passage_ids": [p["id"]], "quotes": {p["id"]: "Study enrolled 20 adults in 2025."}},
                        {"id": "invalid", "task_id": "a", "text": "Study enrolled 999 adults in 2025.", "passage_ids": [p["id"]], "quotes": {p["id"]: "Study enrolled 20 adults in 2025."}}])
                    break
            for p in payload["passages"]:
                if "Follow-up lasted" in p["text"]:
                    claims.append({"id": "duration", "task_id": "b", "text": "Follow-up lasted 12 months.",
                                   "passage_ids": [p["id"]], "quotes": {p["id"]: "Follow-up lasted 12 months."}})
                    break
            result = {"claims": claims}
        elif role == "verifier":
            decisions = []
            for item in payload:
                c = item["claim"]
                text = " ".join(p["text"] for p in item["passages"])
                supported = c["text"] in text
                decisions.append({"claim_id": c["id"], "status": "supported" if supported else "unsupported",
                                  "reason": "Fixture literal support oracle", "quotes": c["quotes"] if supported else {}})
            result = {"decisions": decisions}
        elif role == "coverage assessor":
            items = []
            for task in payload["tasks"]:
                facet = task["facets"][0]
                phrase = "enrolled" if facet == "participants" else "Follow-up"
                ids = [c["id"] for c in payload["supported_claims"] if phrase in c["text"]]
                items.append({"task_id": task["id"], "supported_facets": {facet: ids} if ids else {},
                              "missing_facets": [] if ids else [facet], "contradictions": [],
                              "next_queries": [] if ids else ["repair follow-up duration"]})
            result = {"items": items}
        elif role == "synthesizer":
            ids = [c["id"] for c in payload["claims"]]
            result = {"title": "Fixture", "sections": [{"title": "Injected unsupported fact", "claim_ids": ids + ["invented"]}],
                      "summary_claim_ids": ids[:2]}
        elif role == "summarizer":
            result = {"claim_ids": [c["id"] for c in payload["claims"]][:2] + ["invented"]}
        else:
            raise AssertionError(role)
        return schema.model_validate(result)

    def close(self):
        pass
