"""Research inspection widgets backed only by saved application records."""
from datetime import datetime, timezone
import json
from pathlib import Path
import streamlit as st
from .artifacts import md_text, safe_link
from .presentation import report_sections, reference_numbers, revision_changes


def render_examples(root):
    """Explicit recorded runs; this view never constructs an API/model client."""
    folder = Path(root) / "evaluation" / "examples"
    index = folder / "index.json"
    if not index.exists():
        return
    examples = json.loads(index.read_text(encoding="utf-8"))
    names = {item["file"]: item["title"] for item in examples}
    selected = st.sidebar.selectbox("Open a recorded investigation", [None, *names], key="recorded_investigation",
                                    format_func=lambda item: names.get(item, "Live workspace"))
    if selected is None:
        return
    path = (folder / selected).resolve()
    if path.parent != folder.resolve() or path.suffix != ".json":
        raise ValueError("Example path must stay inside the recorded-example directory")
    example = json.loads(path.read_text(encoding="utf-8"))
    st.subheader(example["title"])
    st.info("Recorded investigation · browsing this example makes no AI calls. Choose Live workspace to start new research.")
    st.caption(f"Recorded {example['recorded_at']} · {example['run']['model']} · {example['run']['calls']} model requests")
    st.write(example["summary"])
    for limitation in example["limitations"]:
        st.caption(limitation)
    render_report(example["run"], example, example)
    st.subheader("Research rounds")
    render_rounds(example["events"])
    with st.expander("Verification decisions"):
        claims = {c["id"]: c for c in example["claims"]}
        for decision in example["decisions"]:
            claim = claims.get(decision["claim_id"])
            if claim:
                st.write(claim["text"])
                st.caption(decision["status"] + " · " + decision["reason"])
    st.download_button("Download recorded report", example["report_markdown"], file_name=path.stem + ".md")
    st.download_button("Download recorded evidence", json.dumps(example, indent=2), file_name=path.name)
    st.stop()


