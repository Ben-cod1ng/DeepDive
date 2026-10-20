"""Deterministic display grouping; verified claim records remain unchanged."""


def claim_key(claim):
    # Case, numbers, units and negation remain significant.
    return (claim["text"].strip(), claim.get("kind", "fact"))


def report_sections(run, claims):
    published = {cid: claims[cid] for cid in run.get("published_claim_ids", []) if cid in claims}
    ordered = list(dict.fromkeys(
        cid for section in run.get("outline", {}).get("sections", []) for cid in section["claim_ids"]
        if cid in published))
    ordered.extend(cid for cid in published if cid not in ordered)
    groups = {}
    for cid in ordered:
        claim = published[cid]
        key = claim_key(claim)
        group = groups.setdefault(key, {"text": claim["text"], "task_id": claim["task_id"],
                                       "claim_ids": [], "passage_ids": []})
        group["claim_ids"].append(cid)
        group["passage_ids"] = sorted(set(group["passage_ids"]) | set(claim["passage_ids"]))
    tasks = {t["id"]: t for t in run.get("plan", {}).get("tasks", [])}
    sections = {}
    for group in groups.values():
        task_id = group["task_id"]
        task = tasks.get(task_id)
        # A task question is labeled as a question, never promoted to a verified finding.
        title = "Research question: " + task["question"] if task else "Research findings"
        section = sections.setdefault(task_id, {"title": title, "findings": []})
        section["findings"].append(group)
    return list(sections.values())


def reference_numbers(sections):
    ids = sorted({pid for section in sections for finding in section["findings"] for pid in finding["passage_ids"]})
    return {pid: index + 1 for index, pid in enumerate(ids)}


def revision_changes(previous_ids, current_ids, claims):
    def indexed(ids):
        result = {}
        for cid in ids:
            if cid in claims:
                c = claims[cid]
                key = (*claim_key(c), tuple(sorted(c["passage_ids"])))
                result.setdefault(key, []).append(cid)
        return result
    before, after = indexed(previous_ids), indexed(current_ids)
    return {
        "retained": [{"previous_ids": before[k], "current_ids": after[k]} for k in after if k in before],
        "added": [cid for k in after if k not in before for cid in after[k]],
        "removed": [cid for k in before if k not in after for cid in before[k]],
    }
