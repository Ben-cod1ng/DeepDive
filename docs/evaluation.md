# Evaluation and model operations

DeepDive separates three questions: **does the application work, does the verifier judge support correctly, and does the final report answer the research question?** Passing the first does not establish the other two.

## Datasets and honest labels

`evaluation/claims.v1.json` contains 32 synthetic claim/passage cases, including numerical, attribution, negation, causal, quotation, and missing-source negatives. Its current dataset version is **1.1**. Labels were authored with the implementation and **have not been independently reviewed**. The filename stays stable; the embedded version and SHA-256 identify the actual content.

`evaluation/questions.v1.json` contains ten research questions spanning technical, scientific, market, comparative, time-sensitive, contradictory, and insufficient-evidence tasks. These are an acceptance set, not evidence that the application has already answered them successfully.

Before a release, an independent reviewer must inspect every claim, passage, quotation, and reference label. Record actual reviewer identity/date in the dataset's `independent_review` object with `status: approved`, update its label status/version, and retain review notes in version control. Do not fill these fields merely to pass automation. Freeze reviewed evaluation cases before tuning prompts against other development examples.

## Offline checks

```powershell
uv run pytest -q --junitxml=evaluation/results/junit.xml
uv run python -m deepdive.evaluation fixtures --output evaluation/results/fixture-gates.json
```

Tests use `tests/fakes.py` for explicit synthetic model/retrieval responses. Production startup has no fake-provider fallback. Tests exercise rejection, repair, publication gates, persisted reviews, API boundaries, memory/cache isolation, atomic budgets, backup/restore, and UI approval through the API.

The fixture command checks deterministic citation and number gates only. A case passing that gate still requires semantic verification. Its output deliberately leaves `semantic_precision` and `semantic_recall` null. Do not call the fraction passing a deterministic check “accuracy.”

## Public retrieval check

```powershell
uv run python -m deepdive.evaluation retrieval --output evaluation/results/public-retrieval.json
```

This uses the production network transport, tries a public SQLite documentation page and DuckDuckGo discovery, and records availability, hashes, and timing without AI calls or saved user history. Add repeated `--source URL` arguments to test other public pages. Results are point-in-time observations; a provider can challenge or block later requests. User-supplied source URLs remain the fallback when discovery is unavailable.

## Live verifier evaluation

Configure `MESH_API_KEY` locally or in the protected `live-validation` GitHub environment. This command uses credits:

```powershell
uv run python -m deepdive.evaluation verifier --output evaluation/results/live-verifier.json
uv run python -m deepdive.evaluation gate evaluation/results/live-verifier.json
```

Evaluation uses a separate database under `data/evaluations/RUN_ID`, so synthetic cases do not enter user memory/history. Each result records dataset version/hash, model, fixed Mesh gateway, policy version, release, source-code and lockfile hashes, date, decisions, model requests, and tokens.

Support precision is `TP / (TP + FP)`; recall is `TP / (TP + FN)`. Unsupported, contradicted, and insufficient decisions all count as predicted negatives for binary support scoring. Undefined ratios remain null. Report counts and individual failures alongside percentages; 32 examples provide limited statistical assurance.

The release gate requires:

- A live Mesh result, with at least 30 labeled examples.
- Independently reviewed reference labels.
- Matching dataset, code/dependency manifest, gateway, and policy.
- Support precision at least **0.90** and recall at least **0.50**.

The recall floor prevents a reject-everything verifier from appearing safe and useful. It is an operational gate added with the end-to-end/MLOps scope, not a measured result. The workflow will fail until the real prerequisites and measurements satisfy it. Never weaken this gate to conceal model failures.

## End-to-end benchmark and final-claim review

With the API running and Mesh configured:

```powershell
uv run python -m deepdive.evaluation benchmark --output evaluation/results/live-benchmark.json
```

The benchmark creates real research sessions, explicitly uses automatic approval, and checkpoints its result file after each question. It may make up to ten runs' worth of bounded provider requests. It records actual per-run coverage, citation validity, acceptance/rejection counts, repair rounds, usage, and duration. Keep detailed run exports private if their source material or questions are sensitive.

For manual acceptance, also run one question with manual review, revise it, restart at a waiting review, resume/approve, and inspect every export. Demonstrate an actual rejected claim and a repair that obtains new supporting evidence. Fixture demonstrations are useful for regression testing but do not substitute for this live exercise.

An independent reviewer must inspect **every published factual claim** against its stored passages and assess the required facets against the original question. Record claim ID, supported/unsupported/ambiguous label, reviewer, rationale, and any omitted qualification. Calculate final support accuracy from those independent labels; `independent_support_accuracy` remains null until such review exists. Also report unanswered facets and contradictions. A tiny report with valid citations may still be inadequate.

## Rubrics, calibration, and drift

The UI reports verifier acceptance and facet coverage separately. Citation validity means the referenced passage exists; it does not mean the citation entails the claim. Coverage and traceability rubric values are explicit linear 1–5 scores, not probability estimates.

```powershell
uv run python -m deepdive.evaluation export-labels --output evaluation/results/human-labels.csv
# A reviewer fills human_score, reviewer, and notes in the CSV.
uv run python -m deepdive.evaluation calibrate evaluation/results/human-labels.csv --output evaluation/results/calibration.json
```

Calibration reports labeled sample count, mean absolute error, and Pearson correlation when defined. It does not fit a confidence model. Drift compares the older and newer halves of at least four recorded runs and reports a descriptive mean difference. Changes in question difficulty, retrieval availability, or model routing can confound it; it is not a significance test or an automatic retraining trigger.

## Release evidence

Keep reviewable aggregate evaluation results with the tested commit. GitHub uploads detailed CI/live evidence with finite retention. The release job evaluates the candidate again before pushing its image and saves the resulting digest. Changing prompts, code, policy, model selection, or dependencies requires a new evaluation and review of the observed regressions. A hosted model can change behind a model identifier; record dates and repeat the frozen set when behavior changes.

## Document retrieval evaluation

Run `uv run python -m deepdive.rag_evaluation --output evaluation/results/live-rag.json` to compare BM25, dense, fused, and reranked document retrieval on the versioned ten-document/seven-question dataset. It records Recall@3, MRR@3, nDCG@3, latency, usage, model/profile, and code/data hashes in an isolated evaluation directory. This is a retrieval ablation; it does not measure final-answer accuracy. See [the RAG guide](rag.md#evaluate-retrieval).

The live-evaluation and release workflows retain this retrieval evidence alongside verifier results. Retrieval labels are authored synthetic cases pending independent review; successful command execution is not a retrieval quality threshold. The existing independently reviewed verifier precision/recall gate still controls image publication. Establish reviewed retrieval thresholds on a representative held-out corpus before treating the RAG experiment as a production promotion gate.

Live Mesh results and independently measured final-claim support remain pending. Offline application and remote quality checks have passed previously; see [the checklist](../tasks/todo.md) for current validation.
