# Agent Arena submission checklist

The implementation uses Mesh for every model request through the fixed `https://api.meshapi.ai/v1` gateway. The default model is `openai/gpt-4o`. Public search and source retrieval require no additional paid retrieval key. Tests use explicitly synthetic local responses and are not presented as live model results.

## Repository layout

Publish the contents of **DeepDive as the repository root**, with README, lockfile, source, tests, evaluation data, and `.github/workflows` at the top level. Do not publish the parent workspace or the original project's history/private configuration. Choose the repository owner/name explicitly before creating a remote repository.

The organizer workflow is installed at `.github/workflows/agent-arena-2026-checks.yml`. It was retrieved unchanged from the [official workflow URL](https://careerapi-production.krishnaik.in/api/ci/hackathons/agent-arena-2026/workflow.yml). It uses Python 3.11 for its standalone organizer script; the application and its own CI use Python 3.13. These are separate jobs.

## External steps still required

- Configure a valid local `MESH_API_KEY` and run the live verifier, ten-question benchmark, and manual review/restart/export acceptance flow.
- Obtain independent review of at least 30 reference cases and independently assess published claims. Retain counts, failures, limitations, and measured results.
- Authenticate GitHub and select a public repository owner/name. Push only reviewed project files.
- Set repository secrets `MESH_API_KEY` and `SUBMISSION_TOKEN` from the organizer dashboard. Keep both out of commits, logs, and chat.
- Configure protected live/release environments and required Quality status checks if using the delivery pipeline.
- Run the actual organizer workflow on the intended submission commit. Inspect its result and verify the dashboard records that same commit/repository.
- Supply the demonstration and submission fields requested by the organizer. Confirm acceptance on the dashboard; a successful local test does not submit the project.

The user removed the implementation timeline. Any organizer closing date/time remains an external rule to verify on its dashboard, not an assumed extension.

## Demonstration outline

1. Ask a bounded question and show parallel research activity.
2. Open a saved passage and a rejected claim with its reason.
3. Show missing facets triggering another research round.
4. Revise a waiting report, then approve it through the same durable graph.
5. Download the report, presentation, charts, and audit JSON; inspect their citations.
6. Show explicit memory controls, scoped cache reuse, and refresh behavior.
7. Show the Docker health check, monitoring dashboard, and reproducible evaluation evidence.

Label a fixture demonstration as synthetic if live access is unavailable. Do not replace measured verifier accuracy with acceptance rate or imply an unrun organizer check passed.
