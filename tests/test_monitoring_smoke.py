import json
from pathlib import Path

import httpx
import pytest
from deepdive.monitoring_smoke import ALERTS, check_once


@pytest.mark.parametrize("failure", [None, "scrape", "metrics", "rule", "datasource", "dashboard", "query"])
def test_monitoring_requires_the_complete_telemetry_path(failure):
    dashboard = json.loads(Path("monitoring/grafana/dashboards/deepdive.json").read_text())
    queries = []

    def respond(request):
        if request.url.port == 3000:
            assert request.headers.get("Authorization", "").startswith("Basic ")
        else:
            assert "Authorization" not in request.headers
        path = request.url.path
        if path.endswith("/api/v1/query"):
            expression = request.url.params["query"]
            queries.append((request.url.port, expression))
            if failure == "query" and request.url.port == 3000:
                return httpx.Response(400, json={"error": "invalid query"})
            value = "1"
            if failure == "scrape" and expression.startswith("up{"):
                value = "0"
            series = [{"value": [1, value]}]
            if failure == "metrics" and expression.startswith("sum(deepdive_http"):
                series = []
            data = {"status": "success", "data": {"result": series}}
        elif path == "/api/v1/rules":
            rules = [{"name": name, "health": "err" if failure == "rule" else "ok"} for name in ALERTS]
            data = {"data": {"groups": [{"name": "deepdive", "rules": rules}]}}
        elif path == "/api/health":
            data = {"database": "ok"}
        elif path.endswith("/health"):
            data = {"status": "ERROR" if failure == "datasource" else "OK"}
        elif path == "/api/dashboards/uid/deepdive":
            data = {"dashboard": dashboard, "meta": {"provisioned": failure != "dashboard"}}
        else:
            pytest.fail("Unexpected monitoring request")
        return httpx.Response(200, json=data)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        if failure:
            with pytest.raises((RuntimeError, httpx.HTTPError)):
                check_once(client, "test-password")
        else:
            result = check_once(client, "test-password")
            assert result["status"] == "passed" and result["model_requests"] == 0
            assert sum(port == 3000 for port, _ in queries) == 9
            assert "test-password" not in json.dumps(result)
