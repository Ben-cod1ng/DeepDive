"""Local FastAPI interface. Importing this module never initializes an AI client."""
from contextlib import asynccontextmanager
import asyncio
import hmac
import shutil
import sqlite3
import time
import uuid
from typing import Literal
from fastapi import FastAPI, Header, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware
from pydantic import Field
from .config import Settings
from .guardrails import scope_for
from .models import (StrictModel, RunRequest, ReviewRequest, Named, Preference, SessionRequest)
from .service import ResearchService, Conflict
from .observability import REQUEST_ID, ENTRY_POINT


class ChatRequest(StrictModel):
    message: str = Field(min_length=1, max_length=10000)
    session_id: str | None = None
    run_id: str | None = None
    auto_human_decision: Literal["approve", "revise", "stop"] = "approve"
    human_feedback: str = Field("", max_length=10000)


def create_app(service=None, settings=None):
    settings = settings or Settings.load()

    @asynccontextmanager
    async def lifespan(app):
        app.state.service = service or ResearchService(settings)
        yield
        if service is None:
            app.state.service.close()

    app = FastAPI(title="DeepDive", version="0.1.0", lifespan=lifespan,
                  description="Local, source-verifying research. Live model calls use Mesh.")
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver", "api"])

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        origin = request.headers.get("origin")
        if origin and origin not in {"http://127.0.0.1:8501", "http://localhost:8501", "http://127.0.0.1:8000", "http://localhost:8000"}:
            return JSONResponse({"detail": "Untrusted browser origin"}, 403)
        if request.url.path != "/health" and settings.api_token:
            token = request.headers.get("authorization", "").removeprefix("Bearer ")
            if not hmac.compare_digest(token.encode(), settings.api_token.encode()):
                return JSONResponse({"detail": "Local API token required"}, 401)
        try:
            length = int(request.headers.get("content-length", "0"))
        except ValueError:
            return JSONResponse({"detail": "Invalid Content-Length"}, 400)
        if length < 0 or length > 200000:
            return JSONResponse({"detail": "Request body exceeds 200 KB"}, 413)
        async def bounded_body():
            content = bytearray()
            async for chunk in request.stream():
                content.extend(chunk)
                if len(content) > 200000:
                    return None
            return bytes(content)
        try:
            body = await asyncio.wait_for(bounded_body(), timeout=15)
        except asyncio.TimeoutError:
            return JSONResponse({"detail": "Request body timed out"}, 408)
        if body is None:
            return JSONResponse({"detail": "Request body exceeds 200 KB"}, 413)
        request._body = body
        return await call_next(request)

    @app.middleware("http")
    async def diagnostics(request: Request, call_next):
        request_id = uuid.uuid4().hex
        request_token = REQUEST_ID.set(request_id)
        client = request.headers.get("x-deepdive-client", "api")
        entry_token = ENTRY_POINT.set(client if client in {"api", "cli", "streamlit", "evaluation"} else "api")
        started = time.monotonic()
        try:
            try:
                response = await call_next(request)
            except Exception as exc:
                error_id = app.state.service.observability.error(exc)
                response = JSONResponse({"detail": "Internal error", "error_id": error_id}, 500)
            route = getattr(request.scope.get("route"), "path", "unmatched")
            method = request.method if request.method in {"GET", "POST", "PATCH", "DELETE", "HEAD", "OPTIONS", "PUT"} else "OTHER"
            app.state.service.observability.http.labels(method, route, str(response.status_code // 100) + "xx").observe(time.monotonic() - started)
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response
        finally:
            REQUEST_ID.reset(request_token)
            ENTRY_POINT.reset(entry_token)

    @app.exception_handler(KeyError)
    async def not_found(request, exc):
        return JSONResponse({"detail": "Record not found"}, 404)

    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, 409)

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, 422)

    def s():
        return app.state.service

    def scope(session_id=None, project_id=None):
        if bool(session_id) == bool(project_id):
            raise ValueError("Supply exactly one session_id or project_id")
        if session_id:
            return scope_for(s().session(session_id))
        if not s().store.get("project", project_id):
            raise KeyError(project_id)
        return "project:" + project_id

    @app.get("/health")
    def health():
        return {"status": "ok", "mesh_configured": bool(settings.mesh_key), "model": settings.model}

    @app.get("/ready")
    def ready():
        try:
            with s().store.connection() as db:
                db.execute("SELECT 1").fetchone()
            # A small atomic write exercises the actual persistent mount.
            probe = settings.data / (".ready-" + uuid.uuid4().hex)
            try:
                probe.write_bytes(b"ready")
            finally:
                probe.unlink(missing_ok=True)
            if shutil.disk_usage(settings.data).free < 100_000_000:
                return JSONResponse({"status": "unavailable", "reason": "low_disk"}, 503)
        except (OSError, sqlite3.Error):
            return JSONResponse({"status": "unavailable", "reason": "storage"}, 503)
        return {"status": "ready", "mesh_configured": bool(settings.mesh_key)}

    @app.get("/metrics", include_in_schema=False)
    def metrics():
        return Response(s().observability.metrics(), media_type="text/plain; version=0.0.4")

    @app.get("/errors")
    def errors():
        return {"errors": s().store.list("error", "operations")[-100:]}

    @app.get("/sessions")
    def sessions():
        return {"sessions": s().store.list("session")}

    @app.post("/sessions", status_code=201)
    def new_session(body: SessionRequest):
        return s().create_session(body.name, body.project_id)

    @app.get("/sessions/{session_id}")
    def session(session_id: str):
        result = s().export_session(session_id)
        return {**result["session"], "turns": [r["run"] for r in result["runs"]]}

    @app.patch("/sessions/{session_id}")
    def rename_session(session_id: str, body: Named):
        s().session(session_id)
        return s().store.update("session", session_id, name=body.name)

    @app.delete("/sessions/{session_id}")
    def delete_session(session_id: str):
        s().delete_session(session_id)
        return {"deleted": True}

    @app.get("/sessions/{session_id}/export")
    def export_session(session_id: str):
        return s().export_session(session_id)

    @app.get("/projects")
    def projects():
        return {"projects": s().store.list("project")}

    @app.post("/projects", status_code=201)
    def create_project(body: Named):
        return s().create_project(body.name)

    @app.get("/memory")
    def memory(session_id: str | None = None, project_id: str | None = None):
        return {"items": s().store.list("memory", scope(session_id, project_id))}

    @app.post("/memory/preferences", status_code=201)
    def remember(body: Preference, session_id: str | None = None, project_id: str | None = None):
        return s().memory.preference(scope(session_id, project_id), body.text)

    @app.patch("/memory/{memory_id}")
    def edit_preference(memory_id: str, body: Preference, session_id: str | None = None, project_id: str | None = None):
        return s().memory.preference(scope(session_id, project_id), body.text, memory_id)

    @app.delete("/memory/{memory_id}")
    def forget(memory_id: str, session_id: str | None = None, project_id: str | None = None):
        if not s().memory.forget(scope(session_id, project_id), memory_id):
            raise KeyError(memory_id)
        return {"deleted": True}

    @app.delete("/memory")
    def clear_memory(session_id: str | None = None, project_id: str | None = None):
        selected = scope(session_id, project_id)
        for item in s().store.list("memory", selected):
            s().memory.forget(selected, item["id"])
        return {"deleted": True}

    @app.post("/runs", status_code=202)
    def run(body: RunRequest, idempotency_key: str | None = Header(None, max_length=128)):
        return s().create_run(body, idempotency_key)

    @app.get("/runs/{run_id}")
    def get_run(run_id: str):
        result = s().get_run(run_id)
        artifacts = [a for a in s().store.list("artifact", result["scope"]) if a["run_id"] == run_id]
        return {**result, "artifacts": artifacts}

    @app.get("/runs/{run_id}/events")
    def events(run_id: str, after: int = Query(0, ge=0)):
        s().get_run(run_id)
        return {"events": s().store.events(run_id, after)}

    @app.get("/runs/{run_id}/evidence")
    def evidence(run_id: str):
        return s().evidence(run_id)

    @app.get("/runs/{run_id}/claims")
    def claims(run_id: str):
        return s().claims(run_id)

    @app.get("/runs/{run_id}/export")
    def export_run(run_id: str):
        return s().export_run(run_id)

    @app.post("/runs/{run_id}/review", status_code=202)
    def review(run_id: str, body: ReviewRequest, idempotency_key: str | None = Header(None, max_length=128)):
        return s().review(run_id, body, idempotency_key)

    @app.post("/runs/{run_id}/cancel")
    def cancel(run_id: str):
        return s().cancel(run_id)

    @app.post("/runs/{run_id}/resume", status_code=202)
    def resume(run_id: str):
        return s().resume(run_id)

    @app.get("/runs/{run_id}/artifacts/{artifact_id}")
    def artifact(run_id: str, artifact_id: str):
        path = s().artifacts.path(run_id, artifact_id)
        return FileResponse(path, filename=path.name, headers={"X-Content-Type-Options": "nosniff",
                             "Content-Security-Policy": "default-src 'none'; img-src data:; style-src 'unsafe-inline'"})

    @app.get("/runs/{run_id}/report")
    def report(run_id: str):
        run = s().get_run(run_id)
        if not run.get("outline"):
            raise Conflict("Report is not available yet")
        return {"markdown": s().artifacts.render(run_id)[1]}

    @app.get("/evaluation")
    def evaluation():
        from .evaluation import run_metrics, drift
        rows = [run_metrics(s(), r["id"]) for r in s().store.list("run")
                if r.get("status") in {"completed", "insufficient"}]
        return {"runs": rows, "drift": drift(rows)}

    @app.post("/chat", status_code=202)
    def chat(body: ChatRequest):
        if body.run_id:
            run = s().review(body.run_id, ReviewRequest(decision=body.auto_human_decision, feedback=body.human_feedback))
        else:
            run = s().create_run(RunRequest(question=body.message, session_id=body.session_id,
                         review_mode="auto" if body.auto_human_decision == "approve" else "manual"))
        return {"session_id": run["session_id"], "intent": "research", "reply": "Research queued.",
                "final_state": {}, "run_id": run["id"], "status": run["status"]}

    return app


app = create_app()
