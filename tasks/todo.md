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
- [x] Architecture, API, operations, evaluation, decision, contribution, submission guides
- [x] Official organizer workflow retrieved, inspected, and installed unchanged
- [x] Rebuilt Docker API/UI smoke check and local/CI monitoring HTTP/data checks
- [x] Public repository and successful remote Quality workflow: [Ben-cod1ng/DeepDive](https://github.com/Ben-cod1ng/DeepDive)

## Measured validation

- [x] Full local suite passed (62 tests); final document/retrieval regressions passed (21 tests), including calculation evidence and backup restoration
- [x] Ruff, Pydantic-contract type checks, and compilation passed
- [x] Dependency audit reported no known vulnerabilities after targeted upgrades
- [x] Recorded 32-case deterministic fixture evaluation, dataset version 1.1
- [x] Actual public page retrieval and search succeeded; no AI calls used
- [x] Docker API/UI health and HTTP persistence/memory/export smoke check passed
- [ ] Live Mesh verifier evaluation; intentionally pending credentials
- [ ] Live Mesh embeddings/reranking retrieval ablation and document research acceptance; independent retrieval labels
- [ ] Independent review of the 32 authored reference labels
- [ ] Ten-question live benchmark and independent final-claim review
- [ ] Manual live research/revise/restart/export acceptance demonstration
- [ ] Optional live Sentry delivery and chosen remote-host deployment, if used
- [x] GitHub environment reviewers and required main-branch application checks configured
- [ ] Release-image publication verified after live evaluation gates pass
- [ ] Organizer secrets/check execution and dashboard submission acceptance

Live semantic precision/recall and independent final-report accuracy remain
unmeasured. Fixture results and the verifier's own acceptance rate cannot fill
those gaps. See [evaluation/results](../evaluation/results) for recorded local
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
