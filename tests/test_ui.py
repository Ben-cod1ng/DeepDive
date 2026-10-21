from fastapi.testclient import TestClient
import json
import pytest
from streamlit.testing.v1 import AppTest
from deepdive.api import create_app
from deepdive.models import RunRequest
from .test_workflow import service
from .test_documents import DocumentGateway
from deepdive.config import Settings
from deepdive.guardrails import scope_for
from deepdive.service import ResearchService
from .fakes import FixtureHTTP


@pytest.mark.parametrize("with_documents", [False, True])
def test_ui_review_and_artifacts_share_the_api(tmp_path, monkeypatch, with_documents):
    s = ResearchService(Settings(tmp_path), gateway=DocumentGateway(), http=FixtureHTTP()) if with_documents else service(tmp_path)
    try:
        options = {}
        if with_documents:
            session = s.create_session("Document UI")
            doc = s.documents.ingest(scope_for(session), "study.txt", b"Study enrolled 20 adults in 2025. Follow-up lasted 12 months.")
            options = {"session_id": session["id"], "document_ids": [doc["id"]], "research_mode": "documents"}
        run = s.create_run(RunRequest(question="Study participants and follow-up", **options), enqueue=False)
        s.execute(run["id"])
        with TestClient(create_app(s, s.settings)) as api:
            def request(self, method, path, **kwargs):
                response = api.request(method, path, **kwargs)
                response.raise_for_status()
                return response.json()
            def download(self, run_id, artifact_id):
                response = api.get(f"/runs/{run_id}/artifacts/{artifact_id}")
                response.raise_for_status()
                return response.content
            monkeypatch.setattr("deepdive.client.Client.request", request)
            monkeypatch.setattr("deepdive.client.Client.download", download)
            app = AppTest.from_file("streamlit_app.py", default_timeout=30)
            app.session_state["active_session"] = run["session_id"]
            app.session_state["active_run"] = run["id"]
            app.run()
            assert not app.exception
            assert any("Inspect evidence" in e.label for e in app.expander)
            assert any("Complete cited passage" in w.value for w in app.markdown)
            assert not any('<a id="ref-' in w.value for w in app.markdown)
            assert next(w for w in app.selectbox if w.label == "Research sources").value == ("documents" if with_documents else "web")
            buttons = [b for b in app.button if b.label == "Approve and generate exports"]
            assert len(buttons) == 1
            buttons[0].click().run()
            s.futures[run["id"]].result(timeout=45)
            app.run()
            assert not app.exception
            assert s.get_run(run["id"])["status"] == "completed"
            assert s.export_run(run["id"])["artifacts"]
            if not with_documents:
                # A saved example remains inspectable even with the API unavailable.
                folder = tmp_path / "evaluation" / "examples"
                folder.mkdir(parents=True)
                (folder / "index.json").write_text(json.dumps([{"file": "fixture.json", "title": "Recorded fixture"}]))
                example = {**s.export_run(run["id"]), "title": "Recorded fixture", "recorded_at": "test",
                           "summary": "Explicit synthetic regression fixture", "limitations": [], "report_markdown": "Report"}
                (folder / "fixture.json").write_text(json.dumps(example))
                monkeypatch.setattr("deepdive.config.ROOT", tmp_path)
                def unavailable(*args, **kwargs):
                    raise AssertionError("Recorded examples must not contact the API")
                monkeypatch.setattr("deepdive.client.Client.request", unavailable)
                gallery = AppTest.from_file("streamlit_app.py", default_timeout=30)
                gallery.session_state["recorded_investigation"] = "fixture.json"
                gallery.run()
                assert not gallery.exception
                assert any("Inspect evidence" in e.label for e in gallery.expander)
    finally:
        s.close()
