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

## Hosted demo extension

The user requested a free or temporary hosted hackathon demo that reviewers can
use without the owner's laptop. Railway hosts two services from the same image:
a public Streamlit frontend and a private FastAPI backend with a `/data` volume.
This extends the original local deployment boundary. A separate shared demo code
protects the frontend; visitors share documents, research, and the owner's Mesh
budget. This does not provide separate accounts or tenant isolation.

Railway supplies root-owned volumes. The API initializer changes only the mount
directory's owner and then drops permanently to UID/GID 10001 before importing
the service. The backend requires a persistent-volume configuration and an
internal token; it accepts the exact Railway private hostname and deployment
healthcheck hostname. A dual-stack listener supports internal IPv6 and IPv4.
The frontend has no Mesh key and contacts the backend only after demo login.
See [the deployment guide](railway.md) for settings and acceptance steps.

The demo does not bypass the independently reviewed accuracy gate for publishing
release images. Prior live evaluation records retain their original code hashes;
hosted acceptance must be recorded separately.

## Organizer checks

The organizer's workflow was retrieved and inspected on 2026-10-17 and installed unchanged at `.github/workflows/agent-arena-2026-checks.yml`. It requests GitHub OIDC, downloads the organizer's check script using `SUBMISSION_TOKEN`, and supplies Mesh/submission credentials to that script. The script itself is served only to authenticated GitHub Actions; it has not been fetched or executed locally.

Source: [official workflow](https://careerapi-production.krishnaik.in/api/ci/hackathons/agent-arena-2026/workflow.yml). Downloaded SHA-256: `f34c4aba59caa82bd992a3fb77b51c0c94d3d2c3551acb55f72ddaf4e6e91436`.

## Document RAG extension — October 18, 2026

**Authorization:** the user requested uploaded-document RAG with chunking, hybrid BM25/vector search, reranking, and updated documentation. This extends the original plan's explicit document-upload exclusion; the historical approved plan remains unchanged.

**Decision:** parse PDF/DOCX/TXT/Markdown locally; preserve immutable page/section passages; use 384-token windows with up to 64-token overlap; combine FTS5 BM25 and exact normalized-vector search with reciprocal rank fusion; rerank up to 16 candidates through the existing Mesh chat gateway. Mesh embeddings use the documented `/v1/embeddings` endpoint. Generation, embedding, and reranking attempts share the run budget and audit.

**Why:** the existing SQLite owner and backup model can support a bounded local corpus without a separate vector service. Structure-aware windows preserve citation locations; hybrid retrieval supports exact identifiers and semantic matches. No single chunk size is universally best, so the implementation includes a versioned retrieval ablation rather than an unmeasured quality claim. See [RAG design and references](rag.md).

**Boundaries:** document-only mode disables public network tools; mixed mode explicitly allows document-derived public queries. Uploads work without credentials; real hybrid retrieval requires Mesh. No local model bypass, OCR, approximate-neighbor index, or hosted document service is added. Removed library items retain historical citations until their owning research is deleted. Live accuracy and independent label review remain pending.

## Competition quality extension — October 20, 2026

**Authorization:** the user approved the full [competition plan](../tasks/competition-plan.md)
subject to using only existing Mesh credit and retaining half its measured balance
for demos. No additional credits or hosting upgrade are authorized.

**Decisions:** allow bounded fresh-query repair after an empty first round, persist
normalized attempts and round decisions, and present exact stored evidence through
native Streamlit controls. Deduplicate only identical text and claim kind while
preserving all claim IDs and passage references. Compare revisions using text,
kind, and cited IDs; do not rewrite verified claims for presentation.

Freeze research/reference cases before live evaluation. A persistent evaluation
ledger guards every request/retry using conservative cost bounds and a current
balance check. Benchmarks use an isolated store, per-case checkpoints, and strict
configuration identity on resume. Human review remains separate from planner
coverage and automated verifier output. The existing release gate is unchanged.

Retain 384-token chunks with 64-token overlap: the larger public-document audit
found complete labeled quotations in the existing chunks, so no measured boundary
failure justified a tuning sweep. Passage-level metrics and fallback categories
make remaining retrieval weaknesses observable. Real recorded examples retain
their original provenance and make no API/model calls when inspected.

The October 20 CI dependency audit reported nine advisories in Pillow 12.3.0 and
certifi 2026.7.22. Patched targets are Pillow 13.0.0 and certifi 2026.10.19. Streamlit
1.56.0 (and latest 1.66.0 when checked) still declares Pillow <13. A narrow uv
dependency override selects Pillow 13 while keeping the tested Streamlit release;
our UI, chart, image and PowerPoint export checks must pass before deployment.
This does not claim upstream support for every Streamlit/Pillow combination.
Remove the override when Streamlit permits the patched major version. The audit
does not ignore or suppress the reported advisories.
