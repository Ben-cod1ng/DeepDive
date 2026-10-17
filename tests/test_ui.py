from fastapi.testclient import TestClient
from streamlit.testing.v1 import AppTest
from deepdive.api import create_app
from deepdive.models import RunRequest
from .test_workflow import service


def test_ui_review_and_artifacts_share_the_api(tmp_path, monkeypatch):
    s = service(tmp_path)
    try:
        run = s.create_run(RunRequest(question="Study participants and follow-up"), enqueue=False)
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
            buttons = [b for b in app.button if b.label == "Approve and generate exports"]
            assert len(buttons) == 1
            buttons[0].click().run()
            s.futures[run["id"]].result(timeout=45)
            app.run()
            assert not app.exception
            assert s.get_run(run["id"])["status"] == "completed"
            assert s.export_run(run["id"])["artifacts"]
    finally:
        s.close()
