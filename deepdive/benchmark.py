"""Isolated, resumable research evaluations with explicit spending limits."""
import json
import re
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from .config import pipeline_fingerprint
from .evaluation import experiment_manifest, run_metrics
from .guardrails import scope_for
from .models import Limits, RunRequest
from .service import ResearchService
from .storage import digest


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def benchmark(dataset, settings, output, limits, cases=None, resume=False, ledger=None, factory=ResearchService):
    if ledger is None:
        raise ValueError("A spending ledger is required; credentials alone do not start a live benchmark")
    questions = dataset["questions"]
    by_id = {q["id"]: q for q in questions}
    if any(not re.fullmatch(r"[a-z0-9_-]{1,64}", cid) for cid in by_id):
        raise ValueError("Case IDs must be short lowercase identifiers")
    if len(by_id) != len(questions):
        raise ValueError("Benchmark question IDs must be unique")
    selected = list(dict.fromkeys(cases or by_id))
    if not selected or set(selected) - by_id.keys():
        raise ValueError("Select existing benchmark case IDs")
    limits = Limits.model_validate(limits)
    manifest = {**experiment_manifest(dataset, settings.model),
                "limits": limits.model_dump(), "input_tokens": settings.input_tokens,
                "output_tokens": settings.output_tokens, "embedding_model": settings.embedding_model,
                "embedding_revision": settings.embedding_revision}
    experiment = digest(manifest)
    output = Path(output)
    if output.exists():
        if not resume:
            raise ValueError("Output already exists; use --resume for the same experiment")
        result = json.loads(output.read_text(encoding="utf-8"))
        if result["experiment"] != experiment:
            raise ValueError("Dataset, code, model or configuration changed; choose a new output")
    else:
        result = {"mode": "live-mesh-research-benchmark", "experiment": experiment,
                  "manifest": manifest, "date": datetime.now(timezone.utc).isoformat(),
                  "pipeline": pipeline_fingerprint(), "cases": {}, "status": "running",
                  "independent_support_accuracy": None}
    # The output identifies the experiment; data is always under the evaluation root.
    data = settings.data / "evaluations" / ("benchmark_" + experiment[:24])
    if data.exists() and not output.exists():
        raise ValueError("This experiment already has data; resume its original output")
    result["data_id"] = data.name
    service = factory(replace(settings, data=data))
    service.gateway.spend_guard = ledger
    try:
        for case_id in selected:
            item = by_id[case_id]
            entry = result["cases"].get(case_id)
            if entry and entry["status"] in {"completed", "failed", "limited", "insufficient", "cancelled", "stopped"}:
                continue
            if entry is None:
                session = service.create_session("Benchmark: " + case_id)
                documents = [service.documents.ingest(scope_for(session), doc["name"], doc["text"].encode())
                             for doc in item.get("documents", [])]
                entry = {"session_id": session["id"], "document_ids": [d["id"] for d in documents],
                         "run_ids": [], "metrics": [], "status": "running",
                         "split": item.get("split", "unspecified"),
                         "reference_facets": item.get("reference_facets", []),
                         "review_status": "independent human review pending"}
                result["cases"][case_id] = entry
                save_json(output, result)
            turns = item.get("turns") or [{"question": item["question"]}]
            for index, turn in enumerate(turns):
                if index < len(entry["run_ids"]):
                    run = service.get_run(entry["run_ids"][index])
                else:
                    request = RunRequest(question=turn["question"], session_id=entry["session_id"],
                                         source_urls=turn.get("source_urls", item.get("source_urls", [])),
                                         document_ids=entry["document_ids"],
                                         research_mode=item.get("research_mode", "web"), review_mode="auto", limits=limits)
                    run = service.create_run(request, idempotency_key=f"{experiment}:{case_id}:{index}", enqueue=False)
                    entry["run_ids"].append(run["id"])
                    save_json(output, result)
                if run["status"] in {"queued", "running", "interrupted"}:
                    service.execute(run["id"])
                run = service.get_run(run["id"])
                metrics = run_metrics(service, run["id"])
                if index < len(entry["metrics"]):
                    entry["metrics"][index] = metrics
                else:
                    entry["metrics"].append(metrics)
                details = service.export_run(run["id"])
                save_json(data / (case_id + f"-turn-{index + 1}.json"), details)
                sources = service.evidence(run["id"])["sources"]
                entry.setdefault("source_snapshots_by_turn", {})[str(index + 1)] = [
                    {"id": s["id"], "url": s.get("final_url"), "content_hash": s.get("content_hash"),
                     "retrieved_at": s.get("retrieved_at")} for s in sources]
                entry["status"] = run["status"] if run["status"] != "completed" or index == len(turns) - 1 else "running"
                result["spending"] = ledger.summary()
                save_json(output, result)
                if run["status"] != "completed":
                    break
            if entry["status"] == "limited":
                break
        result["not_run"] = [cid for cid in by_id if cid not in result["cases"]]
        result["status"] = "complete" if not result["not_run"] and all(
            c["status"] in {"completed", "insufficient"} for c in result["cases"].values()) else "partial"
        result["spending"] = ledger.summary()
        save_json(output, result)
        return result
    finally:
        service.close()
