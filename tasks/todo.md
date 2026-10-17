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
- [ ] Final rebuilt Docker image smoke check and live monitoring dashboard check
- [x] Public repository and successful remote Quality workflow: [Ben-cod1ng/DeepDive](https://github.com/Ben-cod1ng/DeepDive)

## Measured validation

- [x] 41 tests passed locally, including gateway budgets, API boundaries, recovery, UI review, and monitoring failure detection
- [x] Ruff, Pydantic-contract type checks, and compilation passed
- [x] Dependency audit reported no known vulnerabilities after targeted upgrades
- [x] Recorded 32-case deterministic fixture evaluation, dataset version 1.1
- [x] Actual public page retrieval and search succeeded; no AI calls used
- [x] Docker API/UI health and HTTP persistence/memory/export smoke check passed
- [ ] Live Mesh verifier evaluation; intentionally pending credentials
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

Current local host issue: the user approved restarting Docker Desktop. The normal
restart timed out; relaunch then reported an already-attached WSL data disk and
failed filesystem bootstrap. Desktop was stopped and WSL shut down after confirming
only `docker-desktop` was installed. No disk reset, deletion, or formatting command
was issued. C: had approximately 326 MB free, so Docker remains stopped while host
space is recovered. Filesystem integrity and the final local smoke remain unverified.
The clean Linux API/UI deployment passed in this recorded
[Quality run](https://github.com/Ben-cod1ng/DeepDive/actions/runs/39044152610).
CI now also starts monitoring and checks its actual HTTP/data path; its first
expanded run is pending. See the [host recovery runbook](../docs/operations.md#docker-desktop-or-wsl-does-not-start).
