import pytest
from deepdive.models import RunRequest, ReviewRequest
from .test_workflow import service


def test_revise_uses_same_graph_and_new_claim_version(tmp_path):
    s = service(tmp_path)
    try:
        run = s.create_run(RunRequest(question="Study participants"), enqueue=False)
        s.execute(run["id"])
        s.review(run["id"], ReviewRequest(decision="revise", feedback="Include follow-up duration"), enqueue=False)
        s.execute(run["id"])
        assert s.get_run(run["id"])["status"] == "waiting_review", s.get_run(run["id"]).get("error")
        assert s.get_run(run["id"])["version"] == 2
        assert {c["version"] for c in s.claims(run["id"])["claims"]} == {1, 2}
        s.review(run["id"], ReviewRequest(decision="stop"), enqueue=False)
        s.execute(run["id"])
        assert s.get_run(run["id"])["status"] == "stopped"
        assert not s.export_run(run["id"])["artifacts"]
    finally:
        s.close()


def test_cancel_queued_and_delete_session(tmp_path):
    s = service(tmp_path)
    try:
        run = s.create_run(RunRequest(question="Study"), enqueue=False)
        s.cancel(run["id"])
        s.execute(run["id"])
        assert s.get_run(run["id"])["status"] == "cancelled"
        s.delete_session(run["session_id"])
        with pytest.raises(KeyError):
            s.get_run(run["id"])
    finally:
        s.close()


def test_exact_context_report_cache_and_refresh(tmp_path):
    s = service(tmp_path)
    try:
        session = s.create_session("repeated")
        req = RunRequest(question="Study adults", session_id=session["id"], review_mode="auto")
        # Queued requests share identical context. A completed turn changes context and must miss.
        first = s.create_run(req, enqueue=False)
        second = s.create_run(req, enqueue=False)
        s.execute(first["id"]); s.execute(second["id"])
        assert s.get_run(second["id"])["cached_from"] == first["id"]
        assert s.get_run(second["id"])["calls"] == 0
        assert s.get_run(second["id"])["status"] == "completed"
        third = s.create_run(req.model_copy(update={"refresh": True}), enqueue=False)
        s.execute(third["id"])
        assert not s.get_run(third["id"]).get("cached_from")
        assert s.get_run(third["id"])["calls"] > 0
        with pytest.raises(KeyError):
            artifact = s.export_run(first["id"])["artifacts"][0]
            s.artifacts.path(second["id"], artifact["id"])
    finally:
        s.close()


def test_project_memory_promotes_only_verified_claims(tmp_path):
    s = service(tmp_path)
    try:
        project = s.create_project("Research")
        run = s.create_run(RunRequest(question="Study", project_id=project["id"], review_mode="auto"), enqueue=False)
        s.execute(run["id"])
        memory = s.store.list("memory", "project:" + project["id"])
        assert len(memory) == 1 and memory[0]["kind"] == "research"
        assert "999" not in memory[0]["text"] and memory[0]["source_ids"]
        session = s.create_session("follow-up", project["id"])
        context = s.memory.context(session, "Study")
        assert context["research_memory"]
        isolated = s.create_session("isolated")
        assert not s.memory.context(isolated, "Study")["research_memory"]
    finally:
        s.close()


def test_publication_rejects_tampered_claim(tmp_path):
    s = service(tmp_path)
    try:
        run = s.create_run(RunRequest(question="Study"), enqueue=False)
        s.execute(run["id"])
        cid = s.get_run(run["id"])["published_claim_ids"][0]
        s.store.update("claim", cid, text="Invented 99999 participants.")
        with pytest.raises(ValueError):
            s.final_gate(run["id"])
    finally:
        s.close()
