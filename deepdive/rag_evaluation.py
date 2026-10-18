"""Live retrieval ablation; explicit labels and separate evidence for each ranking stage."""
import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time

from .config import Settings, pipeline_fingerprint, ROOT
from .documents import CHUNK_VERSION, PARSER_VERSION
from .guardrails import scope_for
from .models import RunRequest
from .rag import RETRIEVAL_VERSION
from .service import ResearchService
from .storage import digest, uid


def rank_metrics(ranking, relevant, k=3):
    unique = list(dict.fromkeys(ranking))[:k]
    relevant = set(relevant)
    hits = [index + 1 for index, name in enumerate(unique) if name in relevant]
    dcg = sum(1 / math.log2(rank + 1) for rank in hits)
    ideal = sum(1 / math.log2(rank + 1) for rank in range(1, min(k, len(relevant)) + 1))
    return {"recall_at_3": len(hits) / len(relevant) if relevant else 0,
            "mrr_at_3": 1 / hits[0] if hits else 0, "ndcg_at_3": dcg / ideal if ideal else 0}


def evaluate(dataset, settings, output):
    if not settings.mesh_key:
        raise ValueError("Live retrieval evaluation requires MESH_API_KEY; no synthetic fallback is used")
    settings = replace(settings, data=settings.data / "evaluations" / uid("rag"))
    result = {"mode": "live-mesh-retrieval", "date": datetime.now(timezone.utc).isoformat(),
              "dataset_version": dataset["version"], "dataset_hash": digest(dataset),
              "label_status": dataset["label_status"], "independent_review": dataset["independent_review"],
              "embedding_model": settings.embedding_model, "embedding_revision": settings.embedding_revision,
              "reranking_model": settings.model, "pipeline": pipeline_fingerprint(),
              "lock_hash": digest((ROOT / "uv.lock").read_text(encoding="utf-8")),
              "chunk_version": CHUNK_VERSION, "parser_version": PARSER_VERSION,
              "retrieval_version": RETRIEVAL_VERSION, "results": [], "status": "running"}
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    def save():
        output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    service = ResearchService(settings)
    try:
        session = service.create_session("Retrieval evaluation")
        documents = [service.documents.ingest(scope_for(session), d["name"], d["text"].encode()) for d in dataset["documents"]]
        names = {d["source_id"]: d["name"] for d in documents}
        run = service.create_run(RunRequest(question="Retrieval evaluation", session_id=session["id"],
                    research_mode="documents", document_ids=[d["id"] for d in documents]), enqueue=False)
        service.rag.index(run["id"])
        for item in dataset["questions"]:
            started = time.monotonic()
            service.rag.search(run["id"], item["question"], 16)
            event = [e for e in service.store.events(run["id"]) if e["event"] == "document_retrieval"][-1]
            metrics = {}
            for stage, field in [("bm25", "lexical_ids"), ("dense", "dense_ids"), ("hybrid", "fused_ids"), ("final", "selected_ids")]:
                ranking = [names[service.store.get("passage", pid, scope_for(session))["source_id"]] for pid in event[field]]
                metrics[stage] = rank_metrics(ranking, item["relevant"])
            result["results"].append({"question_id": item["id"], "metrics": metrics, "reranked": event["reranked"],
                                      "seconds": round(time.monotonic() - started, 3)})
            save()
        result["averages"] = {stage: {metric: sum(r["metrics"][stage][metric] for r in result["results"]) / len(result["results"])
                                     for metric in ("recall_at_3", "mrr_at_3", "ndcg_at_3")}
                              for stage in ("bm25", "dense", "hybrid", "final")}
        result["reranking_completed_for_all_queries"] = all(r["reranked"] for r in result["results"])
        current = service.get_run(run["id"])
        result.update(status="completed", model_requests=current["calls"], tokens=current["tokens"])
        service.store.update("run", run["id"], status="stopped", stage="retrieval evaluation complete")
    except Exception as exc:
        result.update(status="failed", error_type=type(exc).__name__)
        raise
    finally:
        save()
        service.close()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="evaluation/documents.v1.json")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    settings = Settings.load()
    if not settings.mesh_key:
        parser.exit(2, "Configure MESH_API_KEY before running live document retrieval evaluation.\n")
    try:
        result = evaluate(dataset, settings, args.output)
    except Exception as exc:
        raise SystemExit(f"Retrieval evaluation failed ({type(exc).__name__}); inspect the saved status and local audit") from None
    print(json.dumps({k: v for k, v in result.items() if k != "results"}, indent=2))


if __name__ == "__main__":
    main()
