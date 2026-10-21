# Competition evaluation inspection

These are assistant inspection notes, not independent human review or accuracy
labels. Original run results and source snapshots are preserved.

## Held-out mixed document/web case, October 21

- Candidate: `84fd94e16ad2816edc32cc46b0f6d921b407eba5`.
- Run: `run_c42ffba950794eb0a7e1`; [aggregate evidence](competition-benchmark-v3.json).
- Two of four candidates published; two of five planner facets covered. Research
  stopped because suggested queries had already been attempted. No repair ran.
- `claim_277be25aabe9556b1a6b6cf5` asserted simultaneous writers were supported by
  reader/writer concurrency evidence. The verifier withheld it as insufficient.
- `claim_37b0933399600d9a914d601e` asserted the proposal was contradicted, but its
  quoted SQLite text only says readers do not block a writer. The verifier accepted
  this because the quote did not explicitly permit multiple writers. Absence of
  permission in this excerpt does not establish the asserted contradiction. This
  is a potential false positive, even though other SQLite passages could establish
  the one-writer restriction. A correct conclusion still needs adequate citations.
- The retained network-filesystem claim quotes the official same-host restriction.
  A second candidate about network sharing failed exact-quotation validation.
- No changes were made to the pipeline after inspecting this held-out result.

Independent reviewers should assess every final claim and the frozen reference
facets. Blind worksheets for the web retest and mixed case are under the private
`data/evaluations` directory; the existing 32-case verifier worksheet is at
[`evaluation/review/claims.v1.blind.csv`](../review/claims.v1.blind.csv).

## What remains unestablished

No live run in this batch demonstrated a successful repair round. Regression
fixtures do exercise an empty first round followed by productive repair. Eight of
ten research scenarios were not executed within the remaining evaluation budget.
Independent relevance labels, final-report support, reference-facet completeness,
and the current-candidate verifier release gate remain pending. The old live
verifier result is not a measurement of this changed candidate.
