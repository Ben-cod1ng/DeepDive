"""Reproducible metrics, labeled verifier evaluation, calibration, and drift."""
import argparse
import csv
import json
import statistics
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import Settings, POLICY_VERSION
from .gateway import RunContext, MeshGateway
from .guardrails import validate_claim
from .models import Claim, Limits
from .storage import Store, uid, digest
from .verification import verify_claims


def run_metrics(service, run_id):
    run = service.get_run(run_id)
    records = service.claims(run_id)
    latest = {d["claim_id"]: d for d in records["decisions"]}
    accepted = sum(d["status"] == "supported" for d in latest.values())
    count = len(records["claims"])
    coverage = run.get("coverage", [])
    covered = sum(len(i["supported_facets"]) for i in coverage)
    required = covered + sum(len(i["missing_facets"]) for i in coverage)
    evidence = {p["id"] for p in service.evidence(run_id)["passages"]}
    published = [c for c in records["claims"] if c["id"] in run.get("published_claim_ids", [])]
    citations = [pid for c in published for pid in c["passage_ids"]]
    valid = sum(p in evidence for p in citations)
    events = service.store.events(run_id)
    acceptance = accepted / count if count else 0
    facet_coverage = covered / required if required else 0
    rounds = [e for e in events if e["event"] == "round_assessed"]
    round_coverage = [{"round": e["round"], "version": e.get("version"),
                       "supported_facets": sum(len(c["supported_facets"]) for c in e["coverage"]),
                       "missing_facets": sum(len(c["missing_facets"]) for c in e["coverage"])} for e in rounds]
    return {"run_id": run_id, "status": run["status"], "model": run["model"], "claims": count,
            "published": len(published), "verifier_acceptance_rate": acceptance,
            "citation_validity": valid / len(citations) if citations else None,
            "facet_coverage": facet_coverage, "rejected": len(latest) - accepted,
            "repair_rounds": sum(e["event"] == "repair_round" for e in events),
            "cache_hits": sum(e["event"] == "cache_hit" for e in events),
            "calls": run["calls"], "tokens": run["tokens"], "active_seconds": run["active_seconds"],
            "independent_support_accuracy": None,
            "reference_facet_completeness": None,
            "round_coverage": round_coverage,
            "round_comparison_note": "Within-run planner-facet counts; not independent completeness or a randomized ablation",
            "rubric_coverage_1_to_5": round(1 + 4 * facet_coverage, 2),
            "rubric_traceability_1_to_5": round(1 + 4 * (valid / len(citations) if citations else 0), 2)}


def classification_metrics(pairs):
    tp = sum(expected and predicted for expected, predicted in pairs)
    fp = sum(not expected and predicted for expected, predicted in pairs)
    fn = sum(expected and not predicted for expected, predicted in pairs)
    tn = sum(not expected and not predicted for expected, predicted in pairs)
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None, "count": len(pairs)}


def drift(rows, metric="verifier_acceptance_rate"):
    values = [float(r[metric]) for r in rows if r.get(metric) is not None]
    if len(values) < 4:
        return {"metric": metric, "delta": None, "reason": "At least four runs required"}
    half = len(values) // 2
    return {"metric": metric, "delta": statistics.mean(values[half:]) - statistics.mean(values[:half]),
            "meaning": "Descriptive drift across these runs; not a significance test"}


def calibrate(rows):
    pairs = [(float(r["system_score"]), float(r["human_score"])) for r in rows if r.get("human_score", "").strip()]
    if not pairs:
        return {"count": 0, "mae": None, "pearson": None}
    x, y = zip(*pairs)
    correlation = statistics.correlation(x, y) if len(pairs) > 1 and statistics.pstdev(x) and statistics.pstdev(y) else None
    return {"count": len(pairs), "mae": statistics.mean(abs(a-b) for a, b in pairs), "pearson": correlation}


def fixture_gates(dataset):
    results = []
    for example in dataset["examples"]:
        claim = Claim(id=example["id"], task_id="evaluation", text=example["claim"], passage_ids=["p"],
                      quotes={"p": example["quote"]})
        passages = {"p": {"id": "p", "source_id": "source", "text": example["passage"]}} if example.get("source_exists", True) else {}
        error = validate_claim(claim, passages)
        results.append({"id": example["id"], "reference_supported": example["supported"],
                        "deterministic_rejection": error, "requires_semantic_verification": error is None})
    return {"mode": "offline-deterministic-gates", "dataset_version": dataset["version"],
            "date": datetime.now(timezone.utc).isoformat(), "policy": POLICY_VERSION,
            "examples": len(results), "rejected_by_gate": sum(bool(r["deterministic_rejection"]) for r in results),
            "semantic_precision": None, "semantic_recall": None,
            "note": "Passing a deterministic gate is NOT a support verdict. Live model evaluation has not run.",
            "results": results}