def render_report(run, records, evidence):
    claims = {c["id"]: c for c in records["claims"]}
    decisions = {d["claim_id"]: d for d in records["decisions"]}
    passages = {p["id"]: p for p in evidence["passages"]}
    sources = {s["id"]: s for s in evidence["sources"]}
    sections = report_sections(run, claims)
    refs = reference_numbers(sections)
    if not sections:
        st.info("No substantive finding has passed the source-support checks.")
    number = 0
    for section in sections:
        st.subheader(section["title"])
        for finding in section["findings"]:
            number += 1
            citations = " ".join(f"[{refs[p]}]" for p in finding["passage_ids"])
            st.markdown(md_text(finding["text"]) + " **" + citations + "**")
            with st.expander(f"Inspect evidence · finding {number} · {citations}"):
                for cid in finding["claim_ids"]:
                    d = decisions.get(cid, {})
                    st.caption(f"{cid} · {d.get('status', 'unavailable')}")
                    st.write(d.get("reason", "No verification decision available."))
                for pid in finding["passage_ids"]:
                    passage = passages.get(pid)
                    if not passage or passage["source_id"] not in sources:
                        st.error("This saved reference is unavailable.")
                        continue
                    source = sources[passage["source_id"]]
                    st.markdown(f"**[{refs[pid]}] {md_text(source['title'])}**")
                    st.caption(passage.get("location", "") or "Stored source passage")
                    if source.get("retrieved_at"):
                        st.caption("Retrieved " + datetime.fromtimestamp(source["retrieved_at"], timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
                    if source.get("final_url"):
                        st.link_button("Open original source", safe_link(source["final_url"]))
                    quotes = list(dict.fromkeys(
                        decisions.get(cid, {}).get("quotes", {}).get(pid, "") for cid in finding["claim_ids"]))
                    for quote in filter(None, quotes):
                        st.markdown("**Supporting quotation**")
                        st.text(quote)
                    st.markdown("**Complete cited passage**")
                    st.markdown(md_text(passage["text"]))
                    st.caption("Reference ID: " + pid)
    gaps = [(c["task_id"], g) for c in run.get("coverage", []) for g in c["missing_facets"]]
    conflicts = [c for item in run.get("coverage", []) for c in item["contradictions"]]
    if gaps or conflicts:
        st.subheader("Coverage and disagreements")
        for _, gap in gaps:
            st.markdown("- Unanswered: " + md_text(gap))
        for conflict in conflicts:
            st.markdown("- Unresolved disagreement (assessment): " + md_text(conflict))
    prior = run.get("report_versions", {}).get(str(run.get("version", 1) - 1))
    if prior:
        changes = revision_changes(prior["published_claim_ids"], run["published_claim_ids"], claims)
        with st.expander("Changes since the previous draft"):
            st.write(f"{len(changes['retained'])} retained · {len(changes['added'])} added · {len(changes['removed'])} removed")
            for label in ("added", "removed"):
                for cid in changes[label]:
                    st.markdown(f"**{label.title()}**: " + md_text(claims[cid]["text"]))
                    st.caption(cid + " · " + ", ".join(claims[cid]["passage_ids"]))


def render_progress(run, events):
    version = max((e.get("version", 1) for e in events), default=run.get("version", 1))
    tasks = run.get("plan", {}).get("tasks", [])
    current = [e for e in events if e.get("version", version) == version]
    active = {}
    for event in current:
        key = (event.get("round", 0), event.get("task_id"))
        if event["event"] == "task_started":
            active[key] = event["question"]
        elif event["event"] == "task_finished":
            active.pop(key, None)
    if run["status"] not in {"running", "queued"}:
        active.clear()
    round_number = max((e.get("round", 0) for e in current), default=0)
    st.caption(f"{len(tasks)} planned research tasks · round {round_number + 1} · {len(active)} tasks active")
    for question in active.values():
        st.markdown("- Researching: " + md_text(question))
    assessed = [e for e in current if e["event"] == "round_assessed"]
    if assessed:
        latest = assessed[-1]
        gaps = sum(len(c["missing_facets"]) for c in latest["coverage"])
        st.caption(f"{len(latest['supported_claim_ids'])} supported claims · "
                   f"{len(latest['rejected_claim_ids'])} claims not supported · {gaps} unanswered facets")


def render_rounds(events):
    reasons = {
        "facets_covered": "The assessor found support for the planned facets.",
        "round_limit": "The configured repair-round limit was reached.",
        "model_call_reserve": "Remaining model requests were reserved for finishing the report.",
        "active_time_reserve": "Remaining active time was reserved for finishing the report.",
        "no_untried_queries": "No new query was available; unanswered facets remain visible.",
        "unanswered_facets_with_new_queries": "Unanswered facets triggered targeted research with new queries.",
    }
    snapshots = {}
    for event in events:
        key = (event.get("version", 1), event.get("round", 0))
        if event["event"] == "round_assessed":
            snapshots[key] = event
        elif event["event"] == "research_next_step":
            snapshot = snapshots.get(key, {})
            with st.expander(f"Draft {key[0]} · round {key[1] + 1} · {event['decision']}"):
                st.write(reasons.get(event["reason"], event["reason"]))
                st.caption(f"{len(snapshot.get('source_ids', []))} sources · "
                           f"{len(snapshot.get('new_source_ids', []))} new sources · "
                           f"{len(snapshot.get('supported_claim_ids', []))} supported claims · "
                           f"{len(snapshot.get('rejected_claim_ids', []))} not supported")
                for item in snapshot.get("coverage", []):
                    st.write("Task: " + item["task_id"])
                    for facet, ids in item["supported_facets"].items():
                        st.markdown("- Covered: " + md_text(facet))
                        st.caption(", ".join(ids))
                    for facet in item["missing_facets"]:
                        st.markdown("- Missing: " + md_text(facet))
                    for conflict in item["contradictions"]:
                        st.markdown("- Disagreement assessment: " + md_text(conflict))
                    if event["decision"] == "repair":
                        for query in item["next_queries"]:
                            st.markdown("- Proposed query: " + md_text(query))
