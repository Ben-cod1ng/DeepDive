"""Single local execution owner shared by API, Streamlit, and CLI."""
import json
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from .artifacts import Artifacts
from .config import POLICY_VERSION, Settings, pipeline_fingerprint
from .gateway import MeshGateway, RunContext, BudgetExceeded, Cancelled
from .guardrails import cache_key, redact, scope_for, validate_claim, validate_verdict
from .memory import Memory
from .models import Claim, Limits, ReviewRequest, RunRequest, Verdict
from .observability import Observability, REQUEST_ID, ENTRY_POINT
from .storage import Store, digest, uid
from .tools import ToolBox
from .workflow import Workflow


class Conflict(ValueError):
    pass


class ResearchService:
    def __init__(self, settings=None, gateway=None, http=None):
        self.settings = settings or Settings.load()
        self.store = Store(self.settings.data)
        self._ownership = (self.settings.data / "service.lock").open("a+b")
        self._ownership.seek(0)
        if os.name == "nt":
            import msvcrt
            if self._ownership.read(1) == b"":
                self._ownership.write(b"0")
                self._ownership.flush()
            self._ownership.seek(0)
            try:
                msvcrt.locking(self._ownership.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                self._ownership.close()
                raise RuntimeError("Another backend owns this data directory. Use one Uvicorn worker.")
        else:
            import fcntl
            fcntl.flock(self._ownership, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.observability = Observability(self.store)
        self.store.observer = self.observability
        self.memory = Memory(self.store)
        self.gateway = gateway or MeshGateway(self.settings, self.store)
        self.http = http
        self.contexts, self.toolboxes = {}, {}
        self.mutation = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="deepdive-run")
        self.futures = {}
        self.checkpoint_db = sqlite3.connect(self.settings.data / "checkpoints.sqlite3", check_same_thread=False)
        self.checkpoint_db.execute("PRAGMA journal_mode=WAL")
        self.saver = SqliteSaver(self.checkpoint_db)
        self.artifacts = Artifacts(self)
        self.workflow = Workflow(self, self.saver)
        for run in self.store.list("run"):
            if run.get("status") in {"running", "queued"}:
                self.store.update("run", run["id"], status="interrupted",
                                  error="Backend restarted. Resume explicitly; unfinished external calls may repeat.")
                self.store.event(run["id"], "recovery_required")

    def get_run(self, run_id):
        run = self.store.get("run", run_id)
        if not run:
            raise KeyError(run_id)
        return run

    def session(self, session_id):
        value = self.store.get("session", session_id)
        if not value:
            raise KeyError(session_id)
        return value

    def create_session(self, name, project_id=None):
        if project_id and not self.store.get("project", project_id):
            raise KeyError(project_id)
        item = {"id": uid("session"), "name": name.strip(), "project_id": project_id}
        return self.store.put("session", item, scope_for(item))

    def create_project(self, name):
        item = {"id": uid("project"), "name": name.strip()}
        return self.store.put("project", item, "project:" + item["id"])

    def create_run(self, request, idempotency_key=None, enqueue=True):
        request = RunRequest.model_validate(request)
        if idempotency_key and len(idempotency_key) > 128:
            raise ValueError("Idempotency key exceeds 128 characters")
        raw = json.loads(redact(request.model_dump_json()))
        request_hash = digest(raw)
        with self.mutation:
            opid = "operation_" + digest(["create", idempotency_key]) if idempotency_key else None
            prior = self.store.get("operation", opid) if opid else None
            if prior:
                if prior["request_hash"] != request_hash:
                    raise Conflict("Idempotency key already used for different input")
                return self.get_run(prior["run_id"])
            if len([r for r in self.store.list("run") if r.get("status") in {"queued", "running"}]) >= 8:
                raise Conflict("Local run queue is full")
            session = self.session(request.session_id) if request.session_id else self.create_session(raw["question"][:80], request.project_id)
            if request.project_id and request.project_id != session.get("project_id"):
                raise ValueError("Session belongs to a different project")
            scope = scope_for(session)
            run = {"id": uid("run"), "session_id": session["id"], "project_id": session.get("project_id"),
                   "scope": scope, "request": raw, "status": "queued", "stage": "queued",
                   "calls": 0, "tokens": 0, "active_seconds": 0, "source_ids": [], "visited_urls": [],
                   "version": 1, "feedback": [], "coverage": [], "published_claim_ids": [],
                   "policy": POLICY_VERSION, "model": self.settings.model,
                   "pipeline": pipeline_fingerprint(),
                   "request_id": REQUEST_ID.get(), "entry_point": ENTRY_POINT.get(),
                   "context": self.memory.context(session, raw["question"])}
            run["report_cache_key"] = cache_key("report", scope=scope, question=raw["question"],
                context=run["context"], sources=raw["source_urls"], limits=raw["limits"],
                preferences=self.memory.fingerprint(scope), model=self.settings.model, pipeline=run["pipeline"])
            self.store.put("run", run, scope)
            if opid:
                self.store.put("operation", {"id": opid, "request_hash": request_hash, "run_id": run["id"]}, scope)
            self.store.event(run["id"], "run_created", limits=raw["limits"], review_mode=raw["review_mode"])
            if enqueue:
                self._submit(run["id"])
            return self.get_run(run["id"])

    def _submit(self, run_id):
        self.futures[run_id] = self.executor.submit(self.execute, run_id)

    def runtime(self, run_id):
        with self.mutation:
            run = self.get_run(run_id)
            if run_id not in self.contexts:
                self.contexts[run_id] = RunContext(self.store, run_id, Limits.model_validate(run["request"]["limits"]))
                self.toolboxes[run_id] = ToolBox(self.store, self.memory, self.contexts[run_id], run, self.http)
            return run, self.contexts[run_id], self.toolboxes[run_id]

    def execute(self, run_id):
        run, context, _ = self.runtime(run_id)
        request_token = REQUEST_ID.set(run.get("request_id"))
        entry_token = ENTRY_POINT.set(run.get("entry_point", "service"))
        config = {"configurable": {"thread_id": run_id}, "max_concurrency": 3, "recursion_limit": 120}
        try:
            if not (run.get("pending_review") and run["pending_review"]["decision"]["decision"] in {"approve", "stop"}):
                context.check()
            self.store.update("run", run_id, status="running", error=None)
            snapshot = self.workflow.graph.get_state(config)
            if not snapshot.values:
                initial = {"run_id": run_id, "version": 1, "round": 0, "claims": [],
                           "supported": [], "feedback": [], "results": {}}
                self.workflow.graph.invoke(initial, config)
            else:
                pending = run.get("pending_review")
                has_interrupt = any(t.interrupts for t in snapshot.tasks)
                same_version = pending and pending["version"] == snapshot.values.get("version", 1)
                command = Command(resume=pending["decision"]) if has_interrupt and same_version else None
                self.store.event(run_id, "checkpoint_resume", may_repeat_external_calls=True)
                self.workflow.graph.invoke(command, config)
            snapshot = self.workflow.graph.get_state(config)
            if any(t.interrupts for t in snapshot.tasks):
                self.store.update("run", run_id, status="waiting_review", stage="review", pending_review=None)
            elif self.get_run(run_id)["status"] not in {"completed", "insufficient", "stopped"}:
                raise RuntimeError("Graph ended without a terminal publication or stop")
        except Cancelled:
            self.store.update("run", run_id, status="cancelled", stage="cancelled")
            self.store.event(run_id, "cancelled")
        except BudgetExceeded as exc:
            self.store.update("run", run_id, status="limited", error=str(exc))
            self.store.event(run_id, "budget_exhausted", error=str(exc))
        except Exception as exc:
            error_id = self.observability.error(exc, run_id)
            self.store.update("run", run_id, status="failed", error=redact(str(exc))[:2000], error_id=error_id)
            self.store.event(run_id, "run_failed", error=redact(str(exc))[:2000], error_type=type(exc).__name__)
        finally:
            self.store.update("run", run_id, active_seconds=context.elapsed())
            with self.mutation:
                self.contexts.pop(run_id, None)
                self.toolboxes.pop(run_id, None)
            REQUEST_ID.reset(request_token)
            ENTRY_POINT.reset(entry_token)

    def review(self, run_id, request, idempotency_key=None, enqueue=True):
        decision = ReviewRequest.model_validate(request)
        with self.mutation:
            run = self.get_run(run_id)
            opid = "operation_" + digest(["review", run_id, idempotency_key]) if idempotency_key else None
            prior = self.store.get("operation", opid) if opid else None
            request_hash = digest(decision.model_dump())
            if prior:
                if prior["request_hash"] != request_hash:
                    raise Conflict("Review idempotency key already used for another decision")
                return run
            if run["status"] != "waiting_review":
                raise Conflict("Run is not waiting for review")
            if decision.decision == "revise":
                limits = Limits.model_validate(run["request"]["limits"])
                if run["calls"] >= limits.model_calls or run["active_seconds"] >= limits.active_seconds:
                    raise Conflict("Revision budget exhausted. Start a follow-up run.")
            self.store.update("run", run_id, status="queued",
                              pending_review={"version": run["version"], "decision": decision.model_dump()})
            if opid:
                self.store.put("operation", {"id": opid, "request_hash": request_hash, "run_id": run_id}, run["scope"])
            if enqueue:
                self._submit(run_id)
            return self.get_run(run_id)

    def cancel(self, run_id):
        with self.mutation:
            run = self.get_run(run_id)
            if run["status"] in {"completed", "insufficient", "stopped", "cancelled"}:
                raise Conflict("Run is already terminal")
            waiting = run["status"] in {"waiting_review", "interrupted", "failed", "limited"}
            return self.store.update("run", run_id, cancel_requested=True,
                                     status="cancelled" if waiting else run["status"])

    def resume(self, run_id):
        with self.mutation:
            run = self.get_run(run_id)
            if run["status"] not in {"failed", "interrupted"}:
                raise Conflict("Only failed or interrupted runs can resume")
            self.store.update("run", run_id, status="queued", cancel_requested=False)
            self._submit(run_id)
            return self.get_run(run_id)

    def relevant_passages(self, run_id, query, limit=18):
        run = self.get_run(run_id)
        ids = set(run.get("source_ids", []))
        fresh = {sid for sid in ids if (s := self.store.get("source", sid, run["scope"])) and s["fresh_until"] > time.time()}
        ranked = self.store.search(run["scope"], query, limit=150)
        eligible = [p for p in ranked if p["source_id"] in fresh]
        if not eligible:
            eligible = [p for p in self.store.list("passage", run["scope"]) if p["source_id"] in fresh]
        selected, counts = [], {}
        for p in eligible:
            count = counts.get(p["source_id"], 0)
            if count < 3:
                selected.append(p)
                counts[p["source_id"]] = count + 1
            if len(selected) >= limit:
                break
        return selected

    def evidence_inventory(self, run_id, query):
        return [{"id": p["id"], "source_id": p["source_id"], "excerpt_for_discovery": p["text"][:350]}
                for p in self.relevant_passages(run_id, query)]

    @staticmethod
    def tool_digest(items):
        return [redact(json.dumps(i, ensure_ascii=False))[:2000] for i in items]

    def evidence(self, run_id):
        run = self.get_run(run_id)
        sources = [self.store.get("source", sid, run["scope"]) for sid in run.get("source_ids", [])]
        return {"sources": [s for s in sources if s],
                "passages": [p for p in self.store.list("passage", run["scope"]) if p["source_id"] in run.get("source_ids", [])]}

    def claims(self, run_id):
        run = self.get_run(run_id)
        return {kind + "s": [r for r in self.store.list(kind, run["scope"]) if r["run_id"] == run_id]
                for kind in ("claim", "decision")}

    def final_gate(self, run_id):
        run = self.get_run(run_id)
        records = self.claims(run_id)
        latest = {}
        for d in records["decisions"]:
            latest[d["claim_id"]] = d
        passages = {p["id"]: p for p in self.evidence(run_id)["passages"]}
        for cid in run.get("published_claim_ids", []):
            raw = self.store.get("claim", cid, run["scope"])
            if not raw or raw["run_id"] != run_id:
                raise ValueError("Publication contains an inaccessible claim")
            claim = Claim.model_validate({k: raw[k] for k in Claim.model_fields if k in raw})
            d = latest.get(cid)
            if not d or d["status"] != "supported":
                raise ValueError("Publication contains a claim without a passing decision")
            if d.get("claim_hash") != digest(claim.model_dump()):
                raise ValueError("Claim changed after verification")
            verdict = Verdict.model_validate({k: d[k] for k in Verdict.model_fields if k in d})
            error = validate_claim(claim, passages) or validate_verdict(claim, verdict, passages)
            if error:
                raise ValueError(error)
        self.store.event(run_id, "publication_gate_passed", claim_count=len(run.get("published_claim_ids", [])))

    def publish(self, state):
        run_id = state["run_id"]
        if self.get_run(run_id).get("cancel_requested"):
            raise Cancelled("Run cancelled before publication")
        self.final_gate(run_id)
        run = self.get_run(run_id)
        self.store.update("run", run_id, summary_ids=state["summary"])
        self.artifacts.publish(run_id, state["charts"])
        run = self.get_run(run_id)
        claims = [self.store.get("claim", c, run["scope"]) for c in run["published_claim_ids"]]
        status = "completed" if claims else "insufficient"
        self.store.update("run", run_id, status=status, stage="finished", completed_at=time.time(), pending_review=None)
        self.memory.promote(self.get_run(run_id), claims, self.evidence(run_id)["sources"])
        self.store.event(run_id, "run_finished", status=status, supported=len(claims),
                         completeness="partial" if any(c["missing_facets"] for c in run["coverage"]) else "covered")
        sources = self.evidence(run_id)["sources"]
        ttl = min(3600, min((s["fresh_until"] - time.time() for s in sources), default=0))
        if claims and ttl > 0:
            self.store.cache(run["scope"], run["report_cache_key"], {"run_id": run_id}, ttl)
        # Capture final status and completion events in the downloadable audit.
        self.artifacts.save(self.get_run(run_id), "audit.json", json.dumps(self.export_run(run_id), indent=2), "audit", list(run["published_claim_ids"]))

    def restore_cached_report(self, run_id):
        run = self.get_run(run_id)
        if run["request"]["refresh"]:
            return None
        hit = self.store.cached(run["scope"], run["report_cache_key"])
        if not hit:
            return None
        original = self.store.get("run", hit["run_id"], run["scope"])
        if not original or original["status"] != "completed":
            return None
        sources = [self.store.get("source", sid, run["scope"]) for sid in original["source_ids"]]
        if any(not source or source["fresh_until"] <= time.time() for source in sources):
            return None
        mapping = {cid: "claim_" + digest([run_id, cid])[:24] for cid in original["published_claim_ids"]}
        decisions = {d["claim_id"]: d for d in self.claims(original["id"])["decisions"]}
        for old, new in mapping.items():
            claim = self.store.get("claim", old, run["scope"])
            cloned = {**claim, "id": new, "run_id": run_id, "reused_from": old}
            self.store.put("claim", cloned, run["scope"])
            claim_hash = digest(Claim.model_validate({k: cloned[k] for k in Claim.model_fields if k in cloned}).model_dump())
            decision = decisions[old]
            self.store.put("decision", {**decision, "id": "decision_" + digest([run_id, old])[:24],
                                       "claim_id": new, "run_id": run_id, "claim_hash": claim_hash, "reused_from": decision["id"]}, run["scope"])
        outline = {**original["outline"], "sections": [{**section, "claim_ids": [mapping[c] for c in section["claim_ids"]]}
                    for section in original["outline"]["sections"]],
                   "summary_claim_ids": [mapping[c] for c in original["outline"]["summary_claim_ids"]]}
        coverage = [{**item, "supported_facets": {f: [mapping[c] for c in ids if c in mapping]
                                                  for f, ids in item["supported_facets"].items()}}
                    for item in original["coverage"]]
        self.store.update("run", run_id, source_ids=original["source_ids"], plan=original["plan"], outline=outline,
                          published_claim_ids=list(mapping.values()), supported_claim_ids=list(mapping.values()), coverage=coverage,
                          cached_from=original["id"])
        self.store.event(run_id, "cache_hit", cache="report", original_run=original["id"])
        self.final_gate(run_id)
        return {"cached_report": True, "plan": original["plan"], "outline": outline,
                "supported": list(mapping.values()), "claims": list(mapping.values()), "coverage": coverage}

    def export_run(self, run_id):
        return {"run": self.get_run(run_id), **self.evidence(run_id), **self.claims(run_id),
                "events": self.store.events(run_id),
                "artifacts": [a for a in self.store.list("artifact") if a["run_id"] == run_id]}

    def export_session(self, session_id):
        session = self.session(session_id)
        return {"session": session, "runs": [self.export_run(r["id"]) for r in self.store.list("run", scope_for(session))
                                            if r["session_id"] == session_id]}

    def delete_session(self, session_id):
        with self.mutation:
            session = self.session(session_id)
            scope = scope_for(session)
            runs = [r for r in self.store.list("run", scope) if r["session_id"] == session_id]
            if any(r["status"] in {"queued", "running"} for r in runs):
                raise Conflict("Cancel active runs and wait before deleting their session")
            run_ids = {r["id"] for r in runs}
            remaining = [r for r in self.store.list("run", scope) if r["id"] not in run_ids]
            retained_sources = {sid for r in remaining for sid in r.get("source_ids", [])}
            # Shared project snapshots stay available to other reports. Remove only
            # unreferenced evidence, including evidence whose original owner was deleted.
            owned_sources = {s["id"] for s in self.store.list("source", scope) if s["id"] not in retained_sources}
            for r in runs:
                self.saver.delete_thread(r["id"])
                self.artifacts.delete_run(r["id"])
                self.store.delete_events(r["id"])
            for kind in ["memory", "artifact", "decision", "claim", "operation", "run"]:
                for item in self.store.list(kind, scope):
                    if item.get("run_id", item["id"] if kind == "run" else None) in run_ids:
                        self.store.delete(kind, item["id"], scope)
                    elif kind == "memory" and not session.get("project_id"):
                        self.store.delete(kind, item["id"], scope)
            for error in self.store.list("error", "operations"):
                if error.get("run_id") in run_ids:
                    self.store.delete("error", error["id"], "operations")
            for p in self.store.list("passage", scope):
                if p["source_id"] in owned_sources:
                    self.store.delete("passage", p["id"], scope)
            for sid in owned_sources:
                self.store.delete("source", sid, scope)
            self.store.delete("session", session_id, scope)
            self.store.clear_cache(scope)

    def close(self):
        for run_id, future in list(self.futures.items()):
            if not future.done():
                self.store.update("run", run_id, cancel_requested=True)
        self.executor.shutdown(wait=True)
        self.gateway.close()
        self.checkpoint_db.close()
        self.store.observer = None
        self.observability.close()
        self._ownership.close()