def live_verifier(dataset, settings):
    if not settings.mesh_key:
        raise ValueError("Live evaluation requires MESH_API_KEY. Fixture results cannot substitute.")
    run_id = uid("run")
    # Evaluation has its own database; synthetic cases never enter user history or memory.
    store = Store(settings.data / "evaluations" / run_id)
    scope = "evaluation:" + run_id
    run = {"id": run_id, "scope": scope, "calls": 0, "tokens": 0, "active_seconds": 0,
           "source_ids": [], "request": {"refresh": True}}
    claims = []
    for index, item in enumerate(dataset["examples"]):
        sid, pid = f"{run_id}_s{index}", f"{run_id}_p{index}"
        source = {"id": sid, "fresh_until": time.time() + 3600, "text": item["passage"]}
        if item.get("source_exists", True):
            store.put("source", source, scope)
            store.put("passage", {"id": pid, "source_id": sid, "text": item["passage"]}, scope)
            run["source_ids"].append(sid)
        claims.append(Claim(id=item["id"], task_id="evaluation", text=item["claim"],
                            passage_ids=[pid], quotes={pid: item["quote"]}).model_dump())
    store.put("run", run, scope)
    gateway = MeshGateway(settings, store)
    try:
        context = RunContext(store, run_id, Limits())
        decisions = verify_claims(store, gateway, context, run, claims)
        by_id = {d["claim_id"]: d for d in decisions}
        pairs = [(e["supported"], by_id[e["id"]]["status"] == "supported") for e in dataset["examples"]]
        return {"mode": "live-mesh-verifier", "dataset_version": dataset["version"],
                "manifest": experiment_manifest(dataset, settings.model),
                "independent_review": dataset.get("independent_review", {}),
                "reference_label_status": dataset["label_status"], "date": datetime.now(timezone.utc).isoformat(),
                "model": settings.model, "policy": POLICY_VERSION, **classification_metrics(pairs),
                "usage": {k: store.get("run", run_id)[k] for k in ("calls", "tokens")},
                "decisions": decisions}
    finally:
        gateway.close()


def experiment_manifest(dataset, model):
    import hashlib
    import os
    root = Path(__file__).resolve().parent.parent
    paths = [p.relative_to(root).as_posix() for p in sorted((root / "deepdive").glob("*.py"))] + ["uv.lock"]
    return {"model": model, "gateway": "https://api.meshapi.ai/v1", "policy": POLICY_VERSION,
            "dataset_sha256": digest(dataset), "release": os.getenv("DEEPDIVE_RELEASE", "local"),
            "files": {p: hashlib.sha256((root / p).read_bytes()).hexdigest() for p in paths}}


def quality_gate(result, dataset):
    failures = []
    if result.get("mode") != "live-mesh-verifier":
        failures.append("A live Mesh verifier result is required")
    if result.get("manifest", {}).get("dataset_sha256") != digest(dataset):
        failures.append("Result does not match this dataset")
    expected_manifest = experiment_manifest(dataset, result.get("model", ""))
    for field in ("files", "gateway", "policy"):
        if result.get("manifest", {}).get(field) != expected_manifest[field]:
            failures.append(f"Result does not match the current {field}")
    review = dataset.get("independent_review", {})
    if not review.get("reviewer") or not review.get("reviewed_at") or review.get("status") != "approved":
        failures.append("Independent reference-label review is pending")
    if result.get("count", 0) < 30:
        failures.append("At least 30 labeled examples are required")
    if result.get("precision") is None or result["precision"] < .90:
        failures.append("Support precision must be at least 0.90")
    if result.get("recall") is None or result["recall"] < .50:
        failures.append("Recall must be at least 0.50; rejecting everything cannot pass")
    return {"passed": not failures, "failures": failures, "precision_target": .90, "recall_floor": .50}


