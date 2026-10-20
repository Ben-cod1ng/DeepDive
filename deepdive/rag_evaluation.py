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
from .evaluation import experiment_manifest


def rank_metrics(ranking, relevant, k=3):
    unique = list(dict.fromkeys(ranking))[:k]
    relevant = set(relevant)
    hits = [index + 1 for index, name in enumerate(unique) if name in relevant]
    dcg = sum(1 / math.log2(rank + 1) for rank in hits)
    ideal = sum(1 / math.log2(rank + 1) for rank in range(1, min(k, len(relevant)) + 1))
    return {"recall_at_3": len(hits) / len(relevant) if relevant else None,
            "mrr_at_3": (1 / hits[0] if hits else 0) if relevant else None,
            "ndcg_at_3": dcg / ideal if ideal else None}


def passage_labels(question, passages, names):
    """Resolve authored exact quotations to eligible chunks, including overlaps."""
    relevant = set()
    for label in question.get("relevant_passages", []):
        matches = {p["id"] for p in passages if names[p["source_id"]] == label["document"]
                   and label["quote"] in p["text"]}
        if not matches:
            raise ValueError(f"Reference quotation crosses a chunk boundary or is missing: {question['id']}")
        relevant.update(matches)
    return sorted(relevant)


def averages(rows, field):
    result = {}
    for stage in ("bm25", "dense", "hybrid", "final"):
        result[stage] = {}
        for metric in ("recall_at_3", "mrr_at_3", "ndcg_at_3"):
            values = [row[field][stage][metric] for row in rows if row.get(field)
                      and row[field][stage][metric] is not None]
            result[stage][metric] = sum(values) / len(values) if values else None
    return result


def audit_corpus(dataset):
    """Check frozen reference spans against real parser/chunker without inference."""
    from .document_parser import extract
    from .documents import chunks
    passages, names, counts = [], {}, {}
    for document in dataset["documents"]:
        name = document["name"]
        names[name] = name
        blocks = extract(document["text"].encode(), Path(name).suffix)["blocks"]
        parts = list(chunks(blocks))
        for index, part in enumerate(parts):
            assert part["text"] == blocks[part["block"]]["text"][part["start"]:part["end"]]
            assert part["tokens"] <= 384
            passages.append({**part, "id": f"{name}:{index}", "source_id": name})
        counts[name] = {"sections": len(blocks), "chunks": len(parts), "characters": len(document["text"])}
    labels = {q["id"]: passage_labels(q, passages, names) for q in dataset["questions"]}
    return {"mode": "offline-chunk-boundary-audit", "dataset_sha256": digest(dataset),
            "chunk_version": CHUNK_VERSION, "parser_version": PARSER_VERSION,
            "documents": counts, "reference_chunks": labels, "ai_calls": 0,
            "reference_quotes_fit_chunks": True, "exact_offsets": True,
            "note": "Authored quotation coverage and immutable offsets, not measured retrieval or answer quality"}


def evaluate(dataset, settings, output, ledger=None):
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
    result["manifest"] = experiment_manifest(dataset, settings.model)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    def save():
        output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    service = ResearchService(settings)
    service.gateway.spend_guard = ledger
    try:
        session = service.create_session("Retrieval evaluation")
        documents = [service.documents.ingest(scope_for(session), d["name"], d["text"].encode()) for d in dataset["documents"]]
        names = {d["source_id"]: d["name"] for d in documents}
        passages = [p for p in service.store.list("passage", scope_for(session)) if p["source_id"] in names]
        # Validate every label before paying for embeddings.
        labels = {q["id"]: passage_labels(q, passages, names) for q in dataset["questions"]}
        result["corpus"] = {"documents": len(documents), "chunks": len(passages),
                            "reference_quotes_fit_chunks": True}
        run = service.create_run(RunRequest(question="Retrieval evaluation", session_id=session["id"],
                    research_mode="documents", document_ids=[d["id"] for d in documents]), enqueue=False)
        service.rag.index(run["id"])
        for item in dataset["questions"]:
            started = time.monotonic()
            service.rag.search(run["id"], item["question"], 16)
            event = [e for e in service.store.events(run["id"]) if e["event"] == "document_retrieval"][-1]
            metrics, passage_metrics, rankings = {}, {}, {}
            for stage, field in [("bm25", "lexical_ids"), ("dense", "dense_ids"), ("hybrid", "fused_ids"), ("final", "selected_ids")]:
                ranking = [names[service.store.get("passage", pid, scope_for(session))["source_id"]] for pid in event[field]]
                metrics[stage] = rank_metrics(ranking, item["relevant"])
                rankings[stage] = event[field]
                if "relevant_passages" in item:
                    passage_metrics[stage] = rank_metrics(event[field], labels[item["id"]])
            result["results"].append({"question_id": item["id"], "metrics": metrics, "reranked": event["reranked"],
                                      "passage_metrics": passage_metrics, "rankings": rankings,
                                      "relevant_passage_ids": labels[item["id"]],
                                      "answer_absent": not item["relevant"],
                                      "returned_passages": len(event["selected_ids"]),
                                      "fallback_reason": event.get("fallback_reason"),
                                      "seconds": round(time.monotonic() - started, 3)})
            save()
        result["averages"] = averages(result["results"], "metrics")
        result["passage_averages"] = averages(result["results"], "passage_metrics")
        result["metric_definition"] = "Binary relevance per eligible chunk; overlapping chunks count separately. Answer-absent ratios are null and excluded from means. Retrieval is not an abstention verdict."
        result["reranking_completed_for_all_queries"] = all(r["reranked"] for r in result["results"])
        current = service.get_run(run["id"])
        result.update(status="completed", model_requests=current["calls"], tokens=current["tokens"])
        service.store.update("run", run["id"], status="stopped", stage="retrieval evaluation complete")
    except Exception as exc:
        result.update(status="failed", error_type=type(exc).__name__)
        raise
    finally:
        if ledger is not None:
            result["spending"] = ledger.summary()
        save()
        service.close()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="evaluation/documents.v1.json")
    parser.add_argument("--output", required=True)
    parser.add_argument("--ledger")
    parser.add_argument("--max-spend-usd")
    parser.add_argument("--reserve-usd")
    parser.add_argument("--offline", action="store_true", help="Audit chunk/reference boundaries with zero model calls")
    args = parser.parse_args()
    dataset = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    if args.offline:
        result = audit_corpus(dataset)
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result, indent=2))
        return
    settings = Settings.load()
    if not settings.mesh_key:
        parser.exit(2, "Configure MESH_API_KEY before running live document retrieval evaluation.\n")
    ledger = None
    if any([args.ledger, args.max_spend_usd, args.reserve_usd]):
        if not all([args.ledger, args.max_spend_usd, args.reserve_usd]):
            parser.error("Provide ledger, max-spend-usd and reserve-usd together")
        from .spending import SpendLedger, metadata, available_balance
        _, prices = metadata(settings)
        ledger = SpendLedger(args.ledger, args.max_spend_usd, args.reserve_usd, prices,
                             lambda: available_balance(settings))
    try:
        result = evaluate(dataset, settings, args.output, ledger)
    except Exception as exc:
        raise SystemExit(f"Retrieval evaluation failed ({type(exc).__name__}); inspect the saved status and local audit") from None
    finally:
        if ledger is not None:
            ledger.close()
    print(json.dumps({k: v for k, v in result.items() if k != "results"}, indent=2))


if __name__ == "__main__":
    main()
