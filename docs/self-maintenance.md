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
| Runtime pin | `d5e38f65feb7dc61a29129d1ae5ee3c990308b55` (quota recovery runtime); deliberately independent of the source revision being inspected |
| Model bounds | `claude-sonnet-5-5`, 32 turns, analysis step 10 minutes, 4 required source files per dispatch, at most 3 candidates |
| Writes | One state issue and deduplicated candidate reports; no source edits, PRs, approvals, merges, or branch deletion |
| Validation | Exact source SHA, package/policy/source digest, declared `reviewed_files` coverage (not proof of reading), literal anchors/quotes, schema, fresh remote head before publication |
| Cadence | Manual pilot, then Monday/Thursday 00:37 UTC (09:37 KST) once `CAI_SELF_MAINTENANCE_ENABLED=true` |
| Retry | Automatic bounded quota deferral; one explicit retry for ordinary failures. No retry of successful batches |
| Stop switch | `CAI_SELF_MAINTENANCE_ENABLED=false` stops new scheduled work; `CAI_QUOTA_RECOVERY_DISABLED=true` stops all scheduled work including recovery. Neither cancels an active run or manual dispatch |

The source selection excludes this evaluation record, the self-caller, and the lane's own config and
prompt. Recording observations here therefore does not, by itself, invalidate the scan's source
digest. Editing the actual lane policy/prompt still changes its policy digest. The analysis tool's
read scope is broader than the selected source list; the list is not a filesystem sandbox.

The regular new-work schedule skips until `CAI_SELF_MAINTENANCE_ENABLED` is enabled.
Hourly recovery checks only resume an existing quota wait, even while new-work scheduling is off.
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
| [First live pilot 43809707993](https://github.com/Maokami/continuous-ai-kit/actions/runs/43809707993), source `d7c3d07ee96108dd44d32aad0125132c4b1cdd97`; runtime `3b92523aa657b35f64130c2eed4a5446a1b52423`; batch `2` | Preparation passed; Claude returned `is_error=true` without structured output. Validation/publication skipped; failure recording passed | No candidates to evaluate | [State #7](https://github.com/Maokami/continuous-ai-kit/issues/7) recorded attempt 1 as error at the time; no retry was dispatched for that digest | SDK reported 1 turn, 224 ms, $0 API-equivalent estimate and empty model usage; no successful analysis claimed |
| [Instrumented pilot 43811425527](https://github.com/Maokami/continuous-ai-kit/actions/runs/43811425527), source `f7464936574d23e26593e5fd7189b07c94a09d57`; runtime `b0a29dce5a3a65a158fe3c2ec2c74fc661e16241`; batch `2` | Preparation and token shape check passed; analysis failed; diagnostics emitted `usage_limit`; validation/publication skipped; failure recording passed | No candidates; schedule kept disabled | State #7 records attempt 1 for the new runtime/source digest, with no published candidate IDs | SDK reported 1 turn, 322 ms, $0 API-equivalent estimate and empty model usage |

A read-only `agy` advisory review of the self-maintenance patch and pinned workflow/scanner found
no blocking issue. Its authentication-before-dispatch and successful-pilot-before-no-op clarifications
are incorporated above. This was a supplied-source review, not an independent execution or an
OAuth test. The Claude advisory route was unavailable due to the subscription's five-hour limit.

The OAuth secret was registered on 2026-11-16. The first pilot could not distinguish the provider
error. [PR #8](https://github.com/Maokami/continuous-ai-kit/pull/8) added fixed-category diagnostics
and token input checks ([115-test CI](https://github.com/Maokami/continuous-ai-kit/actions/runs/43811030474)
and independent supplied-source review); [PR #9](https://github.com/Maokami/continuous-ai-kit/pull/9)
pinned that released runtime. Its live diagnostic step worked and classified the next failure as
`usage_limit`. Token shape passed; this alone is not authentication proof. No successful analysis,
candidate publication, or successful-result no-op test has occurred yet.

`CAI_SELF_MAINTENANCE_ENABLED` is confirmed `false` after the two operational failures.
No further scan inference or automatic retry was scheduled. The diagnostic improvement came from running
the Kit on itself and inspecting its failure; it is not an AI-generated maintenance candidate and
does not satisfy the accepted-finding target.

## Historical resume procedure before quota recovery

Confirm the subscription can serve the configured model, then use the existing explicit retry:

```sh
gh workflow run continuous-ai-kit-maintenance.yml \
  --repo Maokami/continuous-ai-kit --ref main -f batch=2 -f retry_failed=true
```

At the checkpoint above, digest `2168a5cfd4cb28540037ed024d8e87b33340ec3d0e5f769053318d99024539f0`
has `attempts=1`, `status=error`. One retry remains under the [bounded retry policy](scan.md).
Check current source/policy/runtime
and state first; do not clear the ledger or change inputs to evade the limit. The instrumented run
used a new digest because diagnostics and source actually changed, not an explicit retry of the old run.
Its required files were `cai/scan.py`, `cai/triage.py`, `cli.py`, and `docs/onboarding.md`.

After a successful analysis/publication, dispatch the same batch with `retry_failed=false` and verify
that analysis/publication skip and state/report body and timestamps remain unchanged. Evaluate the
actual findings before setting `CAI_SELF_MAINTENANCE_ENABLED=true`. Keep it false until those checks pass.

The repository originally had no `CLAUDE_CODE_OAUTH_TOKEN` secret.
Follow the official [Claude Code GitHub Actions setup](https://code.claude.com/docs/en/github-actions)
and keep the token value in Actions secrets, never in this record.

Source review and deterministic tests cannot establish that the model finds useful bugs. Conversely,
one useful report does not establish recall, absence of defects, or safety of unrelated autonomous writes.

## Automatic recovery upgrade

The updated caller adds an hourly recovery-only trigger and the runtime now records quota failures as
`waiting_for_quota` with a persistent cooldown. See [policy and migration](quota-recovery.md).
The two pilot failures above remain historical evidence from the old runtime; their `error` entries
are not silently reclassified. After the reviewed runtime/caller are deployed, start a normal manual
pilot. If it hits a limit, subsequent recovery checks should resume automatically without waiting on
a runner. Record that live sequence before claiming end-to-end subscription recovery has passed.

## Startup failure correction — 2026-11-17

Scheduled recovery runs [44046565194](https://github.com/Maokami/continuous-ai-kit/actions/runs/44046565194)
and [44055057493](https://github.com/Maokami/continuous-ai-kit/actions/runs/44055057493) failed before
creating jobs. The workflow-file annotation reported `workflow was not found` for the pre-squash
feature SHA `1ba588c8600b0d4ad84ac156ba733811dbb281b4`, although the contents API could read that file.
The self-caller now pins the published squash commit `d5e38f65feb7dc61a29129d1ae5ee3c990308b55`.
CI checks that this pin belongs to published main history, with a regression fixture distinguishing
feature and squash SHAs. The prior unit tests/actionlint did not exercise GitHub workflow resolution.
Pin future self-maintenance upgrades to the published post-merge SHA in a subsequent caller update.