def retrieval_check(urls):
    """Exercise the real public transport without an AI key or user history."""
    from .retrieval import PublicHTTP, extract_page, search_public
    transport = PublicHTTP()
    results = []
    for url in urls:
        started = time.monotonic()
        try:
            response = transport.get(url)
            page = extract_page(response)
            results.append({"url": url, "outcome": "retrieved", "final_url": response["url"],
                            "sha256": response["sha256"], "text_characters": len(page["text"]),
                            "seconds": round(time.monotonic() - started, 3)})
        except (ValueError, OSError) as exc:
            results.append({"url": url, "outcome": "unavailable", "reason": str(exc)})
    try:
        discovered = search_public(transport, "SQLite write ahead logging official documentation")
        search = {"outcome": "available", "urls": [item["url"] for item in discovered]}
    except (ValueError, OSError) as exc:
        search = {"outcome": "unavailable", "reason": str(exc)}
    return {"mode": "live-public-retrieval", "date": datetime.now(timezone.utc).isoformat(),
            "ai_calls": 0, "sources": results, "search": search}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ["fixtures", "verifier"]:
        command = sub.add_parser(name)
        command.add_argument("--dataset", default="evaluation/claims.v1.json")
        command.add_argument("--output", required=True)
    calibration = sub.add_parser("calibrate")
    calibration.add_argument("csv_file")
    calibration.add_argument("--output", required=True)
    export = sub.add_parser("export-labels")
    export.add_argument("--output", required=True)
    benchmark = sub.add_parser("benchmark")
    benchmark.add_argument("--dataset", default="evaluation/questions.v2.json")
    benchmark.add_argument("--output", required=True)
    benchmark.add_argument("--case", action="append", dest="cases")
    benchmark.add_argument("--resume", action="store_true")
    benchmark.add_argument("--model-calls", type=int, default=25)
    benchmark.add_argument("--input-tokens", type=int, default=12000)
    benchmark.add_argument("--output-tokens", type=int, default=2000)
    benchmark.add_argument("--ledger", required=True, help="Persistent ledger shared across evaluation batches")
    benchmark.add_argument("--max-spend-usd", required=True, help="Existing-credit allocation, never a top-up")
    benchmark.add_argument("--reserve-usd", required=True, help="Existing credit to retain for demos")
    gate = sub.add_parser("gate")
    gate.add_argument("result")
    gate.add_argument("--dataset", default="evaluation/claims.v1.json")
    retrieval = sub.add_parser("retrieval")
    retrieval.add_argument("--source", action="append")
    retrieval.add_argument("--output", required=True)
    args = parser.parse_args()
    if args.command == "gate":
        result = quality_gate(json.loads(Path(args.result).read_text(encoding="utf-8")),
                              json.loads(Path(args.dataset).read_text(encoding="utf-8")))
        print(json.dumps(result, indent=2))
        raise SystemExit(0 if result["passed"] else 1)
    if args.command == "retrieval":
        result = retrieval_check(args.source or ["https://www.sqlite.org/wal.html"])
    elif args.command == "calibrate":
        with open(args.csv_file, newline="", encoding="utf-8") as handle:
            result = calibrate(list(csv.DictReader(handle)))
    elif args.command == "export-labels":
        from .client import Client
        rows = Client().request("GET", "/evaluation")["runs"]
        path = Path(args.output); path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["run_id", "metric", "system_score", "human_score", "reviewer", "notes"])
            writer.writeheader()
            for row in rows:
                writer.writerow({"run_id": row["run_id"], "metric": "coverage_1_to_5",
                                 "system_score": row["rubric_coverage_1_to_5"]})
        return
    elif args.command == "benchmark":
        from dataclasses import replace
        from .benchmark import benchmark
        from .spending import SpendLedger, metadata, available_balance
        settings = replace(Settings.load(), input_tokens=args.input_tokens, output_tokens=args.output_tokens)
        if not settings.mesh_key or not 1000 <= args.input_tokens <= 24000 or not 500 <= args.output_tokens <= 5000:
            raise SystemExit("Configure Mesh and bounded input/output token allocations")
        dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
        _, prices = metadata(settings)
        ledger = SpendLedger(args.ledger, args.max_spend_usd, args.reserve_usd, prices,
                             lambda: available_balance(settings))
        try:
            result = benchmark(dataset, settings, args.output,
                               Limits(subquestions=2, sources=8, claims=8, repair_rounds=1,
                                      model_calls=args.model_calls, active_seconds=300),
                               args.cases, args.resume, ledger)
        finally:
            ledger.close()
    else:
        dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
        result = fixture_gates(dataset) if args.command == "fixtures" else live_verifier(dataset, Settings.load())
    path = Path(args.output); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k not in {"results", "decisions", "runs", "cases", "manifest"}}, indent=2))


if __name__ == "__main__":
    main()
