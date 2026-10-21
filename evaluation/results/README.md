# Evaluation evidence index

Historical records keep their original dates and hashes. They are not silently
upgraded to measurements of the latest code.

| Record | What it establishes | Limit |
|---|---|---|
| [Competition validation/deployment status](competition-validation.json) | Main CI, local recorded-example browser checks, old hosted demo availability | New deployment and backup blocked by Railway outage; independent review and organizer secrets pending |
| [Initial competition web pilot](competition-benchmark.json) | Actual web retrieval, seven rejected quotations, incomplete answer | Failed development-quality result retained; one of ten cases run |
| [Quotation-fix retest](competition-benchmark-v2.json) | Seven of eight candidates retained; four planner facets covered; exact source spans restored | Development retest with fresh retrieval, not a controlled ablation; independent review pending |
| [Competition chunk audit](competition-chunk-audit.json) | 50 chunks; labeled quotes fit; exact source offsets | Offline structural check, zero AI calls |
| [Competition passage retrieval](competition-rag.json) | Seven queries, no rerank fallbacks; passage Recall@3 0.8611 fused / 0.9444 reranked across six answerable queries | Small authored labels; answer-absent ratios undefined; independent review pending |
| [Held-out mixed-source research](competition-benchmark-v3.json) | Real document/web retrieval, two withheld candidates, cache reuse | Incomplete coverage and a potential semantic false positive; see [inspection notes](competition-review-notes.md) |
| [Hosted acceptance, October 20](railway-hosted.json) | Login, document research/review, exports and restart persistence | Earlier application revision; synthetic source material |
| [Document acceptance, October 19](live-document-acceptance.json) | Live document research, revision and exports | Three reranker fallbacks and repeated findings were observed |
| [Verifier, October 19](live-verifier.json) | 32-case live classification against authored labels | Independent reference-label review pending; old code manifest |
| [Retrieval, October 19](live-rag.json) | Four-stage document-level retrieval comparison | Ten small synthetic documents; dense also scored perfectly |

The competition benchmarks and passage-level comparison are recorded
separately. Their manifests identify the exact candidate and
dataset. Results from unexecuted cases and independent human review remain
pending. See [evaluation methods](../../docs/evaluation.md) and the
[approved scope](../../tasks/competition-plan.md).
