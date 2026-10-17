# DeepDive: Adapt the Existing Research Assistant for Agent Arena

## 1. Objective and Codebase Decisions

Build a separate `DeepDive` project beside `Advanced-LangGraph-Implementation`. Preserve the existing application’s breadth and presentation quality while making research traceable, verifiable, and compliant with the hackathon.

**Delivery target:** a complete local Streamlit application, FastAPI backend, CLI, public GitHub repository, reproducible evaluation, and submission documentation by **October 20, 2026**. Aim for submission readiness on October 19; the brief does not specify the exact closing time or timezone.

The existing project contains approximately 6,500 lines of Python across orchestration, retrieval, UI, evaluation, and artifacts. Reuse useful implementation and design work, with the following changes:

| Existing capability | Planned treatment |
|---|---|
| LangGraph workflow | Retain; add planning, source verification, bounded research loops, and durable review checkpoints. |
| Google/Bing research agents | Replace provider-specific identities with complementary research roles using public search and page retrieval. |
| Reddit discovery and analysis | Retain public-discussion research; distinguish reported opinions from independently supported facts. |
| Research-site scraper | Preserve extraction of text, tables, and media metadata; choose domains relevant to the question. |
| Stock data and charts | Retain for relevant questions; validate symbols and remove the automatic AAPL fallback. |
| Deep analysis and synthesis | Preserve depth; require evidence references for factual findings and derived conclusions. |
| Human review | Preserve approve, revise, and stop through the same graph in every interface. |
| Streamlit history and downloads | Preserve search, rename, delete, export, evaluation trends, and per-turn artifacts. |
| HTML presentations and PNG charts | Preserve and strengthen citation support; expose the existing PowerPoint exporter with equivalent provenance. |
| Evaluation and calibration | Retain rubric scoring, human-label calibration, and drift tracking; replace the current grounding calculation. |
| Bright Data snapshot utilities | Exclude from the new runtime because retrieval will not require Bright Data. |

The current evaluator accepts similarity to generated analyses as evidence. The current prompts cite source categories rather than specific passages. These must change before any report is described as verified.

## 2. Research Architecture and Evidence Contract

### Stack and Mesh compliance

