import json
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import httpx
import pytest
from openai import APIConnectionError
from deepdive.config import Settings, MESH_BASE_URL
from deepdive.gateway import MeshGateway, RunContext, BudgetExceeded, ContextExceeded
from deepdive.models import Limits, SummarySelection, RunRequest
from deepdive.storage import Store
from .test_workflow import service


def context(tmp_path, limit=5):
    store = Store(tmp_path)
    store.put("run", {"id": "run_test", "calls": 0, "tokens": 0}, "test")
    return store, RunContext(store, "run_test", Limits(model_calls=limit))


def test_parallel_budget_reservation_never_overspends(tmp_path):
    store, run_context = context(tmp_path)
    def attempt(_):
        try:
            run_context.reserve()
            return True
        except BudgetExceeded:
            return False
    with ThreadPoolExecutor(max_workers=8) as workers:
        assert sum(workers.map(attempt, range(20))) == 5
    assert store.get("run", "run_test")["calls"] == 5


def test_gateway_routes_mesh_and_bounds_transport_and_schema_retries(tmp_path, monkeypatch):
    store, run_context = context(tmp_path)
    responses = [APIConnectionError(request=httpx.Request("POST", MESH_BASE_URL)),
                 "invalid-json", '{"claim_ids": ["c"]}']
    captured = []
    class FakeClient:
        def __init__(self, **kwargs):
            captured.append(kwargs)
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))
        def create(self, **kwargs):
            captured.append(kwargs)
            result = responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=result))],
                                   usage=SimpleNamespace(model_dump=lambda: {"total_tokens": 7}))
        def close(self):
            pass
    monkeypatch.setattr("deepdive.gateway.OpenAI", FakeClient)
    monkeypatch.setattr("deepdive.gateway.token_count", lambda value: 100)
    monkeypatch.setattr("deepdive.gateway.time.sleep", lambda value: None)
    gateway = MeshGateway(Settings(tmp_path, mesh_key="test-only"), store)
    result = gateway.ask(run_context, "summarizer", "Select IDs", {"text": "rsk_abcdefghijklmnopqrst"}, SummarySelection)
    assert result.claim_ids == ["c"]
    assert captured[0]["base_url"] == MESH_BASE_URL and captured[0]["max_retries"] == 0
    assert store.get("run", "run_test")["calls"] == 3
    assert store.get("run", "run_test")["tokens"] == 14
    assert "rsk_abcdefghijklmnopqrst" not in json.dumps(captured[1]["messages"])
    gateway.close()


def test_context_budget_and_elapsed_limit_stop_before_request(tmp_path, monkeypatch):
    store, run_context = context(tmp_path)
    gateway = MeshGateway(Settings(tmp_path), store)
    monkeypatch.setattr("deepdive.gateway.token_count", lambda value: 1_000_000)
    with pytest.raises(ContextExceeded):
        gateway.ask(run_context, "verifier", "Check", {}, SummarySelection)
    assert store.get("run", "run_test")["calls"] == 0
    run_context.started = time.monotonic() - 1000
    with pytest.raises(BudgetExceeded):
        run_context.reserve()


def test_semantic_tampering_is_rejected_even_when_numbers_match(tmp_path):
    s = service(tmp_path)
    try:
        run = s.create_run(RunRequest(question="Study participants"), enqueue=False)
        s.execute(run["id"])
        cid = s.get_run(run["id"])["published_claim_ids"][0]
        s.store.update("claim", cid, text="The trial did not enroll 20 adults.")
        with pytest.raises(ValueError, match="changed after verification"):
            s.final_gate(run["id"])
    finally:
        s.close()


def test_deleting_session_forgets_its_preferences(tmp_path):
    s = service(tmp_path)
    try:
        session = s.create_session("Private")
        scope = "session:" + session["id"]
        s.memory.preference(scope, "An explicit personal preference")
        s.delete_session(session["id"])
        assert s.store.list("memory", scope) == []
    finally:
        s.close()
