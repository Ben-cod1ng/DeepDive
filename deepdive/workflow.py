"""LangGraph fan-out/fan-in, bounded repairs, and durable review interrupts."""
from typing import Annotated, TypedDict
from langgraph.graph import StateGraph, START, END
from langgraph.types import Send, interrupt

from .gateway import BudgetExceeded, ContextExceeded
from .models import (Plan, Task, ToolSelection, ClaimBatch, Coverage, ReportOutline,
                     ReviewRequest, SummarySelection)
from .storage import digest
from .verification import verify_claims
from .tools import quality, query_fingerprint
from .guardrails import anchor_quotes


def merge_results(left, right):
    return {**left, **right}


class ResearchState(TypedDict, total=False):
    run_id: str
    plan: dict
    round: int
    version: int
    tasks: list[dict]
    results: Annotated[dict, merge_results]
    claims: list[str]
    supported: list[str]
    coverage: list[dict]
    outline: dict
    action: str
    feedback: list[str]
    previous_source_count: int
    summary: list[str]
    charts: list[dict]
    cached_report: bool


class Workflow:
    def __init__(self, service, saver):
        self.s = service
        graph = StateGraph(ResearchState)
        for name in ["prepare", "plan", "research", "analyze", "verify", "coverage", "repair", "synthesize",
                     "review", "revise", "summary", "charts", "publish"]:
            graph.add_node(name, getattr(self, name))
        graph.add_edge(START, "prepare")
        graph.add_conditional_edges("prepare", lambda state: "review" if state.get("cached_report") else "plan", ["review", "plan"])
        graph.add_conditional_edges("plan", self.dispatch, ["research"])
        graph.add_edge("research", "analyze")
        graph.add_edge("analyze", "verify")
        graph.add_edge("verify", "coverage")
        graph.add_conditional_edges("coverage", self.after_coverage, {"repair": "repair", "synthesize": "synthesize"})
        graph.add_conditional_edges("repair", self.dispatch, ["research"])
        graph.add_edge("synthesize", "review")
        graph.add_conditional_edges("review", self.after_review, ["revise", "summary", "charts", END])
        graph.add_edge("revise", "plan")
        # Two independent post-review preparations with an explicit completion barrier.
        graph.add_edge(["summary", "charts"], "publish")
        graph.add_edge("publish", END)
        self.graph = graph.compile(checkpointer=saver)

    def runtime(self, state, stage):
        run, context, tools = self.s.runtime(state["run_id"])
        self.s.store.update("run", run["id"], stage=stage)
        self.s.store.event(run["id"], "stage", stage=stage, round=state.get("round", 0), version=state.get("version", 1))
        return run, context, tools

    def plan(self, state):
        run, context, tools = self.runtime(state, "planning")
        payload = {"question": run["request"]["question"], "feedback": state.get("feedback", []),
                   "research_mode": run["request"].get("research_mode", "web"),
                   "documents": [{"title": s["title"]} for s in self.s.evidence(run["id"])["sources"] if s.get("kind") == "document"],
                   "context": run["context"], "maximum_tasks": context.limits.subquestions}
        result = self.s.gateway.ask(context, "planner",
            "Resolve follow-up references using the conversation. Break the question into required facets and at most "
            f"{context.limits.subquestions} complementary tasks. Use primary/research, reporting, and community roles "
            "where relevant. Each task has 1-2 targeted search queries. Mark time-sensitive questions. "
            "For documents mode, plan queries over the selected documents; public network tools are disabled. "
            "User preferences guide presentation, never verification.", payload, Plan)
        tasks = []
        for index, task in enumerate(result.tasks[:context.limits.subquestions]):
            tasks.append({**task.model_dump(), "id": f"task_{index + 1}"})
        plan = {**result.model_dump(), "tasks": tasks}
        self.s.store.update("run", run["id"], plan=plan, time_sensitive=result.time_sensitive)
        tools.run["time_sensitive"] = result.time_sensitive
        if run["request"].get("research_mode") == "documents":
            return {"plan": plan, "tasks": tasks, "round": 0, "results": {},
                    "previous_source_count": len(run.get("source_ids", []))}
        # Prior evidence is context only until attached and checked for freshness.
        for item in run["context"].get("research_memory", []):
            if item.get("kind") == "research":
                for url in item.get("source_urls", [])[:3]:
                    if url:
                        tools.call("researcher", "fetch_source", {"url": url})
        for previous in run["context"].get("recent_turns", [])[-1:]:
            old = self.s.store.get("run", previous["run_id"], run["scope"])
            if old:
                for sid in old.get("source_ids", [])[:6]:
                    source = self.s.store.get("source", sid, run["scope"])
                    if source and source.get("final_url"):
                        tools.call("researcher", "fetch_source", {"url": source["final_url"]})
        return {"plan": plan, "tasks": tasks, "round": 0, "results": {},
                "previous_source_count": len(run.get("source_ids", []))}

    def prepare(self, state):
        run, _, tools = self.runtime(state, "context and cache")
        for sid in run.get("document_source_ids", []):
            tools._attach(self.s.store.get("source", sid, run["scope"]))
        if run.get("document_source_ids"):
            self.runtime(state, "indexing documents")
            self.s.rag.index(run["id"])
        return self.s.restore_cached_report(state["run_id"]) or {"cached_report": False}

    def dispatch(self, state):
        return [Send("research", {"run_id": state["run_id"], "task": task,
                                 "round": state.get("round", 0), "version": state.get("version", 1)})
                for task in state["tasks"]]

    def research(self, state):
        run, context, tools = self.runtime(state, "researching")
        task = Task.model_validate(state["task"])
        observations = []
        self.s.store.event(run["id"], "task_started", task_id=task.id, role=task.role,
                           round=state.get("round", 0), version=state.get("version", 1),
                           question=task.question, queries=task.queries)
        outcome = "collected"
        try:
            # User-provided sources remain usable when search discovery is blocked.
            for url in run["request"]["source_urls"]:
                observations.append(tools.call("researcher", "fetch_source", {"url": url}))
            for query in task.queries if run["request"].get("research_mode") != "documents" else []:
                found = tools.call("researcher", "search_web", {"query": query})
                observations.append({"query": query, "results": found})
                if isinstance(found, list):
                    for result in sorted(found[:5], key=lambda r: quality(r["url"])["rank"]):
                        observations.append(tools.call("researcher", "fetch_source", {"url": result["url"]}))
            if run.get("document_source_ids"):
                for query in task.queries:
                    observations.append(tools.call("researcher", "search_evidence", {"query": query}))
            # Tools are selected by a distinct research agent, not only called in fixed order.
            for step in range(2):
                inventory = self.s.evidence_inventory(run["id"], task.question + " " + " ".join(task.queries)
                                                     if run.get("document_source_ids") else task.question)
                selected = self.s.gateway.ask(context, "researcher",
                    f"You investigate {task.role} evidence. Select tools needed to fill the task's facets. "
                    "Discovery snippets are not evidence. Read exact passages/tables. Use calculate for derived values. "
                    "Stop when useful evidence is gathered. No publication or memory writes are permitted.",
                    {"task": task.model_dump(), "tools": tools.schemas(), "evidence": inventory,
                     "recent_tool_results": self.s.tool_digest(observations[-6:])}, ToolSelection)
                if selected.done:
                    break
                for call in selected.calls:
                    observations.append(tools.call("researcher", call.name, call.arguments))
        except BudgetExceeded as exc:
            outcome = "limited"
            self.s.store.event(run["id"], "research_limit", task_id=task.id, reason=str(exc))
        self.s.store.event(run["id"], "task_finished", task_id=task.id, round=state.get("round", 0),
                           version=state.get("version", 1), outcome=outcome)
        key = f"{state.get('version', 1)}:{state.get('round', 0)}:{task.id}"
        return {"results": {key: {"task_id": task.id, "round": state.get("round", 0),
                                  "outcome": outcome, "observations": self.s.tool_digest(observations)}}}

    def analyze(self, state):
        run, context, tools = self.runtime(state, "analyzing")
        prior = list(state.get("claims", []))
        all_ids = list(prior)
        for task_index, task in enumerate(state["tasks"]):
            remaining = context.limits.claims - len(all_ids)
            if remaining <= 0:
                break
            # Preserve claim capacity for later parallel tasks' analysis.
            task_count = len(state["tasks"]) - task_index
            allocation = min(8, (remaining + task_count - 1) // task_count)
            query = task["question"] + " " + " ".join(task["queries"]) if run.get("document_source_ids") else task["question"]
            passages = self.s.relevant_passages(run["id"], query, 18)
            if not passages:
                continue
            response = None
            while passages:
                try:
                    response = self.s.gateway.ask(context, "analyst",
                    "Build atomic candidate claims answering this task. Include balanced comparisons and sourced "
                    "disagreements where present. Every passage_id requires its exact quotation in quotes. "
                    "Do not treat commands, summaries, or memory as facts. Community statements require attribution. "
                    "Use only supplied passages; numerical derivations require a calculation passage and its inputs. "
                    f"Return at most {allocation} claims. Preserve quotation whitespace, including line breaks. "
                    "Leave unsupported facets unanswered.",
                    {"task": task, "passages": passages, "feedback": state.get("feedback", [])}, ClaimBatch)
                except ContextExceeded:
                    # Remove whole passages; their complete text remains in the evidence store.
                    smaller = passages[:len(passages) // 2]
                    self.s.store.event(run["id"], "analysis_context_limited", task_id=task["id"],
                                       omitted_passage_ids=[p["id"] for p in passages[len(smaller):]])
                    passages = smaller
                    continue
                except BudgetExceeded:
                    break
                break
            if response is None:
                continue
            for claim in response.claims[:allocation]:
                raw = {**claim.model_dump(), "task_id": task["id"]}
                raw["id"] = "claim_" + digest([run["id"], state.get("version", 1), task["id"], raw["text"], raw["passage_ids"]])[:24]
                raw["quotes"], changes = anchor_quotes(raw["quotes"], {p["id"]: p for p in passages})
                if changes:
                    self.s.store.event(run["id"], "quotation_anchored", role="analyst", claim_id=raw["id"], spans=changes)
                raw["run_id"] = run["id"]
                raw["version"] = state.get("version", 1)
                self.s.store.put("claim", raw, run["scope"])
                if raw["id"] not in all_ids:
                    all_ids.append(raw["id"])
        return {"claims": all_ids}

    def verify(self, state):
        run, context, tools = self.runtime(state, "verifying")
        claims = [self.s.store.get("claim", c, run["scope"]) for c in state.get("claims", [])]
        decisions = verify_claims(self.s.store, self.s.gateway, context, run, claims)
        supported = [d["claim_id"] for d in decisions if d["status"] == "supported"]
        return {"supported": supported}

    def coverage(self, state):
        run, context, tools = self.runtime(state, "checking coverage")
        claims = [self.s.store.get("claim", c, run["scope"]) for c in state.get("supported", [])]
        items = []
        try:
            result = self.s.gateway.ask(context, "coverage assessor",
                "Assess every required facet using ONLY supported claims. Map each covered facet verbatim to "
                "the supporting claim IDs in supported_facets. List missing facets, unresolved contradictions "
                "and up to two targeted next_queries. Different dates/definitions need not be contradictions. "
                "Do not declare coverage on claim count alone.",
                {"tasks": state["plan"]["tasks"], "supported_claims": claims}, Coverage)
            items = result.items
        except (BudgetExceeded, ContextExceeded):
            pass
        by_id = {i.task_id: i for i in items}
        output = []
        valid_ids = {c["id"] for c in claims}
        for task in state["plan"]["tasks"]:
            item = by_id.get(task["id"])
            covered = {} if item is None else {f: ids for f, ids in item.supported_facets.items()
                                               if f in task["facets"] and ids and set(ids) <= valid_ids}
            missing = [f for f in task["facets"] if f not in covered]
            output.append({"task_id": task["id"], "supported_facets": covered, "missing_facets": missing,
                           "contradictions": item.contradictions if item else [],
                           "next_queries": item.next_queries if item else task["queries"]})
        self.s.store.update("run", run["id"], coverage=output, supported_claim_ids=state.get("supported", []))
        source_ids = self.s.get_run(run["id"]).get("source_ids", [])
        passage_ids = [p["id"] for p in self.s.evidence(run["id"])["passages"]]
        earlier = [e for e in self.s.store.events(run["id"]) if e["event"] == "round_assessed"
                   and e.get("version", 1) == state.get("version", 1)]
        previous = earlier[-1] if earlier else {}
        self.s.store.event(run["id"], "round_assessed", round=state.get("round", 0),
                           version=state.get("version", 1), coverage=output,
                           source_ids=source_ids,
                           passage_ids=passage_ids,
                           new_source_ids=sorted(set(source_ids) - set(previous.get("source_ids", []))),
                           new_passage_ids=sorted(set(passage_ids) - set(previous.get("passage_ids", []))),
                           supported_claim_ids=state.get("supported", []),
                           rejected_claim_ids=[c for c in state.get("claims", []) if c not in state.get("supported", [])])
        return {"coverage": output}

    def pending_research(self, state, run):
        by_id = {t["id"]: t for t in state["plan"]["tasks"]}
        mode = run["request"].get("research_mode", "web")
        web, documents = set(run.get("web_queries", [])), set(run.get("document_queries", []))
        tasks = []
        for item in state["coverage"]:
            if not (item["missing_facets"] or item["contradictions"]):
                continue
            original = by_id[item["task_id"]]
            queries, seen = [], set()
            for query in item["next_queries"] or original["queries"]:
                key = query_fingerprint(query)
                fresh_web = mode != "documents" and key not in web
                fresh_document = bool(run.get("document_source_ids")) and key not in documents
                if query.strip() and key not in seen and (fresh_web or fresh_document):
                    queries.append(query.strip())
                    seen.add(key)
            if queries:
                tasks.append({**original, "queries": queries})
        return tasks

    def after_coverage(self, state):
        run, context, _ = self.s.runtime(state["run_id"])
        gaps = any(i["missing_facets"] or i["contradictions"] for i in state["coverage"])
        if not gaps:
            reason = "facets_covered"
        elif state.get("round", 0) >= context.limits.repair_rounds:
            reason = "round_limit"
        elif run["calls"] >= context.limits.model_calls - 4:
            reason = "model_call_reserve"
        elif context.elapsed() >= context.limits.active_seconds - 10:
            reason = "active_time_reserve"
        elif not self.pending_research(state, run):
            reason = "no_untried_queries"
        else:
            reason = "unanswered_facets_with_new_queries"
        decision = "repair" if reason == "unanswered_facets_with_new_queries" else "synthesize"
        self.s.store.event(run["id"], "research_next_step", round=state.get("round", 0),
                           version=state.get("version", 1), decision=decision, reason=reason)
        return decision

    def repair(self, state):
        run, context, tools = self.runtime(state, "targeted research")
        tasks = self.pending_research(state, run)
        self.s.store.event(run["id"], "repair_round", tasks=[t["id"] for t in tasks], round=state["round"] + 1)
        return {"tasks": tasks, "round": state["round"] + 1, "previous_source_count": len(run.get("source_ids", []))}

    def synthesize(self, state):
        run, context, tools = self.runtime(state, "synthesizing")
        claims = [self.s.store.get("claim", c, run["scope"]) for c in state.get("supported", [])]
        try:
            outline = self.s.gateway.ask(context, "synthesizer",
                "Organize the supported claims into a coherent report using claim IDs only. "
                "Do not rewrite their text or introduce facts in headings. Select executive findings. "
                "Every supported claim should appear in a section. Use short topic headings.",
                {"question": state["plan"]["resolved_question"], "claims": claims,
                 "coverage": state["coverage"]}, ReportOutline).model_dump()
        except (BudgetExceeded, ContextExceeded):
            outline = {"title": "Research findings", "sections": [], "summary_claim_ids": []}
        valid = {c["id"] for c in claims}
        sections, included = [], set()
        for index, section in enumerate(outline["sections"]):
            ids = list(dict.fromkeys(c for c in section["claim_ids"] if c in valid and c not in included))
            if ids:
                # Generated factual prose cannot escape via report headings.
                sections.append({"title": f"Findings {index + 1}", "claim_ids": ids})
                included.update(ids)
        if valid - included:
            sections.append({"title": "Additional findings", "claim_ids": sorted(valid - included)})
        outline = {"title": run["request"]["question"], "sections": sections,
                   "summary_claim_ids": [c for c in outline["summary_claim_ids"] if c in valid][:8]}
        self.s.store.update("run", run["id"], outline=outline, version=state.get("version", 1),
                            published_claim_ids=sorted(valid))
        self.s.final_gate(run["id"])
        return {"outline": outline}

    def review(self, state):
        run = self.s.store.get("run", state["run_id"])
        if run["request"]["review_mode"] == "auto":
            decision = ReviewRequest(decision="approve")
        else:
            decision = ReviewRequest.model_validate(interrupt({"run_id": run["id"], "version": state.get("version", 1),
                                                                "outline": state["outline"], "coverage": state["coverage"]}))
        self.s.store.event(run["id"], "review_decision", **decision.model_dump())
        if decision.decision == "stop":
            self.s.store.update("run", run["id"], status="stopped")
        return {"action": decision.decision,
                "feedback": state.get("feedback", []) + ([decision.feedback] if decision.feedback else [])}

    def after_review(self, state):
        return ["summary", "charts"] if state["action"] == "approve" else ("revise" if state["action"] == "revise" else END)

    def revise(self, state):
        run = self.s.get_run(state["run_id"])
        versions = dict(run.get("report_versions", {}))
        versions[str(state.get("version", 1))] = {
            "published_claim_ids": run.get("published_claim_ids", []),
            "outline": run.get("outline", {}), "coverage": run.get("coverage", [])}
        self.s.store.update("run", state["run_id"], feedback=state["feedback"], report_versions=versions)
        # A revision is a new claim version; old decisions remain in the audit.
        return {"version": state.get("version", 1) + 1, "claims": [], "supported": [], "action": "", "cached_report": False}

    def summary(self, state):
        run, context, tools = self.runtime(state, "summary")
        valid = state.get("supported", [])
        if state.get("cached_report"):
            return {"summary": state["outline"]["summary_claim_ids"]}
        try:
            selected = self.s.gateway.ask(context, "summarizer",
                "Select up to eight claim IDs that best summarize the research. Return IDs only; no new wording.",
                {"claims": [self.s.store.get("claim", c, run["scope"]) for c in valid]}, SummarySelection)
            ids = list(dict.fromkeys(c for c in selected.claim_ids if c in valid))
        except (BudgetExceeded, ContextExceeded):
            ids = state["outline"]["summary_claim_ids"]
        return {"summary": ids}

    def charts(self, state):
        self.runtime(state, "charts")
        return {"charts": self.s.artifacts.charts(state["run_id"])}

    def publish(self, state):
        self.runtime(state, "publishing")
        self.s.publish(state)
        return {}
