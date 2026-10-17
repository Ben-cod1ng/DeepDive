"""Research Atelier visual foundation, connected to the shared DeepDive API."""
import json
import uuid
from datetime import datetime, timezone
import httpx
import pandas as pd
import streamlit as st
from deepdive.client import Client

st.set_page_config(page_title="DeepDive · Research Atelier", layout="wide")
st.markdown("""
<style>
.stApp{background:radial-gradient(circle at 12% 8%,rgba(240,164,107,.13),transparent 36%),
radial-gradient(circle at 86% 14%,rgba(78,201,176,.13),transparent 40%),linear-gradient(180deg,#141a22,#1d2631);color:#f5f7fb}
h1,h2,h3{font-family:Georgia,serif;font-weight:400}
.hero{padding:1.5rem 1.8rem;border:1px solid #384452;border-radius:22px;background:rgba(17,23,31,.86);margin-bottom:1rem}
.hero p{color:#bdc9d7}.eyebrow{color:#4ec9b0;font-size:.8rem;letter-spacing:.16em;text-transform:uppercase}
.stButton button,.stDownloadButton button{border:1px solid #4ec9b0;border-radius:10px}
[data-testid="stSidebar"]{background:#101720}
</style>
""", unsafe_allow_html=True)
st.markdown('<div class="hero"><div class="eyebrow">Research Atelier / DeepDive</div><h1>Follow the evidence.</h1><p>Investigate a question. Inspect its sources. Review what holds up.</p></div>', unsafe_allow_html=True)
client = Client(entry_point="streamlit")


def call(method, path, **kwargs):
    try:
        return client.request(method, path, **kwargs)
    except (RuntimeError, httpx.HTTPError) as exc:
        st.error(str(exc))
        st.stop()


def new_action(path, payload=None):
    return call("POST", path, json=payload, headers={"Idempotency-Key": uuid.uuid4().hex})


health = call("GET", "/health")
if not health["mesh_configured"]:
    st.info("Mesh is not configured. You can browse saved research; live research needs MESH_API_KEY in DeepDive/.env and a backend restart.")

with st.sidebar:
    st.subheader("Research library")
    projects = call("GET", "/projects")["projects"]
    project_names = {p["id"]: p["name"] for p in projects}
    project = st.selectbox("Research project", [None] + list(project_names),
                           format_func=lambda value: project_names.get(value, "Isolated sessions"))
    st.caption("Selecting a project enables shared research memory. Isolated sessions do not share evidence.")
    with st.expander("New project"):
        name = st.text_input("Project name")
        if st.button("Create project", disabled=not name.strip()):
            new_action("/projects", {"name": name})
            st.rerun()
    sessions = [s for s in call("GET", "/sessions")["sessions"] if s.get("project_id") == project]
    search = st.text_input("Search sessions", placeholder="A topic or question")
    sessions = [s for s in sessions if search.casefold() in s["name"].casefold()]
    names = {s["id"]: s["name"] for s in sessions}
    choices = [None] + list(reversed(names))
    prior = st.session_state.get("active_session")
    selected = st.selectbox("Session", choices, index=choices.index(prior) if prior in choices else 0,
                            format_func=lambda value: names.get(value, "New research session"))
    st.session_state.active_session = selected
    if selected:
        with st.expander("Manage session"):
            rename = st.text_input("Session name", names[selected])
            if st.button("Rename"):
                call("PATCH", f"/sessions/{selected}", json={"name": rename})
                st.rerun()
            exported = call("GET", f"/sessions/{selected}/export")
            st.download_button("Export session JSON", json.dumps(exported, indent=2), file_name=f"{selected}.json")
            delete = st.checkbox("Delete this session and its research")
            if st.button("Delete session", disabled=not delete):
                call("DELETE", f"/sessions/{selected}")
                st.session_state.active_session = None
                st.rerun()
    st.divider()
    st.caption("Local workspace · all AI through Mesh")
    st.caption(health["model"])

