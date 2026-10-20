from types import SimpleNamespace
import json
import math
import numpy as np
import pytest

from deepdive.config import Settings, MESH_BASE_URL
from deepdive.gateway import MeshGateway
from deepdive.guardrails import scope_for
from deepdive.models import RunRequest
from deepdive.rag import fuse, normalized
from deepdive.rag_evaluation import rank_metrics, passage_labels, averages
from deepdive.service import ResearchService
from .test_documents import DocumentGateway
from .test_gateway import context


def test_cosine_and_reciprocal_rank_fusion():
    assert fuse(["a", "b"], ["b", "c"]) == ["b", "a", "c"]
    values = normalized([[3, 4], [0, 2]])
    np.testing.assert_allclose(np.linalg.norm(values, axis=1), [1, 1])
    assert values.dtype == np.dtype("<f4")
    for invalid in [[[0, 0]], [[float("nan"), 1]], [[1, 2], [3]], [[float("inf"), 0]]]:
        with pytest.raises(ValueError):
            normalized(invalid)


def test_retrieval_metrics_deduplicate_documents_and_penalize_missing_evidence():
    scores = rank_metrics(["irrelevant", "a", "a", "b"], ["a", "b"])
    assert scores["recall_at_3"] == 1 and scores["mrr_at_3"] == .5
    assert 0 < scores["ndcg_at_3"] < 1
    assert rank_metrics(["irrelevant"] * 3, ["a"])["recall_at_3"] == 0
    assert rank_metrics(["a"], ["a"])["ndcg_at_3"] == 1


def test_hybrid_semantic_candidate_scope_cache_and_profile(tmp_path):
    gateway = DocumentGateway()
    s = ResearchService(Settings(tmp_path), gateway=gateway)
    try:
        session = s.create_session("Selected library")
        scope = scope_for(session)
        relevant = s.documents.ingest(scope, "transport.txt", b"An automobile is a road vehicle.")
        lexical = s.documents.ingest(scope, "car.txt", b"Car is the name of a fictional moon.")
        secret = s.documents.ingest(scope, "unselected.txt", b"Car automobile vehicle secret information.")
        run = s.create_run(RunRequest(question="car", session_id=session["id"],
                research_mode="documents", document_ids=[relevant["id"], lexical["id"]]), enqueue=False)
        s.rag.index(run["id"])
        first = s.rag.search(run["id"], "car", 5)
        assert {p["source_id"] for p in first} == {relevant["source_id"], lexical["source_id"]}
        assert secret["source_id"] not in {p["source_id"] for p in first}
        event = [e for e in s.store.events(run["id"]) if e["event"] == "document_retrieval"][-1]
        assert event["lexical_count"] == 1 and event["dense_count"] == 2 and event["reranked"]
        assert any(pid.startswith(relevant["source_id"]) for pid in event["dense_ids"])
        calls = s.get_run(run["id"])["calls"]
        assert s.rag.search(run["id"], "car", 5) == first
        assert s.get_run(run["id"])["calls"] == calls
        s.rag.index(run["id"])
        assert s.get_run(run["id"])["calls"] == calls
        s.settings.embedding_revision = "2"
        s.rag.index(run["id"])
        assert s.get_run(run["id"])["calls"] == calls + 1
        assert all("secret information" not in t for t in gateway.embedded)
    finally:
        s.close()


def test_rerank_reorders_only_existing_candidates_and_reports_fallback(tmp_path):
    gateway = DocumentGateway()
    s = ResearchService(Settings(tmp_path), gateway=gateway)
    try:
        session = s.create_session("Ranks")
        doc = s.documents.ingest(scope_for(session), "two.md", b"# Cats\nCats eat fish.\n# Dogs\nDogs eat meat.")
        run = s.create_run(RunRequest(question="food", session_id=session["id"],
                research_mode="documents", document_ids=[doc["id"]]), enqueue=False)
        s.rag.index(run["id"])
        original = gateway.ask
        def reverse(context, role, instruction, payload, schema):
            if role == "document reranker":
                context.reserve()
                return schema(passage_ids=[p["id"] for p in reversed(payload["passages"])])
            return original(context, role, instruction, payload, schema)
        gateway.ask = reverse
        result = s.rag.search(run["id"], "food")
        event = [e for e in s.store.events(run["id"]) if e["event"] == "document_retrieval"][-1]
        assert [p["id"] for p in result] == list(reversed(event["fused_ids"]))
        def bad(context, role, instruction, payload, schema):
            return schema(passage_ids=["invented"])
        gateway.ask = bad
        result = s.rag.search(run["id"], "another question")
        assert result and all(p["id"] != "invented" for p in result)
        event = [e for e in s.store.events(run["id"]) if e["event"] == "document_retrieval"][-1]
        assert event["reranked"] is False
        assert event["fallback_reason"] == "invalid_permutation"
        assert any(e["event"] == "document_rerank_fallback" for e in s.store.events(run["id"]))
    finally:
        s.close()


