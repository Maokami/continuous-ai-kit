# Adoption evidence — 2026-11-10

## Revisions and release

[Kuratowski v1.0.0](https://github.com/Maokami/kuratowski-lean/releases/tag/v1.0.0) was published at
`138e567dbacbed099436c88e7f516bb748f28fe7`. The proof release is separate from this automation alpha.

Kit [PR #1](https://github.com/Maokami/continuous-ai-kit/pull/1) passed 88 tests and independent review.
Its squash commit `d17b8cf395a9bc6fc48799850f4d80c72386e767` has the reviewed head's tree.
The initial consumers pin this exact package revision. Kuratowski migration
[PR #40](https://github.com/Maokami/kuratowski-lean/pull/40) merged at
`e0cd8279005656d966398615bfd707643036edc8`, also with the reviewed head's tree.

## Initial live source scans

All three runs below used the same consumer and package revisions above, requested
`claude-sonnet-5-5`, and reported zero permission denials. Model time excludes Actions setup.
Estimated costs are reported API-equivalent estimates, **not subscription charges or invoices**.

| Run | Result | Model turns / time | Reported estimate |
| --- | --- | --- | --- |
| [Lean first](https://github.com/Maokami/kuratowski-lean/actions/runs/42820181521) | Rejected: evidence paths absent from coverage | 12 / 17.957 s | $0.186604 |
| [Lean explicit retry](https://github.com/Maokami/kuratowski-lean/actions/runs/42820509839) | Rejected: policy file listed as source coverage | 10 / 17.726 s | $0.158983 |
| [Text fixture](https://github.com/Maokami/kuratowski-lean/actions/runs/42820185981) | One validated candidate, published | 5 / 8.329 s | $0.018955 |

Independent checks recomputed each source/policy/package digest, compared plan and output SHAs,
and checked quotations against pinned source. Required files were batch 0: four of 50 Lean files
in 13 batches, or the single documentation fixture. The logs contained no matches for the checked
common credential patterns; this does not prove the absence of every transformed secret.

The Lean model first cited `PathsBuild.lean` and `Lift.lean` without listing them in `reviewed_files`.
The retry listed `AGENTS.md`, which is policy input rather than a configured source. Both were
rejected before publication, and the ledger recorded two failed attempts for that digest. Replays
with only these lists corrected passed; these diagnostic copies were never published or accepted
as original model output. This isolates an ambiguous prompt contract, not a tool-permission failure.

Kuratowski [PR #43](https://github.com/Maokami/kuratowski-lean/pull/43) made the source-only coverage
and evidence membership rules explicit, including exact line-prefix quotations. Kit
[PR #3](https://github.com/Maokami/continuous-ai-kit/pull/3) applies the same instructions to newly
installed lanes and fixes a separate lexical check that rejected qualified Lean declaration names.
The validators still require exact source, coverage, and quotations. A changed policy/package
creates a new digest; a new run is not an extra retry of the old input.

The text fixture intentionally contradicted itself in lines 3 and 4. Its one candidate quoted both
lines correctly. Report [#42](https://github.com/Maokami/kuratowski-lean/issues/42), state
[#41](https://github.com/Maokami/kuratowski-lean/issues/41), and the independently computed candidate
ID agree. This tests the end-to-end transport and publishing path on one deliberately authored
case, not general review quality or search completeness.

## Corrected Lean run

After the prompt fix and package update, [run 42823811257](https://github.com/Maokami/kuratowski-lean/actions/runs/42823811257)
completed prepare, analysis, validation, and publication successfully. It used consumer
`b05f96eb14bb00617bf8b6be61faf3e7f93fcdd7` and package
`55a5329192df029d909bdfbe8d936088dd16e2a0`. Independent local validation recomputed digest
`94da824ab6cacbe8efc192dab7a5baaa00fec3487fbb3b71f4f5fc5597efbedf` and matched both plan copies,
the review SHA, and the four required source paths. The ledger records attempt 1 as successful for
this new digest; the two previously published candidate IDs remain in `seen`.

The model reported 10 turns, 20.528 seconds, and an estimated $0.170296. It returned zero candidates,
so no report issue was created. The known credential-pattern scan again found zero matches.
This verifies the corrected operational path, including a valid empty result. It does not measure
candidate precision, prove reading beyond self-report, or establish that no improvements exist.

Independent source review found that the summary swapped the uses of `hne` and `hn`: `ι_mem`
uses `hn`, while `uvDart` uses `hne`. Summary prose is not fact-checked by the validator. The earlier
run's plausible `ofBipartite` dependency candidate was also not rediscovered; this run did not
follow related files. Batch 0 will not be scanned again for this successful digest. One pair of
runs cannot measure recall, but both observations rule out treating success as an exhaustive audit.

## SW adapter: live reads and stale refusal

The package collector ran locally against the public SW repository using the default version 1
configuration. It read [PR #13](https://github.com/Maokami/smarandache-wellin-lean/pull/13) at head
`0a1ab84` and found `facts_changed=false`, `label_drift=false`: the existing bot report
[#9](https://github.com/Maokami/smarandache-wellin-lean/issues/9) and labels matched the extracted code.
The facts digest was `2afafe9a4ce220d3718fa60fc713e75368d953cdb8db667ad9ec488fb98bc630`;
the configuration digest was `6e33f5fda6fad15ad01a284b52ee67815801cbf54e90bf794804d870d78e29dc`.

After the PR moved to `70cb064`, `publish --dry-run` with the old snapshot returned exit 3,
a stale warning, and no mutation lines. These commands made no repository writes. This verifies
real collection and legacy compatibility, **not a deployed reusable-workflow or AI execution**.
The migration remains a draft PR with the consumer's human approval and merge requirement.

## gh-aw comparison boundary

The [frozen comparison pilot](gh-aw-comparison.md#frozen-kuratowski-pilot) preserves the first Lean
run's plan, prompt, schema, and source revision, including its failed baseline. Its lock file
compiles with gh-aw v1.0.1, and its host materializer reproduces the four input files byte for byte.
No inference run has occurred. Runtime isolation, output quality, and actual API billing remain
unmeasured. Compilation is not evidence of those properties.

Self-reported coverage and valid quotations do not prove that an improvement is correct. Human
review decides whether to adopt a candidate; this kit does not generate or merge code changes.

## First private consumer — 2026-11-14

`Maokami/poorfolio` (private) called `pr-triage.yml` at `765cb2ed08ad00dd0b2336b08357fa12c229a947`.
[Run 43555256611](https://github.com/Maokami/poorfolio/actions/runs/43555256611) failed in `collect`:
`gh pr list --json statusCheckRollup` returned `Resource not accessible by integration
(…statusCheckRollup…checkSuite.workflowRun)`. The collect and publish jobs now request
`actions: read`; a caller must grant it as well. Earlier consumers were public repositories, where
this field is readable without the permission. The fix has not yet been observed in a live run.
