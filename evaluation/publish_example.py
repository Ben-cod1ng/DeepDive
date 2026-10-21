"""Package an explicitly selected public/synthetic real run for read-only inspection.

Review source reuse permission before publishing. This never runs research or
rewrites claims. The original audit hash and model events preserve provenance.
"""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audit")
    parser.add_argument("report")
    parser.add_argument("--output", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--limitation", action="append", required=True)
    parser.add_argument("--claim", action="append", help="Publish an explicitly labeled excerpt containing these original claim IDs")
    args = parser.parse_args()
    raw = Path(args.audit).read_bytes()
    data = json.loads(raw)
    run = data["run"]
    if run["status"] != "completed" or not run["tokens"] or not any(e["event"] == "model_response" for e in data["events"]):
        raise ValueError("Select a completed run with recorded model usage")
    # Exclude local paths, user/session context and artifacts. Retain evidence,
    # original IDs, exact claims/decisions, and operational events unchanged.
    fields = ("id", "model", "calls", "tokens", "status", "created", "completed_at", "version",
              "plan", "outline", "coverage", "report_versions", "published_claim_ids")
    saved_run = {key: run[key] for key in fields if key in run}
    saved_run["request"] = {key: run["request"][key] for key in
                            ("question", "research_mode", "review_mode", "limits", "source_urls") if key in run["request"]}
    source_fields = ("id", "title", "kind", "final_url", "retrieved_at", "published_at", "content_hash", "quality")
    output = {"title": args.title, "summary": args.summary,
              "recorded_at": datetime.fromtimestamp(run["created"], timezone.utc).isoformat(),
              "tested_release": args.release, "original_audit_sha256": hashlib.sha256(raw).hexdigest(),
              "limitations": args.limitation, "run": saved_run,
              "sources": [{k: s[k] for k in source_fields if k in s} for s in data["sources"]],
              **{key: data[key] for key in ("passages", "claims", "decisions", "events")},
              "report_markdown": Path(args.report).read_text(encoding="utf-8")}
    if args.claim:
        selected = set(args.claim)
        if selected - {c["id"] for c in data["claims"]}:
            raise ValueError("Excerpt contains unknown claim IDs")
        output["original_published_claim_ids"] = run["published_claim_ids"]
        output["excerpt"] = True
        output["claims"] = [c for c in data["claims"] if c["id"] in selected]
        output["decisions"] = [d for d in data["decisions"] if d["claim_id"] in selected]
        cited = {pid for c in output["claims"] for pid in c["passage_ids"]}
        output["passages"] = [p for p in data["passages"] if p["id"] in cited]
        sources = {p["source_id"] for p in output["passages"]}
        output["sources"] = [s for s in output["sources"] if s["id"] in sources]
        saved_run["published_claim_ids"] = [cid for cid in run["published_claim_ids"] if cid in selected]
        output["limitations"].append(f"Recorded excerpt: {len(saved_run['published_claim_ids'])} of {len(run['published_claim_ids'])} original published claims. Usage and round totals describe the original full run.")
        # Events can contain third-party quotations; retain operational/round
        # events and only selected claims' quotation/decision events.
        output["events"] = [e for e in data["events"] if not e.get("claim_id") or e["claim_id"] in selected]
        lines = ["# " + args.title, "", output["limitations"][-1], ""]
        for claim in output["claims"]:
            if claim["id"] in saved_run["published_claim_ids"]:
                lines.extend([claim["text"], "References: " + ", ".join(claim["passage_ids"]), ""])
        for passage in output["passages"]:
            lines.extend(["## " + passage["id"], passage["text"], ""])
        output["report_markdown"] = "\n".join(lines)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as handle:
        json.dump(output, handle, ensure_ascii=False, indent=2)
    print(json.dumps({"example": path.name, "claims": len(data["claims"]), "run_id": run["id"]}))


if __name__ == "__main__":
    main()
