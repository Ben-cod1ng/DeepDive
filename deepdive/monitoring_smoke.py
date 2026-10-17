"""Check the local Compose monitoring stack without making model requests."""
import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from .config import ROOT


ALERTS = {"DeepDiveUnavailable", "DeepDiveServerErrors", "DeepDiveQueueStalled", "DeepDiveResearchFailures"}


def check_once(client, password):
    def get(port, path, **kwargs):
        # Credentials go only to Grafana on loopback; redirects are not followed.
        auth = ("admin", password) if port == 3000 else None
        response = client.get(f"http://127.0.0.1:{port}{path}", auth=auth, **kwargs)
        response.raise_for_status()
        return response.json()

    def query(expression, via_grafana=False):
        port = 3000 if via_grafana else 9090
        prefix = "/api/datasources/proxy/uid/prometheus" if via_grafana else ""
        result = get(port, prefix + "/api/v1/query", params={"query": expression})
        if result.get("status") != "success":
            raise RuntimeError("Prometheus query failed")
        return result["data"]["result"]

    up = query('up{job="deepdive"}')
    if len(up) != 1 or float(up[0]["value"][1]) != 1:
        raise RuntimeError("Prometheus has not successfully scraped the DeepDive API")
    traffic = query('sum(deepdive_http_seconds_count{job="deepdive"})')
    if not traffic or float(traffic[0]["value"][1]) < 1:
        raise RuntimeError("Prometheus has not collected application request metrics")
    rules = get(9090, "/api/v1/rules")
    active = [rule for group in rules["data"]["groups"] if group["name"] == "deepdive"
              for rule in group["rules"]]
    if {rule["name"] for rule in active} != ALERTS or any(rule.get("health") != "ok" for rule in active):
        raise RuntimeError("The four DeepDive alert rules are not loaded and healthy")
    if get(3000, "/api/health").get("database") != "ok":
        raise RuntimeError("Grafana database is not healthy")
    if get(3000, "/api/datasources/uid/prometheus/health").get("status") != "OK":
        raise RuntimeError("Grafana cannot query its Prometheus datasource")
    dashboard = get(3000, "/api/dashboards/uid/deepdive")
    panels = dashboard["dashboard"].get("panels", [])
    if not dashboard.get("meta", {}).get("provisioned") or len(panels) != 8:
        raise RuntimeError("The provisioned eight-panel DeepDive dashboard is missing")
    for panel in panels:
        if panel.get("datasource", {}).get("uid") != "prometheus" or not panel.get("targets"):
            raise RuntimeError("A dashboard panel has no Prometheus query")
        for target in panel["targets"]:
            # Empty series are valid for research activity that has not occurred yet.
            query(target["expr"], via_grafana=True)
    proxy_up = query('up{job="deepdive"}', via_grafana=True)
    if len(proxy_up) != 1 or float(proxy_up[0]["value"][1]) != 1:
        raise RuntimeError("Grafana cannot read the API scrape through its datasource")
    return {"status": "passed", "observed_at": datetime.now(timezone.utc).isoformat(),
            "prometheus_scrape": "up", "application_request_metrics": "present",
            "healthy_alert_rules": len(active), "grafana_database": "ok",
            "grafana_datasource": "ok", "provisioned_dashboard_panels": len(panels),
            "panel_queries": "passed through Grafana", "model_requests": 0}


def wait_for_monitoring(password, timeout=120):
    deadline = time.monotonic() + timeout
    with httpx.Client(timeout=3, trust_env=False, follow_redirects=False) as client:
        while True:
            try:
                return check_once(client, password)
            except (httpx.HTTPError, RuntimeError, KeyError, TypeError, ValueError) as exc:
                # Do not print response bodies, headers, or credentials on failure.
                reason = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
                if time.monotonic() >= deadline:
                    raise RuntimeError("Monitoring did not become ready: " + reason) from None
                time.sleep(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        password = (ROOT / ".secrets" / "grafana_admin").read_text(encoding="utf-8").strip()
        if not password:
            raise RuntimeError("Grafana credential file is empty; run deepdive.ops init")
        result = wait_for_monitoring(password)
    except (OSError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from None
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