def test_passage_labels_require_exact_eligible_evidence_and_absence_is_undefined():
    passages = [{"id": "p1", "source_id": "s1", "text": "One writer at a time."},
                {"id": "p2", "source_id": "s2", "text": "One writer at a time."}]
    question = {"id": "q", "relevant_passages": [{"document": "wal.md", "quote": "One writer"}]}
    assert passage_labels(question, passages, {"s1": "wal.md", "s2": "draft.md"}) == ["p1"]
    with pytest.raises(ValueError, match="chunk boundary"):
        passage_labels(question, passages, {"s1": "other.md", "s2": "draft.md"})
    assert all(v is None for v in rank_metrics(["p1"], []).values())
    rows = [{"passage_metrics": {stage: rank_metrics(["p1"], []) for stage in ("bm25", "dense", "hybrid", "final")}}]
    assert averages(rows, "passage_metrics")["final"]["recall_at_3"] is None


def test_frozen_public_corpus_reference_spans_survive_chunking():
    from deepdive.config import ROOT
    from deepdive.rag_evaluation import audit_corpus
    dataset = json.loads((ROOT / "evaluation/documents.v2.json").read_text(encoding="utf-8"))
    result = audit_corpus(dataset)
    assert result["ai_calls"] == 0 and result["exact_offsets"]
    assert result["reference_chunks"]["absent"] == []
    assert len(result["reference_chunks"]["conflicting"]) >= 2


def test_document_calculations_remain_available_to_analysis(tmp_path):
    s = ResearchService(Settings(tmp_path), gateway=DocumentGateway())
    try:
        session = s.create_session("Calculations")
        scope = scope_for(session)
        doc = s.documents.ingest(scope, "values.txt", b"Enrollment rose from 10 people to 20 people.")
        run = s.create_run(RunRequest(question="Calculate enrollment growth", session_id=session["id"],
                research_mode="documents", document_ids=[doc["id"]]), enqueue=False)
        _, _, tools = s.runtime(run["id"])
        tools._attach(s.store.get("source", doc["source_id"], scope))
        s.rag.index(run["id"])
        p = s.rag.passages(s.get_run(run["id"]))[0]
        calculation = tools.call("researcher", "calculate", {"operation": "percent_change", "inputs": [
            {"passage_id": p["id"], "value": 10, "literal": "10"},
            {"passage_id": p["id"], "value": 20, "literal": "20"}]})
        assert "error" not in calculation
        passages = s.relevant_passages(run["id"], "enrollment growth")
        assert any(p["kind"] == "calculation" for p in passages)
        assert any(p["source_id"] == doc["source_id"] for p in passages)
    finally:
        s.close()


@pytest.mark.parametrize("vectors,indexes", [([[0., 0.]], [0]), ([[math.nan, 1.]], [0]),
        ([[1., 0.], [1.]], [0, 1]), ([[1., 0.], [1., 0.]], [0, 0])])
def test_embedding_gateway_rejects_invalid_responses(tmp_path, monkeypatch, vectors, indexes):
    store, run_context = context(tmp_path)
    gateway = MeshGateway(Settings(tmp_path, mesh_key="fixture"), store)
    response = SimpleNamespace(data=[SimpleNamespace(index=i, embedding=v) for i, v in zip(indexes, vectors)], usage=None)
    monkeypatch.setattr(gateway, "client", lambda: SimpleNamespace(embeddings=SimpleNamespace(create=lambda **kwargs: response)))
    with pytest.raises(ValueError):
        gateway.embed(run_context, ["text"] * len(vectors))
    assert store.get("run", "run_test")["calls"] == 1


def test_embedding_gateway_uses_mesh_redacts_and_accounts_usage(tmp_path, monkeypatch):
    store, run_context = context(tmp_path)
    captured = []
    class Client:
        def __init__(self, **kwargs):
            captured.append(kwargs)
            self.embeddings = SimpleNamespace(create=self.create)
        def create(self, **kwargs):
            captured.append(kwargs)
            return SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[1., 0.])],
                                   usage=SimpleNamespace(model_dump=lambda: {"total_tokens": 9}))
        def close(self):
            pass
    monkeypatch.setattr("deepdive.gateway.OpenAI", Client)
    gateway = MeshGateway(Settings(tmp_path, mesh_key="fixture"), store)
    assert gateway.embed(run_context, ["rsk_abcdefghijklmnopqrst"])[0] == [1., 0.]
    assert captured[0]["base_url"] == MESH_BASE_URL and captured[0]["max_retries"] == 0
    assert captured[1]["model"] == "openai/text-embedding-3-small"
    assert "rsk_abcdefghijklmnopqrst" not in json.dumps(captured[1])
    assert store.get("run", "run_test")["tokens"] == 9
    assert any(e.get("role") == "embedding" for e in store.events("run_test"))
