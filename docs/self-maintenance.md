# Kit self-maintenance pilot

The Kit consumes its own public reusable source-scan workflow through the same `onboard.py` path
offered to other repositories. The purpose is to find and fix adoption/operation contract drift,
then record whether the result was useful. A successful workflow is operational evidence; a
reviewed, tested improvement is a separate outcome.

## Lane contract

| Choice | Value |
| --- | --- |
| Owner | Repository maintainer (`Maokami`) |
| Lane | `kit-maintenance`, generated with the `core` profile and a repo-specific prompt |
| Inputs | README, CLI/installer, runtime/schema, selected current docs, prompts/skill, regression tests, two reusable workflows; exact paths in the lane JSON |
| Policy | `AGENTS.md`; distinct from source coverage |
| Initial scope | 28 tracked files, 198,145 bytes, 7 batches at onboarding commit `04c8c2e`; counts change with source |
| Runtime pin | `3b92523aa657b35f64130c2eed4a5446a1b52423`; deliberately independent of the source revision being inspected |
| Model bounds | `claude-sonnet-5-5`, 32 turns, analysis step 10 minutes, 4 required source files per dispatch, at most 3 candidates |
| Writes | One state issue and deduplicated candidate reports; no source edits, PRs, approvals, merges, or branch deletion |
| Validation | Exact source SHA, package/policy/source digest, declared `reviewed_files` coverage (not proof of reading), literal anchors/quotes, schema, fresh remote head before publication |
| Cadence | Manual pilot, then Monday/Thursday 00:37 UTC (09:37 KST) once `CAI_SELF_MAINTENANCE_ENABLED=true` |
| Retry | Diagnose first; one explicit retry for a failed batch/digest. No retry of successful batches |
| Stop switch | Set `CAI_SELF_MAINTENANCE_ENABLED=false`; this stops future scheduled analysis, not an already running job or manual dispatch |

The source selection excludes this evaluation record, the self-caller, and the lane's own config and
prompt. Recording observations here therefore does not, by itself, invalidate the scan's source
digest. Editing the actual lane policy/prompt still changes its policy digest. The analysis tool's
read scope is broader than the selected source list; the list is not a filesystem sandbox.

The scheduled caller is present but its job skips until the repository variable is explicitly enabled.
Two scheduled dispatches per week advance two batches, not two exhaustive audits. Empty candidates
are valid. Keep cadence unchanged during the initial four evaluated runs; measure usage before
expanding scope, model, or frequency. No monetary cap or automatic quality-based stop is implemented.

## Evaluation and improvement loop

1. Confirm `CLAUDE_CODE_OAUTH_TOKEN` is configured before dispatching. Once `prepare` reserves a
   batch, a failed analysis consumes an attempt; provisioning failures should not spend this budget.
   Run batch `2` manually on `main`: it includes triage/CLI and onboarding/scan documentation in the
   initial sorted selection. Check the produced plan rather than relying on that ordering forever.
2. Verify artifacts and the report against the exact checked-out source. Record operational success
   separately from whether the maintainer accepts any finding.
3. Only after successful analysis, validation, publication and a `success` state in the ledger,
   dispatch the same batch with unchanged source/policy/runtime and `retry_failed=false`.
   Confirm analysis/publication skip and the state/report remain unchanged. `auto` can advance to a
   different batch and is not a no-op test. A skip after a failed attempt is retry suppression,
   not evidence of a successful pilot or successful-result deduplication.
4. For each accepted finding, create a scoped fix through the normal PR process. Record the original
   report/candidate ID, reviewed source, reproduction, fix, tests and merged revision. A proposal is
   not implementation authorization for the scheduled agent.
5. Enable the schedule after the valid pilot, no-op check, and maintainer evaluation. Review the first
   four evaluated runs. Pause or narrow the lane after two consecutive unusable reports or operational
   failures until the cause is understood. The maintainer performs this action.

The outcome target is at least one accepted and independently verified maintenance improvement
within four evaluated runs. It is a trial target, not an observed result or a guaranteed quota.
Log valid empty results, rejected/duplicate findings, reviewer effort, and operational failures too.
Do not increase issue count to satisfy the target. Do not infer saved human time from model runtime.

## Observations

| Run and source/runtime revision | Validation and publication | Human disposition | Fix/reproduction evidence | Runtime/usage and reviewer effort |
| --- | --- | --- | --- | --- |
| Local scaffolding | Shared CLI generated and preflighted the lane | Pilot prepared | Local tests and workflow lint; no inference claimed | Not a model run |
| [Kit CI 43808743961](https://github.com/Maokami/continuous-ai-kit/actions/runs/43808743961), source `b8d5afda6e9b0fafd925b4cd546d86f1f2bece7a` | 108 tests and workflow lint passed on the PR head | Infrastructure checks only | Includes five generated caller variants | Not a model run |
| Local pinned-runtime preparation, same source; runtime `3b92523aa657b35f64130c2eed4a5446a1b52423` | Config, policy, source and batch `2` accepted by the exact pinned package | Input compatibility checked | Fake GitHub API; zero network calls or remote writes. Required files: `cai/triage.py`, `cli.py`, `docs/onboarding.md`, `docs/scan.md` | Not a model run |

A read-only `agy` advisory review of the self-maintenance patch and pinned workflow/scanner found
no blocking issue. Its authentication-before-dispatch and successful-pilot-before-no-op clarifications
are incorporated above. This was a supplied-source review, not an independent execution or an
OAuth test. The Claude advisory route was unavailable due to the subscription's five-hour limit.

Authentication, live pilot, no-op result, and schedule activation are pending. This status will be
updated only from observed runs. The repository originally had no `CLAUDE_CODE_OAUTH_TOKEN` secret.
Follow the official [Claude Code GitHub Actions setup](https://code.claude.com/docs/en/github-actions)
and keep the token value in Actions secrets, never in this record.

Source review and deterministic tests cannot establish that the model finds useful bugs. Conversely,
one useful report does not establish recall, absence of defects, or safety of unrelated autonomous writes.
