from pathlib import Path
from deepdive.config import Settings
from deepdive.service import ResearchService
from deepdive.models import RunRequest, ReviewRequest
from .fakes import FixtureGateway, FixtureHTTP


def service(path, blocked=False):
    return ResearchService(Settings(Path(path)), gateway=FixtureGateway(), http=FixtureHTTP(blocked))


def test_full_workflow_rejects_repairs_and_exports(tmp_path):
    s = service(tmp_path)
    try:
        run = s.create_run(RunRequest(question="Study participants and follow-up", review_mode="auto"), enqueue=False)
        s.execute(run["id"])
        result = s.get_run(run["id"])
        assert result["status"] == "completed", result.get("error")
        events = s.store.events(run["id"])
        assert any(e["event"] == "repair_round" for e in events)
        assert any(e["event"] == "claim_decision" and e["status"] == "unsupported" for e in events)
        assert all(not c["missing_facets"] for c in result["coverage"])
        names = {a["name"] for a in s.export_run(run["id"])["artifacts"]}
        assert {"report.html", "report.md", "slides.pptx", "slides.html", "audit.json", "insights.png"} <= names
        report = (s.artifacts.directory(run["id"]) / "report.html").read_text(encoding="utf-8")
        assert "999 adults" not in report and "Injected unsupported fact" not in report
        assert "20 adults" in report and "12 months" in report
        assert len(s.store.list("source")) == 2
        assert s.get_run(run["id"])["calls"] <= 80
    finally:
        s.close()


def test_review_survives_restart(tmp_path):
    s = service(tmp_path)
    run = s.create_run(RunRequest(question="Study participants and follow-up"), enqueue=False)
    s.execute(run["id"])
    assert s.get_run(run["id"])["status"] == "waiting_review", s.get_run(run["id"]).get("error")
    calls = s.get_run(run["id"])["calls"]
    s.close()
    s = service(tmp_path)
    try:
        s.review(run["id"], ReviewRequest(decision="approve"), "approve-once", enqueue=False)
        s.execute(run["id"])
        assert s.get_run(run["id"])["status"] == "completed", s.get_run(run["id"]).get("error")
        assert s.get_run(run["id"])["calls"] == calls + 1
        s.review(run["id"], ReviewRequest(decision="approve"), "approve-once", enqueue=False)
        assert s.get_run(run["id"])["calls"] == calls + 1
    finally:
        s.close()


def test_blocked_sources_produce_insufficient_evidence(tmp_path):
    s = service(tmp_path, blocked=True)
    try:
        run = s.create_run(RunRequest(question="Study", review_mode="auto"), enqueue=False)
        s.execute(run["id"])
        assert s.get_run(run["id"])["status"] == "insufficient", s.get_run(run["id"]).get("error")
        assert not s.get_run(run["id"])["published_claim_ids"]
        assert all(i["missing_facets"] for i in s.get_run(run["id"])["coverage"])
    finally:
        s.close()
