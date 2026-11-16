# Choose a loop by its useful outcome

The first five rows use the current Kit CLI. The remaining rows require a repository-specific adapter.
Do not represent a reference pattern as deployed or available from a generator.

| Need | Route | Required evidence and adoption test |
| --- | --- | --- |
| Instructions/specs contradict behavior | `scan --profile docs` | Both sides of a contradiction; maintainer verifies a concrete mistaken action is prevented |
| Small core logic lacks contract/boundary checks | `scan --profile core` | Function, callers, tests; a follow-up test/repro validates the gap. This scan does not execute tests |
| Lean proofs have reusable improvements | `scan --profile lean` | Exact declaration/assumptions and caller impact; unchanged statements verified by build/audit after implementation |
| Another bounded textual source needs review | `scan --profile general` | Repo-specific prompt and human acceptance criterion; avoid vague whole-repo improvement mandates |
| PRs stall because status/next action is unclear | `triage` | Current head, complete required CI/review facts; deterministic labels, one report, no-op on unchanged facts |
| Research direction needs periodic synthesis | Research-planner adapter | Roadmap, open tasks, known results, hypotheses and blockers; owner selects proposals before any task execution |
| New literature changes a technical decision | Literature-watch adapter | Search provenance, primary-source links, novelty relative to earlier findings, concrete effect on the repo; no duplicate summaries |
| Repeated CI failures need diagnosis | CI-diagnosis adapter | Exact failing revision/job/log, prior success, minimal reproduction; distinguish a cause hypothesis from a reproduced cause |
| Small approved changes can be drafted | Bounded-writer adapter | Approved task fingerprint, trusted executor, allowed diff scope, decisive checks, draft PR cap, current-head review before merge |
| Reader/user feedback should lead to shipped fixes | Feedback-lifecycle adapter | Source identity + fingerprint, ownership, changed/deleted source handling, draft PR link, confirmation of published fix |
| Spec/proof/model must match production | Correspondence-audit adapter | Independently fixed contract and checker, explicit translation boundary, intentionally wrong controls; proof is not specification adequacy |
| Operational state needs periodic assessment | Operations adapter | Deterministic collector/oracle first, bounded analysis, explicit remediation authority; scheduler deployment tested separately |

## Design an adapter only when selected

Write down its input snapshot, output schema, validator, deterministic publisher, and state identity.
For a proposal-only loop, accepted proposals become a human-owned backlog; producing a proposal does
not start implementation. For a writer, require an approved task revision and record which base/head
was edited and tested. Keep the AI analysis step separate from the component holding write authority.

Use content identities for meaningful changes. Avoid retries triggered only by unrelated branch SHA
changes. Bound candidates, open work, turns/time, and repeated failure. Define behavior for no changes,
no candidates, duplicate output, stale input, malformed output, truncated collection, and partial publish.
Test these cases with fixtures before the authorized live pilot. Reuse existing Kit contracts only when
their semantics fit; e.g. a source-scan candidate is not a research-task approval or an execution lease.

The current scan kind enum is `duplicate`, `assumptions`, `dependency`, `tactics`, `documentation`.
Core-profile test gaps use `documentation` and explain the gap explicitly. A richer general-code
schema needs a versioned runtime change. Do not invent fields that current validators cannot accept.

## Prompt quality

Ask for a verifiable change with a specific beneficiary. Give the current code, policy and prior work
needed to avoid rediscovery. Prefer one connected finding with supporting evidence over several names
for the same finding. Permit zero useful findings. State what evidence would refute the proposal and
what next check would justify adoption. Do not label an unexecuted test, a model's confidence, or a
successful JSON validation as proof of correctness.
