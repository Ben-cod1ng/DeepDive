"""Export blind review worksheets; never invent reviewer identities or labels."""
import argparse
import csv
import json
from pathlib import Path
from .storage import digest


FIELDS = ["dataset_sha256", "case_id", "claim", "passages", "supplied_quotes", "source_exists",
          "human_label", "reviewer", "reviewed_at", "notes"]


def claim_rows(dataset):
    fingerprint = digest(dataset)
    return [{"dataset_sha256": fingerprint, "case_id": item["id"], "claim": item["claim"],
             "passages": item["passage"], "supplied_quotes": item["quote"],
             "source_exists": str(item.get("source_exists", True)).lower()}
            for item in dataset["examples"]]


def report_rows(exported):
    passages = {p["id"]: p for p in exported["passages"]}
    sources = {s["id"]: s for s in exported["sources"]}
    fingerprint = digest(exported)
    selected = set(exported["run"]["published_claim_ids"])
    rows = []
    for claim in exported["claims"]:
        if claim["id"] not in selected:
            continue
        evidence = []
        for pid in claim["passage_ids"]:
            passage = passages.get(pid)
            if passage:
                source = sources.get(passage["source_id"], {})
                evidence.append({"id": pid, "text": passage["text"], "location": passage.get("location"),
                                 "source": source.get("final_url") or source.get("title"),
                                 "retrieved_at": source.get("retrieved_at")})
        rows.append({"dataset_sha256": fingerprint, "case_id": claim["id"], "claim": claim["text"],
                     "passages": json.dumps(evidence, ensure_ascii=False),
                     "supplied_quotes": json.dumps(claim["quotes"], ensure_ascii=False),
                     "source_exists": str(len(evidence) == len(claim["passage_ids"])).lower()})
    return rows


def write_review(rows, output):
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Never overwrite a worksheet that might contain a person's judgments.
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def score_review(expected_rows, labels):
    expected = {r["case_id"]: r for r in expected_rows}
    actual = {r["case_id"]: r for r in labels}
    if len(actual) != len(labels) or actual.keys() != expected.keys():
        raise ValueError("Review must contain exactly one row for every exported claim")
    for cid, row in actual.items():
        original = expected[cid]
        if any(row.get(field) != str(original.get(field, "")) for field in FIELDS[:6]):
            raise ValueError("Review evidence or dataset identity changed")
        if row.get("human_label") not in {"supported", "unsupported", "ambiguous"}:
            raise ValueError("Every claim needs a supported, unsupported or ambiguous label")
        if not all(row.get(field, "").strip() for field in ("reviewer", "reviewed_at", "notes")):
            raise ValueError("Every label needs reviewer, review date and rationale")
    count = len(actual)
    supported = sum(r["human_label"] == "supported" for r in labels)
    return {"reviewed_claims": count, "supported": supported,
            "unsupported": sum(r["human_label"] == "unsupported" for r in labels),
            "ambiguous": sum(r["human_label"] == "ambiguous" for r in labels),
            "human_labeled_support_fraction": supported / count if count else None,
            "reviewers": sorted({r["reviewer"] for r in labels}),
            "note": "Worksheet validation does not establish reviewer independence; record that separately."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=["claims", "report"])
    parser.add_argument("source")
    parser.add_argument("--output", required=True)
    parser.add_argument("--labels", help="Validate and score a completed CSV instead of exporting a worksheet")
    args = parser.parse_args()
    source = json.loads(Path(args.source).read_text(encoding="utf-8"))
    rows = claim_rows(source) if args.kind == "claims" else report_rows(source)
    if args.labels:
        with open(args.labels, newline="", encoding="utf-8") as handle:
            result = score_review(rows, list(csv.DictReader(handle)))
        Path(args.output).write_text(json.dumps(result, indent=2), encoding="utf-8")
    else:
        write_review(rows, args.output)
    print(f"{len(rows)} review cases; authored labels and model verdicts are not included.")


if __name__ == "__main__":
    main()
