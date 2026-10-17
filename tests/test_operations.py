import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from deepdive.api import create_app
from deepdive.config import Settings
from deepdive.evaluation import quality_gate
from deepdive.models import RunRequest, ReviewRequest
from deepdive.observability import sanitize_sentry
from deepdive.ops import initialize, backup, restore
from .test_workflow import service


def test_api_diagnostics_body_limits_and_privacy(tmp_path):
    s = service(tmp_path)
    try:
        with TestClient(create_app(s, Settings(tmp_path, api_token="test-token"))) as client:
            assert client.get("/metrics").status_code == 401
            client.headers["Authorization"] = "Bearer test-token"
            assert client.get("/ready").json()["status"] == "ready"
            response = client.post("/sessions", json={"name": "private research"})
            assert response.status_code == 201 and len(response.headers["X-Request-ID"]) == 32
            assert client.post("/sessions", content="x", headers={"Content-Length": "invalid"}).status_code == 400
            assert client.post("/sessions", content=iter([b"x" * 100001, b"x" * 100000])).status_code == 413
            metrics = client.get("/metrics").text
            assert 'route="/sessions"' in metrics and "private research" not in metrics
            error_id = s.observability.error(ValueError("private prompt and rsk_supersecret"))
            record = client.get("/errors").json()["errors"][0]
            assert record["id"] == error_id and "private prompt" not in json.dumps(record)
        logs = (tmp_path / "logs" / "service.jsonl").read_text()
        assert "rsk_supersecret" not in logs and "private prompt" not in logs
        event = sanitize_sentry({"request": {"headers": {"authorization": "secret"}},
                   "exception": {"values": [{"type": "ValueError", "value": "secret"}]}}, {})
        assert "secret" not in json.dumps(event)
    finally:
        s.close()


def test_setup_preserves_credentials_and_does_not_print_them(tmp_path, capsys):
    (tmp_path / ".env.example").write_text("MESH_API_KEY=\nDEEPDIVE_API_TOKEN=\n")
    initialize(tmp_path)
    first = (tmp_path / ".secrets" / "api_token").read_text()
    initialize(tmp_path)
    assert (tmp_path / ".secrets" / "api_token").read_text() == first
    assert first not in capsys.readouterr().out


def test_backup_requires_stopped_owner_and_restores_review(tmp_path):
    data, restored = tmp_path / "original", tmp_path / "restored"
    s = service(data)
    run = s.create_run(RunRequest(question="Study participants"), enqueue=False)
    s.execute(run["id"])
    archive = tmp_path / "backup.zip"
    with pytest.raises(OSError):
        backup(data, archive)
    s.close()
    backup(data, archive)
    restore(archive, restored)
    with pytest.raises(ValueError):
        restore(archive, restored)
    recovered = service(restored)
    try:
        assert recovered.get_run(run["id"])["status"] == "waiting_review"
        recovered.review(run["id"], ReviewRequest(decision="approve"), enqueue=False)
        recovered.execute(run["id"])
        assert recovered.get_run(run["id"])["status"] == "completed"
        assert recovered.export_run(run["id"])["artifacts"]
    finally:
        recovered.close()


def test_quality_gate_refuses_fixture_results_and_unreviewed_labels():
    dataset = json.loads(Path("evaluation/claims.v1.json").read_text())
    result = quality_gate({"mode": "offline-deterministic-gates", "precision": 1, "recall": 1, "count": 32}, dataset)
    assert not result["passed"]
    assert any("Independent" in reason for reason in result["failures"])
    assert any("live Mesh" in reason for reason in result["failures"])
