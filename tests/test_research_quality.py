import json
from pathlib import Path
from deepdive.config import Settings
from deepdive.models import RunRequest
from deepdive.presentation import report_sections, reference_numbers, revision_changes
from deepdive.service import ResearchService
from .fakes import FixtureGateway, FixtureHTTP


class InitiallyEmptyHTTP(FixtureHTTP):
    def get(self, url):
        if "duckduckgo.com" in url and "repair" not in url:
            raise ValueError("Fixture: discovery initially empty")
        return super().get(url)


def test_empty_first_round_repairs_with_new_query_and_keeps_audit(tmp_path):
    s = ResearchService(Settings(Path(tmp_path)), gateway=FixtureGateway(), http=InitiallyEmptyHTTP())
    try:
        run = s.create_run(RunRequest(question="Study participants and follow-up", review_mode="auto"), enqueue=False)
        s.execute(run["id"])
        run = s.get_run(run["id"])
        assert run["status"] == "completed", run.get("error")
        events = s.store.events(run["id"])
        rounds = [e for e in events if e["event"] == "round_assessed"]
        assert rounds[0]["source_ids"] == []
        assert rounds[1]["source_ids"] and rounds[1]["supported_claim_ids"]
        steps = [e for e in events if e["event"] == "research_next_step"]
        assert steps[0]["decision"] == "repair"
        assert steps[-1]["reason"] == "no_untried_queries"
        assert any(item["missing_facets"] for item in run["coverage"])
        sections = report_sections(run, {c["id"]: c for c in s.claims(run["id"])["claims"]})
        assert [f["text"] for section in sections for f in section["findings"]] == ["Follow-up lasted 12 months."]
    finally:
        s.close()


def test_repeated_failed_queries_are_normalized_and_not_repaired_forever(tmp_path):
    class RepeatingGateway(FixtureGateway):
        def ask(self, context, role, instruction, payload, schema):
            value = super().ask(context, role, instruction, payload, schema)
            if role == "coverage assessor":
                for item in value.items:
                    item.next_queries = ["  STUDY   ADULTS  "]
            return value
    s = ResearchService(Settings(tmp_path), gateway=RepeatingGateway(), http=FixtureHTTP(blocked=True))
    try:
        run = s.create_run(RunRequest(question="Study", review_mode="auto"), enqueue=False)
        s.execute(run["id"])
        assert s.get_run(run["id"])["status"] == "insufficient"
        events = s.store.events(run["id"])
        assert not any(e["event"] == "repair_round" for e in events)
        assert any(e.get("reason") == "no_untried_queries" for e in events)
    finally:
        s.close()


def test_display_groups_exact_duplicates_but_preserves_qualifications_and_citations():
    claims = {
        "a": {"id": "a", "text": "20 adults.", "task_id": "t1", "passage_ids": ["p1"]},
        "b": {"id": "b", "text": "20 adults.", "task_id": "t2", "passage_ids": ["p2"]},
        "c": {"id": "c", "text": "20 adults in 2025.", "task_id": "t2", "passage_ids": ["p3"]},
        "d": {"id": "d", "text": "Not 20 adults.", "task_id": "t2", "passage_ids": ["p4"]},
    }
    original = json.dumps(claims, sort_keys=True)
    run = {"published_claim_ids": list(claims), "plan": {"tasks": [
        {"id": "t1", "question": "Who participated?"}, {"id": "t2", "question": "What changed?"}]},
        "outline": {"sections": [{"claim_ids": ["a", "a", "b"]}, {"claim_ids": ["b", "c", "d"]}]}}
    sections = report_sections(run, claims)
    findings = [f for section in sections for f in section["findings"]]
    assert len(findings) == 3
    assert findings[0]["claim_ids"] == ["a", "b"]
    assert findings[0]["passage_ids"] == ["p1", "p2"]
    assert reference_numbers(sections) == {"p1": 1, "p2": 2, "p3": 3, "p4": 4}
    assert sections[0]["title"] == "Research question: Who participated?"
    assert json.dumps(claims, sort_keys=True) == original
    revised = {**claims, "new-a": {**claims["a"], "id": "new-a"}}
    changes = revision_changes(["a", "d"], ["new-a", "c"], revised)
    assert changes == {"retained": [{"previous_ids": ["a"], "current_ids": ["new-a"]}],
                       "added": ["c"], "removed": ["d"]}
