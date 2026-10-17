# API and client guide

Run the backend on `http://127.0.0.1:8000`. `deepdive.client.Client` reads the URL and API token from local configuration and is used by both the CLI and Streamlit.

All endpoints except `GET /health` require `Authorization: Bearer <DEEPDIVE_API_TOKEN>` when a token is configured. The setup command generates one. Keep it out of request URLs and logs. The service is single-owner: session/project scopes organize data and are not user authorization boundaries.

## Start and review a run

Save this as a local Python script, then run it with `uv run python SCRIPT.py` from the project directory:

```python
import time
import uuid
from deepdive.client import Client

client = Client()
run = client.request(
    "POST", "/runs",
    headers={"Idempotency-Key": uuid.uuid4().hex},
    json={
        "question": "How do SQLite WAL checkpoints affect readers?",
        "source_urls": ["https://www.sqlite.org/wal.html"],
        "review_mode": "manual",
        "limits": {"subquestions": 3, "sources": 12, "repair_rounds": 1},
    },
)
print(run["id"], run["session_id"])
while run["status"] in {"queued", "running"}:
    time.sleep(3)
    run = client.request("GET", f"/runs/{run['id']}")
print(run["status"])
if run["status"] == "waiting_review":
    draft = client.request("GET", f"/runs/{run['id']}/report")
    print(draft["markdown"])
    # Inspect the draft before sending an approve/revise/stop decision.
```

To approve a reviewed draft:

```python
client.request("POST", f"/runs/{run['id']}/review",
               headers={"Idempotency-Key": uuid.uuid4().hex},
               json={"decision": "approve"})
```

Revision uses `{"decision": "revise", "feedback": "Compare the reader and writer tradeoffs."}`. Feedback is required. Stop uses `{"decision": "stop"}`. A changed revision is checked again; it cannot bypass verification. `review_mode: auto` explicitly approves without a human pause.

Reuse the same idempotency key only when retrying the same operation. A new question or decision gets a new key. Do not repeatedly submit new keys after a transport timeout; first inspect the returned or existing run.

## Endpoints

| Method and route | Purpose |
|---|---|
| `GET /health` | Liveness, configured model, whether a Mesh key is present; no live model call |
| `GET /ready` | Storage write/read check and disk-space readiness |
| `GET /metrics` | Authenticated Prometheus exposition |
| `GET /errors` | Recent sanitized error records |
| `GET, POST /sessions` | List or create sessions; creation accepts `name`, optional `project_id` |
| `GET, PATCH, DELETE /sessions/{id}` | Inspect, rename, or delete session research |
| `GET /sessions/{id}/export` | Session metadata and complete research audit JSON |
| `GET, POST /projects` | List or create explicit memory-sharing projects |
| `GET /memory` | Inspect selected session/project memory |
| `POST /memory/preferences` | Explicitly save `{"text": "..."}` |
| `PATCH, DELETE /memory/{id}` | Update an explicit preference or forget a record |
| `DELETE /memory` | Clear memory within the selected scope |
| `POST /runs` | Queue research; returns 202 and run metadata |
| `GET /runs/{id}` | Status, limits, usage, coverage, report outline and artifact metadata |
| `GET /runs/{id}/events?after=0` | Ordered audit events after a sequence number |
| `GET /runs/{id}/evidence` | Source snapshots and full extracted passages |
| `GET /runs/{id}/claims` | Candidate claims and recorded verification decisions |
| `GET /runs/{id}/report` | Rendered report Markdown |
| `GET /runs/{id}/export` | Full run audit JSON |
| `POST /runs/{id}/review` | Approve, revise, or stop a waiting review |
| `POST /runs/{id}/cancel` | Cooperative cancellation |
| `POST /runs/{id}/resume` | Explicit checkpoint recovery for interrupted/failed work |
| `GET /runs/{id}/artifacts/{artifact_id}` | Download a registered artifact |
| `GET /evaluation` | Per-run metrics and descriptive drift |
| `POST /chat` | Asynchronous compatibility wrapper over the same research service |

Memory routes require exactly one query parameter: `session_id` or `project_id`. Use the project's scope when managing shared preferences. The OpenAPI schema is available at authenticated `GET /openapi.json`; browser `/docs` is also protected by the token. For a local schema inspection, use `Client().request("GET", "/openapi.json")`.

## Status and errors

Normal progress is `queued` → `running` → `waiting_review` → `running` → `completed`. Other outcomes include `insufficient`, `limited`, `stopped`, `cancelled`, `failed`, and `interrupted`. Completed does not mean every facet was covered; inspect `coverage` and the report limitations.

Responses include an `X-Request-ID`. Validation errors use 422, missing records 404, invalid state transitions/ownership conflicts 409, missing tokens 401, rejected browser origins 403, oversized bodies 413, and body timeouts 408. Unexpected failures return a generic 500 with `error_id`; use that ID in `/errors` rather than exposing stack traces to users.

Requests are limited to 200 KB. Questions and revision feedback are bounded to 10,000 characters, and a run accepts at most ten optional source URLs. Per-run limits are documented in [architecture](architecture.md). Source URLs are validated before retrieval, including DNS and redirect checks.

## Compatibility and exports

`POST /chat` accepts `message`, optional `session_id`, and optional review fields. It returns `session_id`, `intent`, `reply`, `final_state`, `run_id`, and `status`; `final_state` is empty while research is queued. Poll the run endpoint for completion. It is not a synchronous conversational completion API.

The CLI supports research, status, events, review, cancel, resume, sessions, health, audit export, and artifact download. Detailed memory/project CRUD is available through the UI and HTTP API. All execution and review paths use the same service and durable graph.
