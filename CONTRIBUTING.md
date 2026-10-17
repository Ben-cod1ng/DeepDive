# Working on DeepDive

Read [PLAN.md](PLAN.md), the [scope/decision addendum](docs/decisions.md), and [architecture](docs/architecture.md) first. Preserve the separate original project. Keep changes focused, state assumptions, and verify observable behavior, following the Karpathy guidelines requested for this project.

## Development

```powershell
uv sync --frozen --extra dev
uv run python -m deepdive.ops init
uv run ruff check deepdive tests streamlit_app.py
uv run mypy
uv run python -m compileall -q deepdive streamlit_app.py
uv run pytest -q
uv run pip-audit
```

Run from this directory. `mypy` currently checks configuration and public model contracts. Tests use temporary data directories and explicit fixtures; they do not need Mesh credentials. Keep live provider tests separate and document their credit/network requirements.

Add behavioral regression coverage for meaningful changes to evidence validation, run transitions, authorization boundaries, caching, budgets, or recovery. Documentation and small visual edits do not need tests that merely restate their implementation. Use the UI AppTest for review flow and Docker smoke for actual HTTP/deployment checks.

When changing dependencies, update `pyproject.toml` and `uv.lock`, rerun audit and affected compatibility tests, then rebuild the container. When changing prompts or verification policy, run the frozen live evaluation before a release and review individual failures. Never modify reference labels to match a model's mistakes.

## Pull requests and release review

Describe the user-visible problem, resulting behavior, validation evidence, and any migration/rollback requirement. Record architecture changes in the decision guide. Keep README commands consistent with actual modules and workflows. Main-branch protections require the Quality workflow's `Quality checks` and `Docker smoke` jobs.

Do not commit `.env`, `.secrets`, local research databases, private audit exports, caches, or real provider responses containing private material. The Docker context is an explicit allowlist. The organizer workflow is retained as provided; inspect new upstream revisions before replacing it.

No release should claim independent accuracy from synthetic workflow tests. Use the [evaluation guide](docs/evaluation.md) and [operations runbook](docs/operations.md) for measured release gates and rollback.
