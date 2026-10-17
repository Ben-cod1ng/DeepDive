# Scope addendum and engineering decisions

The original approved [PLAN.md](../PLAN.md) remains unchanged as the historical design. This document records subsequent user instructions and concrete implementation choices rather than silently rewriting that approval.

## Scope update: end-to-end delivery

The user removed the calendar deadline and explicitly requested MLOps, CI/CD, Docker deployment, error tracking, and monitoring. Delivery is now driven by acceptance checks, not the original October milestones. The research timeline remains a product audit feature.

This extends the original local-only delivery scope with container packaging, a local Compose deployment, optional Prometheus/Grafana, sanitized optional Sentry, operational recovery commands, versioned evaluation manifests, and GitHub quality/live/release workflows. A remote destination and valid GitHub credentials are external setup requirements; no host or cloud account is invented.

## One owner, one service

**Decision:** retain FastAPI, Streamlit, LangGraph, and SQLite with one process owning persistence and one run worker. Parallel research happens within the graph. API/UI/CLI call the same service.

**Why:** the approved project is a local research workspace. This supports durable review and straightforward recovery without adding a distributed job broker, Redis, Kubernetes, or multiple independently deployed services. A service token protects local API operations; it is not multi-user authentication.

## Security-driven dependency update

**Decision:** update the original compatible LangGraph-related packages and dotenv after the dependency audit identified known vulnerabilities. The resolved versions are recorded in `uv.lock`; the workflow compatibility tests were rerun.

**Why:** starting from the old project did not justify shipping known vulnerable package versions. This is a targeted security exception to the original preference against unrelated framework upgrades. The new dependency set passed the recorded audit at the time checked; future advisories still require review.

## Exact claims at the publication boundary

**Decision:** synthesis and summary models select verified claim IDs. The renderer retains their exact text and citations, supplies safe generic headings, and rechecks the claim hash, evidence, freshness, and verdict before publication.

**Why:** free-form rewriting after verification could introduce new facts in report headings, summaries, or slide bullets. ID selection eliminates that extra factual-writing surface. It replaces the planned free-form correction pass with a narrower deterministic publication contract. The tradeoff is less editorial variation and more generic section headings.

Coverage-assessor disagreement descriptions are labeled unverified limitations. Supported opposing claims can both appear with their own citations, but an assessor's explanation is not automatically promoted to a verified factual claim.

## Memory and caches

**Decision:** use bounded context, explicit preferences, source-linked project memory, SQLite FTS retrieval, and scoped exact caches. Reverify stale evidence and bind report reuse to context, preferences, model, and a code fingerprint.

**Why:** these mechanisms address repetitive work without an extra AI provider or embedding service. They keep the Mesh-only requirement clear and prevent a semantically similar but materially different question from silently receiving an old report.

## Measured model operations

**Decision:** version datasets and hash the code/lockfile in evaluation manifests. Require independent labels, live precision at least 0.90, and a 0.50 recall floor before releasing an image. Keep fixture evaluation clearly separate.

**Why:** schema validity, self-reported confidence, and the verifier's own acceptance rate do not measure correctness. The recall floor prevents empty output from passing solely by avoiding false positives. The 32 authored cases need independent review before this gate is satisfied.

No model is trained by this application. Its model operations concern evaluation, prompt/code changes, hosted-model configuration, monitoring, and rollback.

## Observability and privacy

**Decision:** keep detailed research evidence in the local audit store; emit only allowlisted operational metadata to logs/metrics/error telemetry. Sentry is optional and sanitized. Prometheus labels have bounded operational dimensions.

**Why:** debugging needs request/run correlation, latency, failures, and usage without turning third-party telemetry into a copy of research prompts or secrets. Local backups and exports still contain private research and must be handled accordingly.

## Deployment and delivery

**Decision:** use a locked, non-root container, persistent research volume, read-only root filesystem, loopback ports, readiness checks, stopped-service backup/restore, and immutable published image digests. Build one image for both API and UI commands.

**Why:** this is sufficient for the single-owner target and makes the same build runnable in CI and on a chosen Docker host. Remote host provisioning, TLS/account integration, branch protections, environment reviewers, and external alert recipients must be configured against actual accounts rather than represented by nonfunctional placeholders.

## Organizer checks

The organizer's workflow was retrieved and inspected on 2026-10-17 and installed unchanged at `.github/workflows/agent-arena-2026-checks.yml`. It requests GitHub OIDC, downloads the organizer's check script using `SUBMISSION_TOKEN`, and supplies Mesh/submission credentials to that script. The script itself is served only to authenticated GitHub Actions; it has not been fetched or executed locally.

Source: [official workflow](https://careerapi-production.krishnaik.in/api/ci/hackathons/agent-arena-2026/workflow.yml). Downloaded SHA-256: `f34c4aba59caa82bd992a3fb77b51c0c94d3d2c3551acb55f72ddaf4e6e91436`.
