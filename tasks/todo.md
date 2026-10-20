# DeepDive completion checklist

The user removed the calendar target and added end-to-end operations. The approved
design remains in [PLAN.md](../PLAN.md); subsequent scope and decisions are recorded
in [docs/decisions.md](../docs/decisions.md). Checkmarks describe implemented and
locally verified behavior, not unperformed live model evaluation.

## Application

- [x] Shared service, typed public contracts, Mesh-only model gateway
- [x] Role-scoped tools, public retrieval, immutable evidence snapshots
- [x] Parallel research, sequential analysis/verification, coverage-driven repair
- [x] Deterministic/semantic gates and claim-version checks before publication
- [x] Durable approve/revise/stop, cooperative cancellation, explicit checkpoint resume
- [x] Bounded session context, explicit preferences, project research memory
- [x] Scoped caches, freshness checks, refresh and invalidation
- [x] Streamlit, HTTP API, and CLI use the same execution/review path
- [x] History search/rename/delete/export and memory inspect/edit/forget controls
- [x] HTML/Markdown reports, HTML/PPTX decks, PNG charts/tables, JSON audit exports
- [x] Offline end-to-end workflow and UI approval tests
- [x] Document upload/parsing, page/section chunks, scoped BM25 + Mesh vectors + rank fusion + Mesh reranking
- [x] Document-only tool restrictions, mixed research, library management, and cited review/export integration
- [x] Versioned retrieval evaluation command and dataset, with per-stage ranking metrics

## Operations and documentation

- [x] Docker image and Compose API/UI deployment with persistent storage
- [x] Authenticated readiness/metrics, structured logs, sanitized local error records
- [x] Optional Sentry adapter with privacy-filter tests
- [x] Prometheus/Grafana configuration and four validated alert rules
- [x] Backup/restore commands; restored review resumes and produces exports in tests
- [x] GitHub quality, protected live-evaluation, and release-image workflows
- [x] Versioned datasets, experiment manifests, live release gate, calibration/drift
- [x] README architecture/workflow/review diagrams and setup/run instructions
- [x] Architecture, API, operations, evaluation, decision, and contribution guides
- [x] Official organizer workflow retrieved, inspected, and installed unchanged
- [x] Rebuilt Docker API/UI smoke check and local/CI monitoring HTTP/data checks
- [x] Public repository and successful remote Quality workflow: [Ben-cod1ng/DeepDive](https://github.com/Ben-cod1ng/DeepDive)

## Measured validation

Railway demo work is tracked separately from the independent accuracy release gate:

- [x] Private API hostname support, volume initialization with privilege drop, and shared demo login
- [x] Two-service setup and acceptance instructions in [the Railway guide](../docs/railway.md)
- [x] 69 local tests passed; container validation covered Railway volume permissions, IPv4/IPv6, restart persistence, and private frontend/API access: [evidence](../evaluation/results/railway-smoke-local.json)
- [x] Hosted frontend/backend deployment and public HTTPS browser acceptance: login, synthetic document research with live Mesh, approval, seven downloads, backend restart persistence, and logout ([record](../evaluation/results/railway-hosted.json))

- [x] Full local suite passed (62 tests); final document/retrieval regressions passed (21 tests), including calculation evidence and backup restoration
- [x] Document RAG CI passed all 63 tests, dependency audit, deployed document ingestion/removal, and monitoring checks: [recorded run](https://github.com/Ben-cod1ng/DeepDive/actions/runs/39120462295)
- [x] Local RAG Docker upgrade passed after a checksummed backup; deployed source fingerprint matched tested code, document HTTP smoke passed, and all monitoring checks passed: [validation record](../evaluation/results/document-rag-validation.json), [monitoring evidence](../evaluation/results/document-rag-monitoring-local.json)
- [x] Ruff, Pydantic-contract type checks, and compilation passed
- [x] Dependency audit reported no known vulnerabilities after targeted upgrades
- [x] Recorded 32-case deterministic fixture evaluation, dataset version 1.1
- [x] Actual public page retrieval and search succeeded; no AI calls used
- [x] Docker API/UI health and HTTP persistence/memory/export smoke check passed
- [x] Live Mesh verifier evaluation on October 19, 2026: 32 cases, 11 TP / 21 TN / 0 FP / 0 FN against authored labels; independent review remains pending
- [x] Live Mesh embeddings/reranking retrieval ablation: seven queries, all reranked, final Recall@3 / MRR@3 / nDCG@3 = 1.0 on the ten-document synthetic set
- [ ] Independent review of retrieval labels and representative held-out retrieval evaluation
- [ ] Independent review of the 32 authored reference labels
- [ ] Ten-question live benchmark and independent final-claim review
- [x] Scripted live document research/revise/restart/export acceptance through the deployed manual-review API; eight artifacts validated, with three recorded rerank fallbacks and a repeated finding
- [ ] Independent human review of live reports and complete web research acceptance
- [ ] Optional live Sentry delivery, if used
- [x] GitHub environment reviewers and required main-branch application checks configured
- [ ] Release-image publication verified after live evaluation gates pass
- [ ] Organizer secrets/check execution and dashboard submission acceptance

Live verifier precision/recall and retrieval rankings have now been measured on
the authored synthetic sets. Independent label review and independent final-report
accuracy remain pending; fixture results and the verifier's own acceptance rate
cannot fill those gaps. See [evaluation/results](../evaluation/results) for recorded
evidence and [docs/evaluation.md](../docs/evaluation.md) for the live procedure.

Docker recovery completed after the user approved the restart and freed over 9 GB
on C:. The first restart reported a stale WSL data-disk attachment; shutting WSL
down released it. A metadata inspection recognized the existing ext4 filesystem,
then Docker started successfully with the existing named volumes. No disk reset,
deletion, or formatting command was issued. This was a successful startup and
application smoke check, not a full filesystem integrity scan.

The expanded [Quality run](https://github.com/Ben-cod1ng/DeepDive/actions/runs/39046064658)
passed both jobs. Prometheus scraping, all four alert rules, Grafana's datasource,
and all eight panel queries passed in CI and on the recovered Windows host. Results
are in [monitoring-ci.json](../evaluation/results/monitoring-ci.json) and
[monitoring-local.json](../evaluation/results/monitoring-local.json). The
[host recovery runbook](../docs/operations.md#docker-desktop-or-wsl-does-not-start)
records how to diagnose this class of failure while preserving stored data.