- Retain Python 3.13, FastAPI, Streamlit, LangGraph, Pydantic, pandas, Matplotlib, and the existing presentation libraries.
- Start from the existing lockfile’s compatible package versions. Add explicit dependencies for imported libraries and SQLite checkpointing; avoid an unrelated framework upgrade.
- Use one lazily initialized model factory for all agents, conversational replies, revisions, and evaluation.
- Configure `MESH_API_KEY`, `MESH_MODEL`, and the fixed gateway `https://api.meshapi.ai/v1`. Default to the brief’s `openai/gpt-4o`, subject to an early account-access smoke test. The gateway matches [Mesh’s published integration](https://meshapi.ai/).
- Validate structured responses with Pydantic. Permit one formatting-repair attempt; record failures rather than silently accepting malformed output.
- Keep credentials in a gitignored environment file. Startup, health checks, and offline tests must work without a live key.

### Public retrieval

- Implement public search discovery through DuckDuckGo HTML, with Lite as an alternate parser path. These interfaces are documented by [DuckDuckGo](https://safe.duckduckgo.com/duckduckgo-help-pages/features/non-javascript); live reliability must be checked early.
- Fetch discovered pages directly and extract readable text, tables, publication metadata when available, and canonical links.
- Support optional user-supplied source URLs, allowing research to proceed from known sources when discovery is blocked.
- Keep the existing public stock-data adapter behind the same retrieval interface. Only invoke it for a validated instrument relevant to the question.
- Treat search snippets as discovery material. A claim requires retrieved page text or a captured structured-data response.
- Use timeouts, bounded retries, per-domain pacing, deduplication, response-size limits, and explicit blocked/unavailable outcomes.
- Restrict fetching to public HTTP(S) destinations, including redirect checks. Treat retrieved text as evidence, never as instructions to agents.
- Do not interpret chart images or infer plotted values from media metadata.

### Persistent research records

Use SQLite for sessions, runs, evidence, claims, decisions, events, and artifact metadata. Store generated files under run-specific directories.

Core records:

- **ResearchTask:** subquestion, required facets, queries, assigned role, and completion status.
- **SourceSnapshot:** requested/final URL, title, retrieval time, publication time if known, content hash, extracted content, and retrieval status.
- **EvidencePassage:** source snapshot ID, passage text or table rows, and location within the snapshot.
- **Claim:** atomic statement, subquestion, evidence references, and whether it is factual, attributed opinion, or derived.
- **VerificationDecision:** claim version, supported/unsupported/contradicted/insufficient status, supporting passages, and concise rationale.
- **RunEvent:** sequence number, stage, task, attempt, duration, outcome, and relevant record IDs.
- **Artifact:** report version, artifact type, file location, and supporting claim/data references.

Source snapshots remain immutable. Changed source content creates a new snapshot. Audit logs retain rejected claims and previous revisions without storing private model reasoning.

### Agent workflow

```text
Question + relevant session evidence
                ↓
Planner
                ↓
Parallel research tasks
(primary/research sources, broader reporting, community perspectives)
                ↓
Evidence aggregation and source assessment
                ↓
Analyst: candidate claims, comparisons, contradictions
                ↓
Claim verifier + coverage assessment
                ↓
Missing evidence? ── yes ──→ targeted research → verification
                │
                no / limits reached
                ↓
Synthesizer → final report verification
                ↓
Human review: approve / revise / stop
                ↓
Parallel summary and chart preparation
                ↓
Verified report, presentation, and downloads
```

- Roles have distinct prompts, inputs, and outputs. Retrieval functions remain tools rather than being counted as separate reasoning agents.
- Use LangGraph task dispatch and reducers for concurrent research; aggregate by stable IDs and use explicit completion barriers.
- Default to four subquestions, at most six, and three concurrent researchers. Each task starts with up to two queries and five candidate results per query.
- Cap a run at 30 fetched sources, 40 candidate claims, two additional research rounds, 80 model requests including retries, and 15 minutes of active execution. Review waiting time does not count. Expose these limits in settings and record them with the run.
- Trigger more research when required facets remain unanswered, central claims lack support, or material contradictions remain unresolved. Do not equate a fixed claim count with completeness.
- Stop repeated research when it produces no new usable evidence. Publish supported findings with explicit gaps; return an insufficient-evidence result when nothing substantive is supported.

### Verification and synthesis

- Check citation existence and quotation accuracy deterministically before model-based support checking.
- Have the verifier judge the exact claim against its cited passages, including dates, quantities, units, attribution, and qualifications.
- Prefer primary evidence through an explicit, visible ranking: primary material, reputable secondary reporting, then community or unidentified sources. Rank affects retrieval priority; it never overrides failed verification.
- Retain disagreements with citations to both positions. Different dates or definitions must be considered before labeling a contradiction.
- Keep derived metrics linked to inputs and formulas. Compute numerical transformations in code.
- Build reports with executive findings, subquestion sections, comparisons, disagreements, limitations, and references.
- Recheck final report wording and factual summary/slide bullets. Allow one correction pass; omit remaining unsupported statements.
- Label model-verified results accurately. Evaluation must measure verifier mistakes rather than implying that a passing decision guarantees truth.

## 3. Application, Review, and Output Behavior

### One execution path

Streamlit currently stops the graph and manually calls later agents. Replace this with durable LangGraph checkpoints and review interrupts, using `run_id` as the thread identifier. This follows the framework’s documented [interrupt/resume mechanism](https://docs.langchain.com/oss/python/langgraph/interrupts).

- All interfaces call the same research service.
- Streamlit and CLI default to manual review; unattended API/evaluation runs may explicitly select automatic approval.
- Revision requires feedback and returns through evidence checks. New factual requirements trigger research within the remaining budget.
- Stop preserves the audit record and marks the run stopped.
- Refreshing the UI must not lose a pending review. Restarted execution resumes explicitly from a checkpoint and records any retried external call.
- Serialize mutations to each run and prevent duplicate artifact generation after resume.

### Interfaces

Preserve existing health, session, and chat capabilities. Add:

| Interface | Behavior |
|---|---|
| `POST /runs` | Start research with question, optional session/source URLs, limits, and review mode; return run ID. |
| `GET /runs/{id}` | Return status, progress, coverage gaps, and available results. |
| `GET /runs/{id}/events?after=…` | Return ordered incremental audit events for UI polling. |
| `GET /runs/{id}/evidence` | Return source and passage records. |
| `GET /runs/{id}/claims` | Return claim versions and verification decisions. |
| `POST /runs/{id}/review` | Approve, revise with feedback, or stop a waiting run. |
| `POST /runs/{id}/cancel` | Request cooperative cancellation. |
| `POST /runs/{id}/resume` | Resume an interrupted run from its persisted checkpoint. |
| `GET /runs/{id}/artifacts/{artifact_id}` | Download an artifact belonging to that run. |

Keep `/chat` as a compatibility wrapper around the same service, preserving its existing response fields and adding run/status information. Missing sessions return 404; invalid review transitions return 409; malformed input returns 422.

### UI and feature parity

- Preserve the current Streamlit visual foundation, session controls, chat history, review dialog, downloads, and artifact gallery.
- Add Report, Evidence, Verification, Timeline, and Artifacts views.
- Display concurrent agent activity and current research round instead of a misleading fixed-step progress count.
- Clicking a citation reveals its stored passage and original URL.
- Show rejected claims, reasons, failed retrievals, remaining gaps, model usage, and run duration.
- Follow-ups use previous source snapshots and verified claims as context. New or time-sensitive questions trigger fresh retrieval.
- Produce HTML reports, HTML slide decks, PowerPoint decks, Markdown reports, PNG charts, and JSON audit/session exports.
- Preserve evidence and insight tables. Quantitative charts require source-linked data and labeled units/date ranges.
- Scope artifact filenames by run/version to prevent collisions. Escape user and source text in HTML.
- Start the new project with empty history; the original project remains the historical reference.

## 4. Verification and Measured Evaluation

Add focused automated tests and a reproducible evaluation command.

**Core scenarios**

- Supported claim; unrelated citation; invented quotation; absent source; incorrect number, unit, or date.
- Conflicting sources and source-quality ranking.
- A weak subquestion that obtains support after another research round.
- Blocked retrieval, empty evidence, repeated queries, and exhausted budgets.
- Unsupported claims introduced during synthesis, human revision, or presentation generation.
- Parallel tasks finishing out of order without missing or duplicating results.
- Review persistence, restart/resume, cancellation, session isolation, and follow-up evidence reuse.
- Validated stock symbols; no market-data request for unrelated questions.
- Mesh configuration, response validation, retries, and coverage of every model entry point.
- Artifact provenance, HTML escaping, download boundaries, and complete session export.

**Evaluation deliverables**

- Ten research questions spanning technical, scientific, market, comparative, time-sensitive, contradictory, and insufficient-evidence cases.
- At least 30 independently labeled claim/passage examples for verifier evaluation, including difficult negatives.
- Record support precision/recall against labels, citation validity, independently assessed final-claim support, required-facet coverage, rejection counts, repair outcomes, runtime, model calls, and token usage.
- Distinguish the verifier’s own acceptance rate from independently measured correctness.
- Retain rubric evaluation, calibration export, and drift views. Replace heuristic “confidence bands” with clearly labeled scores or statistically justified intervals.
- Store test-set version, model, configuration, run date, and measured results in the repository. Keep fixture results separate from live runs.

**Release gates**

- All focused tests and compilation checks pass.
- Every published factual claim has a valid evidence reference and a recorded passing verification decision.
- No false-supported decisions in deterministic adversarial fixtures; target at least 90% precision on the labeled verifier set, reporting sample counts and failures.
- Demonstrate an actual rejected claim and an actual research repair loop.
- Complete a clean-install end-to-end run with working review, history, and downloads.
- Passing verification and completeness are reported separately so sparse reports cannot appear successful merely by dropping most claims.

## 5. Delivery Sequence and Submission

| Date | Work and acceptance checkpoint |
|---|---|
| **October 17** | Create the sibling project, establish Mesh access and public retrieval feasibility, define persistent records, and deliver one research question through retrieval, verification, and a cited report. |
| **October 18** | Complete parallel research, coverage-driven repair, contradictions, durable review, shared API/UI execution, and session persistence. Demonstrate rejection, repair, and resume. |
| **October 19** | Complete feature parity, charts and exports, follow-ups, evaluation runs, clean-install checks, README, and demonstration materials. Reach submission readiness. |
| **October 20** | Resolve measured defects, configure repository secrets, run official checks, verify the submitted commit and dashboard status, and submit before the organizer’s closing time. |

The repository must include source, locked dependencies, environment examples, setup/run instructions, architecture and agent cooperation notes, evaluation data/results, limitations, and the official workflow at `.github/workflows/agent-arena-2026-checks.yml`.

The official workflow URL could not be retrieved during planning. Retry and inspect the organizer-provided file before installation; do not substitute an invented workflow and describe it as official.

**Confirmed scope:** local application and public repository; thorough bounded research; web-first retrieval; Mesh for every AI call; no additional paid retrieval key. Hosting, authentication, uploaded-document research, and PDF export are outside this submission plan.

**External prerequisites:** Mesh access and credits for live validation, GitHub access, the submission token, and confirmation of the organizer’s precise closing time. Never place credentials or existing private session data in the public repository.