with st.expander("Research settings", expanded=not selected):
    col1, col2, col3 = st.columns(3)
    tasks = col1.number_input("Subquestions", 1, 6, 4)
    source_limit = col2.number_input("Maximum sources", 1, 30, 30)
    rounds = col3.number_input("Repair rounds", 0, 2, 2)
    col4, col5, col6 = st.columns(3)
    claim_limit = col4.number_input("Maximum candidate claims", 1, 40, 40)
    call_limit = col5.number_input("Maximum model requests", 5, 80, 80)
    seconds = col6.number_input("Active time limit (seconds)", 30, 900, 900)
    sources_text = st.text_area("Optional source URLs", placeholder="One public URL per line. Useful when public search is blocked.")
    refresh = st.checkbox("Refresh sources and bypass caches")
    auto = st.checkbox("Automatically approve the checked report", value=False)

question = st.chat_input("What would you like to investigate?")
if question:
    created = new_action("/runs", {"question": question, "session_id": selected, "project_id": project,
        "source_urls": [u.strip() for u in sources_text.splitlines() if u.strip()],
        "review_mode": "auto" if auto else "manual", "refresh": refresh,
        "limits": {"subquestions": tasks, "sources": source_limit, "repair_rounds": rounds,
                   "claims": claim_limit, "model_calls": call_limit, "active_seconds": seconds}})
    st.session_state.active_session = created["session_id"]
    st.session_state.active_run = created["id"]
    st.rerun()

if selected:
    session = call("GET", f"/sessions/{selected}")
    turns = session.get("turns", [])
    if turns:
        by_id = {r["id"]: r for r in turns}
        prior_run = st.session_state.get("active_run", turns[-1]["id"])
        run_id = st.selectbox("Research turn", list(by_id), index=list(by_id).index(prior_run) if prior_run in by_id else len(by_id)-1,
                             format_func=lambda value: by_id[value]["request"]["question"])
        st.session_state.active_run = run_id

        @st.fragment(run_every="3s")
        def render_run():
            run = call("GET", f"/runs/{run_id}")
            st.write(run["request"]["question"])
            a, b, c, d = st.columns(4)
            a.metric("Status", run["status"].replace("_", " "))
            b.metric("Model calls", run["calls"])
            c.metric("Tokens", run["tokens"])
            d.metric("Active time", f"{run['active_seconds']:.0f}s")
            st.caption("Current activity: " + run["stage"])
            if run.get("error"):
                st.error(run["error"])
            if run.get("cached_from"):
                st.info("Reused a fresh report for this exact context. Review and audit remain available.")
            if run["status"] == "waiting_review":
                st.info("The checked draft is ready for your review.")
                feedback = st.text_area("Revision feedback", key=run_id + "_feedback")
                approve, revise, stop = st.columns(3)
                if approve.button("Approve and generate exports", key=run_id+"_approve"):
                    new_action(f"/runs/{run_id}/review", {"decision": "approve"})
                    st.rerun()
                if revise.button("Revise with feedback", disabled=not feedback.strip(), key=run_id+"_revise"):
                    new_action(f"/runs/{run_id}/review", {"decision": "revise", "feedback": feedback})
                    st.rerun()
                if stop.button("Stop", key=run_id+"_stop"):
                    new_action(f"/runs/{run_id}/review", {"decision": "stop"})
                    st.rerun()
            elif run["status"] in {"failed", "interrupted"}:
                if st.button("Resume from checkpoint", key=run_id+"_resume"):
                    new_action(f"/runs/{run_id}/resume")
                    st.rerun()
            elif run["status"] in {"queued", "running"}:
                if st.button("Cancel run", key=run_id+"_cancel"):
                    new_action(f"/runs/{run_id}/cancel")
                    st.rerun()
            report, evidence, verification, timeline, artifacts = st.tabs(["Report", "Evidence", "Verification", "Timeline", "Artifacts"])
            with report:
                if run.get("outline"):
                    st.markdown(call("GET", f"/runs/{run_id}/report")["markdown"])
                else:
                    st.caption("Research is gathering and checking evidence.")
                st.caption("Automated source verification can be wrong. Coverage and correctness are separate.")
            with evidence:
                records = call("GET", f"/runs/{run_id}/evidence")
                passages = records["passages"]
                for source in records["sources"]:
                    with st.expander(source["title"]):
                        if source.get("final_url"):
                            st.link_button("Open original source", source["final_url"])
                        st.caption(source["quality"]["label"] + " · " + source["quality"]["reason"])
                        st.caption("Retrieved " + datetime.fromtimestamp(source["retrieved_at"], timezone.utc).isoformat())
                        st.code(source["content_hash"], language=None)
                        for passage in [p for p in passages if p["source_id"] == source["id"]]:
                            st.caption(passage["id"])
                            st.text(passage["text"])
            with verification:
                records = call("GET", f"/runs/{run_id}/claims")
                latest = {v["claim_id"]: v for v in records["decisions"]}
                for claim in records["claims"]:
                    decision = latest.get(claim["id"], {})
                    with st.expander(decision.get("status", "pending").upper() + " · " + claim["text"][:100]):
                        st.text(claim["text"])
                        st.write(decision.get("reason", "Not checked"))
                        st.json({"claim_id": claim["id"], "passages": claim["passage_ids"], "quotes": decision.get("quotes", {})})
            with timeline:
                events = call("GET", f"/runs/{run_id}/events")["events"]
                if events:
                    # Event payloads have different schemas; encode nested values for Arrow.
                    st.dataframe(pd.DataFrame([{k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v
                                               for k, v in event.items()} for event in events]), width="stretch")
                st.download_button("Download audit JSON", json.dumps(call("GET", f"/runs/{run_id}/export"), indent=2),
                                   file_name=f"{run_id}-audit.json", key=run_id+"_audit")
            with artifacts:
                for artifact in run.get("artifacts", []):
                    try:
                        content = client.download(run_id, artifact["id"])
                        if artifact["name"].endswith(".png"):
                            st.image(content, caption=artifact["name"])
                        st.download_button(artifact["name"], content, file_name=artifact["name"], key=artifact["id"])
                    except httpx.HTTPError as exc:
                        st.error(str(exc))
        render_run()

