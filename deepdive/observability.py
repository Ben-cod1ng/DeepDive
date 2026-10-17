"""Local JSON diagnostics, bounded metrics, and optional sanitized Sentry errors."""
import json
import logging
import os
import time
import traceback
import uuid
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from .guardrails import redact
from .storage import digest

REQUEST_ID = ContextVar("deepdive_request_id", default=None)
ENTRY_POINT = ContextVar("deepdive_entry_point", default="service")


class JsonFormatter(logging.Formatter):
    def format(self, record):
        payload = {"time": time.time(), "level": record.levelname.lower(),
                   "event": getattr(record, "event", record.getMessage()),
                   "request_id": getattr(record, "request_id", REQUEST_ID.get()),
                   "entry_point": getattr(record, "entry_point", ENTRY_POINT.get())}
        payload.update(getattr(record, "fields", {}))
        return redact(json.dumps(payload, default=str))


def sanitize_sentry(event, hint):
    # Explicit allowlist. No request bodies, headers, user info, prompts, locals, or source passages.
    exceptions = []
    for item in event.get("exception", {}).get("values", []):
        frames = [{k: f[k] for k in ("filename", "function", "lineno", "module") if k in f}
                  for f in item.get("stacktrace", {}).get("frames", [])]
        exceptions.append({"type": item.get("type"), "value": "Details retained only in local research audit",
                           "stacktrace": {"frames": frames}})
    return {"event_id": event.get("event_id"), "timestamp": event.get("timestamp"),
            "level": "error", "platform": "python", "release": event.get("release"),
            "exception": {"values": exceptions}}


class Observability:
    def __init__(self, store):
        self.store = store
        self.registry = CollectorRegistry()
        self.http = Histogram("deepdive_http_seconds", "HTTP request latency", ["method", "route", "status_class"],
                              buckets=(.01, .05, .1, .5, 1, 5, 15, 60), registry=self.registry)
        self.events = Counter("deepdive_events_total", "Research event counts", ["event", "outcome"], registry=self.registry)
        self.dependencies = Histogram("deepdive_dependency_seconds", "Model and tool latency", ["kind", "name"],
                                      buckets=(.1, .5, 1, 5, 15, 30, 60, 120), registry=self.registry)
        self.runs = Gauge("deepdive_runs", "Persisted runs by status", ["status"], registry=self.registry)
        self.queue_age = Gauge("deepdive_oldest_queued_seconds", "Age of oldest queued run", registry=self.registry)
        self.model_calls = Gauge("deepdive_model_calls", "Persisted model attempts", registry=self.registry)
        self.tokens = Gauge("deepdive_model_tokens", "Persisted billed tokens where reported", registry=self.registry)
        self.logger = logging.getLogger("deepdive." + digest(str(store.path.resolve()))[:12])
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        self.logger.handlers.clear()
        formatter = JsonFormatter()
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        logs = store.data / "logs"; logs.mkdir(exist_ok=True)
        rotating = RotatingFileHandler(logs / "service.jsonl", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        rotating.setFormatter(formatter)
        self.logger.addHandler(stream); self.logger.addHandler(rotating)
        self.sentry = None
        if os.getenv("SENTRY_DSN"):
            import sentry_sdk
            sentry_sdk.init(dsn=os.environ["SENTRY_DSN"], release=os.getenv("DEEPDIVE_RELEASE", "local"),
                            default_integrations=False, send_default_pii=False, before_send=sanitize_sentry)
            self.sentry = sentry_sdk

    def emit(self, event, run_id=None, **fields):
        allowed = {"role", "tool", "status", "outcome", "seconds", "round", "error_type", "stage", "cache", "claim_count"}
        safe = {k: v for k, v in fields.items() if k in allowed}
        outcome = str(fields.get("outcome", fields.get("status", "ok")))
        if outcome not in {"ok", "error", "supported", "unsupported", "contradicted", "insufficient", "completed", "failed", "blocked"}:
            outcome = "other"
        self.events.labels(event=event, outcome=outcome).inc()
        if event == "model_response":
            self.dependencies.labels("model", fields.get("role", "unknown")).observe(fields.get("seconds", 0))
        elif event == "tool_result":
            self.dependencies.labels("tool", fields.get("tool", "unknown")).observe(fields.get("seconds", 0))
        run = self.store.get("run", run_id) if run_id else None
        self.logger.info(event, extra={"event": event, "fields": {"run_id": run_id, **safe},
                         "request_id": (run or {}).get("request_id", REQUEST_ID.get()),
                         "entry_point": (run or {}).get("entry_point", ENTRY_POINT.get())})

    def error(self, exc, run_id=None, request_id=None):
        error_id = "error_" + uuid.uuid4().hex[:20]
        frames = [{"file": f.filename.rsplit(os.sep, 1)[-1], "function": f.name, "line": f.lineno}
                  for f in traceback.extract_tb(exc.__traceback__)]
        record = {"id": error_id, "run_id": run_id, "request_id": request_id or REQUEST_ID.get(),
                  "type": type(exc).__name__, "fingerprint": digest([type(exc).__name__, frames]),
                  "frames": frames, "message": "Inspect the local run audit for details"}
        self.store.put("error", record, "operations")
        self.logger.error("error", extra={"event": "error", "fields": record})
        if self.sentry:
            self.sentry.capture_exception(exc)
        return error_id

    def metrics(self):
        rows = [r for r in self.store.list("run") if "status" in r]
        statuses = ["queued", "running", "waiting_review", "completed", "insufficient", "failed", "interrupted", "limited", "cancelled", "stopped"]
        for status in statuses:
            self.runs.labels(status=status).set(sum(r["status"] == status for r in rows))
        queued = [r["created"] for r in rows if r["status"] == "queued"]
        self.queue_age.set(time.time() - min(queued) if queued else 0)
        self.model_calls.set(sum(r.get("calls", 0) for r in rows))
        self.tokens.set(sum(r.get("tokens", 0) for r in rows))
        return generate_latest(self.registry)

    def close(self):
        for handler in self.logger.handlers[:]:
            handler.close()
            self.logger.removeHandler(handler)
        if self.sentry:
            self.sentry.flush(timeout=2)
