# Evaluation and model operations

DeepDive separates three questions: **does the application work, does the verifier judge support correctly, and does the final report answer the research question?** Passing the first does not establish the other two.

## Datasets and honest labels

`evaluation/claims.v1.json` contains 32 synthetic claim/passage cases, including numerical, attribution, negation, causal, quotation, and missing-source negatives. Its current dataset version is **1.1**. Labels were authored with the implementation and **have not been independently reviewed**. The filename stays stable; the embedded version and SHA-256 identify the actual content.

`evaluation/questions.v2.json` freezes ten research scenarios, including mixed document/web research and a real two-turn follow-up in one session. Two development cases and eight held-out cases are identified before evaluation. Reference facets are authored separately from the planner. The older v1 dataset remains historical evidence.

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

Run directly with Mesh configured; no API server is needed. First read the current
Mesh balance and model pricing through the documented [usage API](https://developers.meshapi.ai/docs/reference/usage-api)
and [model metadata](https://developers.meshapi.ai/docs/reference/models). Choose an
allocation from existing credit, preserving at least half the measured balance for
demos. Set the following PowerShell variables to those **reviewed amounts**; the
command deliberately has no automatic dollar allocation:

```powershell
uv run python -m deepdive.evaluation benchmark --case technical --model-calls 25 --input-tokens 12000 --output-tokens 2000 --ledger .secrets/evaluation-ledger.json --max-spend-usd $evaluationAllocation --reserve-usd $demoReserve --output evaluation/results/competition-benchmark.json
# Continue this exact experiment without rerunning completed cases:
uv run python -m deepdive.evaluation benchmark --case follow_up --resume --model-calls 25 --input-tokens 12000 --output-tokens 2000 --ledger .secrets/evaluation-ledger.json --max-spend-usd $evaluationAllocation --reserve-usd $demoReserve --output evaluation/results/competition-benchmark.json
```

The benchmark isolates its SQLite store, checkpoints each run ID, and explicitly
uses automatic approval. Completed cases are never rerun by `--resume`; failed or
limited cases are retained. Configuration/code/dataset changes require a new
output and experiment. Detailed exports stay under `data/evaluations/benchmark_*`.
Only this experiment's run IDs enter its metrics. `not_run` explicitly lists
unexecuted cases. An incomplete ten-case run is reported as partial.

The persistent ledger reserves a conservative request cost **before each chat,
embedding, transport retry, and formatting retry**. It uses UTF-8 input-size bounds,
the configured output limit, current token prices, and a 10% margin. Missing usage
or failed transport retains the reservation. Concurrent threads share an atomic
reservation; an exclusive lock prevents a second evaluation process. The current
provider balance is checked before each request. These are estimates, not provider
charges, and they do not control spending by other clients using the same key.
Reconcile the balance between batches. Keep the original allocation and ledger
across experiments; do not reset either to recover spent credit. After an abrupt
process exit, inspect the ledger and confirm the owner has stopped before removing
only its `.lock` file. Retain unresolved request reservations.

Round snapshots compare planner-assessed facet counts before and after repair.
They are a within-run observation, not an independent completeness score or a
randomized no-repair baseline. `reference_facet_completeness` and
`independent_support_accuracy` remain null until human review supplies evidence.

For manual acceptance, also run one question with manual review, revise it, restart at a waiting review, resume/approve, and inspect every export. Demonstrate an actual rejected claim and a repair that obtains new supporting evidence. Fixture demonstrations are useful for regression testing but do not substitute for this live exercise.

An independent reviewer must inspect **every published factual claim** against its stored passages and assess the required facets against the original question. Record claim ID, supported/unsupported/ambiguous label, reviewer, rationale, and any omitted qualification. Calculate final support accuracy from those independent labels; `independent_support_accuracy` remains null until such review exists. Also report unanswered facets and contradictions. A tiny report with valid citations may still be inadequate.

The [blind 32-case worksheet](../evaluation/review/claims.v1.blind.csv) omits the
authored labels. Fill `human_label` (`supported`, `unsupported`, or `ambiguous`),
`reviewer`, `reviewed_at`, and `notes`. Do not edit the evidence columns. Commands
refuse to overwrite worksheets and reject changed evidence or missing judgments:

```powershell
uv run python -m deepdive.review claims evaluation/claims.v1.json --labels evaluation/review/claims.v1.blind.csv --output evaluation/results/label-review.json
uv run python -m deepdive.review report data/evaluations/EXPERIMENT/technical-turn-1.json --output evaluation/review/technical.blind.csv
```

A validated worksheet does not itself establish reviewer independence or approve
the release. Resolve corrections, record who reviewed them, and run the unchanged
quality gate against the resulting reviewed dataset and current candidate.

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

## Recorded live measurements

The October 20 development pilot is preserved in
[competition-benchmark.json](../evaluation/results/competition-benchmark.json).
It completed one technical case but published only one of eight candidates and
covered none of the four planner facets. Seven candidates failed exact quotation
matching; inspection found line-break/spacing changes in five of those candidates.
This is an observed weakness, not a successful answer-quality result.

The follow-up fix anchors a model quotation only when its literal words,
punctuation, case, and order match one unique source span with whitespace
differences. It stores the original exact slice and records submitted quotation
and offsets in the audit. Both deterministic and semantic gates still run; already
verified claims are untouched. Offline replay recovered five exact quotations;
that replay is not a new semantic support score. Analysis also reserves candidate
capacity for later tasks instead of letting the first task consume the entire
run's claim limit. Any live retest is a new experiment, preserving the failed pilot.

The [separate development retest](../evaluation/results/competition-benchmark-v2.json)
on commit `2a3361d` retained seven of eight candidates and covered four of four
planner facets, using 12 requests and 54,708 tokens. One candidate remained
insufficient. Neither run selected a repair round, so there is no claim of
observed live repair success. Fresh retrieval and a changed candidate allocation
make this a development retest, not a matched quotation-only ablation. Independent
claim support and reference-facet completeness remain unmeasured.

The [v2 retrieval comparison](../evaluation/results/competition-rag.json) on
commit `84fd94e` measures passage-level relevance on longer public documents.
Reranked mean Recall@3 was 0.9444 versus 0.8611 for fusion across six answerable
queries; all seven queries reranked without fallback. See the
[stage comparison and limitations](rag.md#measured-passage-level-results).

The [October 21 held-out mixed-source case](../evaluation/results/competition-benchmark-v3.json)
used the unchanged `84fd94e` candidate. It retained two of four candidates,
covered only two of five planner facets, and stopped with `no_untried_queries`.
It used 20 requests, 47,336 tokens, and 18 cache hits. Assistant inspection flagged
one retained claim: it describes a contradiction, but its quotation establishes
reader/writer concurrency rather than directly excluding simultaneous writers.
That is a potential semantic-verifier false positive requiring independent review,
not a passing answer-quality result. No prompt/configuration tuning followed this
held-out result. [Inspection notes](../evaluation/results/competition-review-notes.md)
identify the claim and the review requirements.

Paid evaluation stopped after these three research runs (two technical pilots,
one mixed case) and the retrieval comparison. Eight distinct research scenarios
remain unrun. The measured Mesh balance was $0.8692058, above the original
$0.67373855 demo reserve. The persistent ledger accounted for $0.57989338 of the
$0.67373855 evaluation ceiling, including one unresolved transport reservation.
These ledger values are conservative estimates, not provider charges. The
remaining evaluation headroom cannot fund a comparable complete research run;
it has not been reallocated from demo credit.

On October 19, 2026, the [32-case live verifier evaluation](../evaluation/results/live-verifier.json) through Mesh with `openai/gpt-4o` produced 11 true positives, 21 true negatives, no false positives, and no false negatives against the authored reference labels. Precision and recall were both 1.0 on this set, using seven model requests and 8,603 reported tokens. These are measurements on a small synthetic set whose labels still await independent review, not an independently established production accuracy estimate.

The [promotion gate](../evaluation/results/live-verifier-gate.json) remains **blocked by independent reference-label review**. Its precision and recall criteria passed; the review requirement has not been bypassed.

The [live retrieval evaluation](../evaluation/results/live-rag.json) also completed all seven questions with actual embeddings and validated reranking. Final document-level Recall@3, MRR@3, and nDCG@3 were all 1.0; BM25 alone scored 0.8571 for each metric. Dense retrieval also scored 1.0, so this set does not establish that reranking improves over dense retrieval. See [the retrieval comparison](rag.md#evaluate-retrieval) for all stages and limitations.

The [deployed document acceptance run](../evaluation/results/live-document-acceptance.json) used three authored synthetic policy documents and real Mesh inference. A script exercised the HTTP API in manual-review mode: upload, two research tasks, draft review, revision, an actual API-container restart at the second review checkpoint, approval, and download of eight artifacts. The PowerPoint, PNGs, and audit JSON parsed successfully; published references resolved and quotations matched their stored passages. This was a scripted acceptance exercise, not independent human review. The session remains available in the local UI as **Live Mesh acceptance (synthetic documents)**.

That investigation used 47 model requests and 47,178 reported tokens across both versions and publication. Eight of its eleven uncached retrievals completed validated reranking; three fell back to hybrid order after two invalid-schema responses each. The fallback preserved source passages and did not falsely mark those retrievals as reranked. The final report repeated the carry-forward rule in two findings, so editorial concision remains a limitation. The separate seven-question retrieval evaluation above had no reranking fallbacks.

The ten-question research benchmark, independent reference-label review, and independently measured final-claim support remain pending. See [the checklist](../tasks/todo.md) for acceptance and deployment validation.