if selected or project:
    with st.expander("Memory · inspect, save, and forget"):
        params = {"project_id": project} if project else {"session_id": selected}
        st.caption("Preferences are saved only when you ask. Project research retains source links; it must be checked again when stale.")
        preference = st.text_area("A preference to remember", placeholder="For example: explain technical terms and include comparison tables.")
        if st.button("Save preference", disabled=not preference.strip()):
            call("POST", "/memory/preferences", params=params, json={"text": preference})
            st.rerun()
        for item in call("GET", "/memory", params=params)["items"]:
            st.caption(item["kind"])
            if item["kind"] == "preference":
                edited = st.text_area("Preference", item["text"], key=item["id"]+"_edit")
                if st.button("Update", key=item["id"]+"_save"):
                    call("PATCH", "/memory/" + item["id"], params=params, json={"text": edited})
                    st.rerun()
            else:
                st.text(item["text"])
            if st.button("Forget", key=item["id"]):
                call("DELETE", "/memory/" + item["id"], params=params)
                st.rerun()
        clear = st.checkbox("Clear all memory in this selected scope")
        if st.button("Clear memory", disabled=not clear):
            call("DELETE", "/memory", params=params)
            st.rerun()

with st.expander("Evaluation trends"):
    metrics = call("GET", "/evaluation")["runs"]
    if metrics:
        frame = pd.DataFrame(metrics)
        st.dataframe(frame, use_container_width=True)
        st.line_chart(frame.set_index("run_id")[["verifier_acceptance_rate", "facet_coverage"]])
        st.download_button("Export evaluation metrics", frame.to_csv(index=False), file_name="evaluation.csv")
    else:
        st.caption("Completed runs will appear here. Verifier acceptance is not independently measured accuracy.")
