"""Explicit preferences and provenance-linked project research, with scoped recall."""
import time
from .config import POLICY_VERSION
from .guardrails import scope_for, redact
from .storage import digest, uid


class Memory:
    def __init__(self, store):
        self.store = store

    def recall(self, scope, query):
        matches = self.store.search(scope, query, kind="memory", limit=16)
        # FTS relevance is primary; freshness breaks ties within a small candidate set.
        return sorted(matches, key=lambda x: (x.get("kind") == "preference",
                                               x.get("fresh_until", 0) > time.time(),
                                               x.get("created", 0)), reverse=True)[:8]

    def context(self, session, question):
        scope = scope_for(session)
        runs = [r for r in self.store.list("run", scope)
                if r["session_id"] == session["id"] and r.get("status") in {"completed", "insufficient", "stopped"}]
        recent = [{"run_id": r["id"], "question": r["request"]["question"][:2500], "status": r["status"],
                   "approved_claim_ids": r.get("published_claim_ids", []),
                   "feedback": [f[:1500] for f in r.get("feedback", [])[-2:]]} for r in runs[-3:]]
        # Extractive compaction keeps links and user constraints. It never invents facts.
        older = [{"run_id": r["id"], "question": r["request"]["question"][:500],
                  "feedback_index": [f[:300] for f in r.get("feedback", [])[-2:]]} for r in runs[:-3][-12:]]
        preferences = [m for m in self.store.list("memory", scope) if m["kind"] == "preference"]
        recalled = [{**m, "text": m["text"][:1200], "is_excerpt": len(m["text"]) > 1200} for m in self.recall(scope, question)]
        return {"recent_turns": recent, "older_turn_index": older,
                "preferences": preferences[-8:], "omitted_preference_ids": [p["id"] for p in preferences[:-8]], "research_memory": recalled,
                "notice": "Memory is context, not evidence. Fetch stale sources and verify claims again."}

    def preference(self, scope, text, memory_id=None):
        if not text.strip():
            raise ValueError("Preference must contain text")
        item = {"id": memory_id or uid("memory"), "kind": "preference", "text": redact(text),
                "explicit": True, "updated": time.time()}
        if memory_id:
            existing = self.store.get("memory", memory_id, scope)
            if not existing or existing["kind"] != "preference":
                raise KeyError(memory_id)
            item["created"] = existing["created"]
        self.store.clear_cache(scope)
        return self.store.put("memory", item, scope)

    def promote(self, run, claims, sources):
        if not run.get("project_id") or not claims:
            return
        # Stable ID makes checkpoint replay idempotent.
        record = {"id": "memory_" + digest([run["id"], run.get("version", 1)])[:20],
                  "kind": "research", "run_id": run["id"], "session_id": run["session_id"],
                  "text": "\n".join(c["text"] for c in claims),
                  "claim_ids": [c["id"] for c in claims],
                  "source_ids": [s["id"] for s in sources],
                  "source_urls": [s["final_url"] for s in sources if s.get("final_url")],
                  "fresh_until": min((s["fresh_until"] for s in sources), default=0),
                  "limitations": run.get("coverage", []), "policy": POLICY_VERSION}
        self.store.put("memory", record, run["scope"])

    def fingerprint(self, scope):
        return digest([(m["id"], m.get("updated"), m["text"]) for m in self.store.list("memory", scope)])

    def forget(self, scope, memory_id):
        return self.store.delete("memory", memory_id, scope)
