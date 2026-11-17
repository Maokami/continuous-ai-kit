# Source scan configuration

Configuration version 1 rejects unknown fields. Required: `version`, `lane`, `source_globs`,
`prompt_file`. Optional: `default_branch` (main), `adapter` (text or lean), `model`
(claude-sonnet-5-5), `batch_size` (1–8, default 4), `max_turns` (1–64, default 32),
`policy_files` (list), `legacy` (only kuratowski-v1 for migration).

Paths must be normalized relative repository paths. Inputs must be tracked ordinary files matching
HEAD exactly; symlinks, dirty inputs, files over 250 KB, over 1,000 sources, and over 2 MB of sources
are rejected. Globs use Python fnmatch over tracked paths. Include policies in `policy_files` and
explicitly instruct the model to read applicable policy files in the prompt.

The digest includes the executing package SHA, schema, config, prompt, policies, and source bytes.
Each digest is scanned once per batch. Automatic selection prioritizes quota-deferred batches, then the least recently visited batch.
An ordinary failed batch has one explicit retry: dispatch its numeric `batch` with `retry_failed: true`.
Do not use GitHub's partial job rerun for retries. Artifacts include run_attempt; this preserves
attempt evidence and intentionally prevents a partial rerun from reading an older attempt.
Re-run all jobs is a new reservation and may select a different batch; use dispatch for intent.

The state issue is a serialized read-modify-write ledger. Only github-actions[bot] issues with the
expected markers are recognized. Report IDs repair an interrupted state write after a report was
posted. Writes are not automatically retried because a server error can occur after applying them.
The ledger supports at most 300 IDs and reading at most 1,000 issues. Exceeding limits fails closed.
To retire an ID, archive the report marker (prefix `archived-`) and remove the same ID from state
under maintenance with the schedule disabled. Deleting only state does not clear recovered IDs.

If main changes during analysis, publication fails and the attempt is consumed. Failure recording
also runs on ordinary cancellation, but forced cancellation may leave pending state. These are
known v0 limitations, not claims of exactly-once execution.

The text adapter requires a literal source anchor and manual risk. The Lean adapter checks a
named declaration's presence; it does not elaborate the proposal or verify theorem preservation.
Both require exact source quotations and full required-file coverage in structured output.

## Diagnosing analysis failures

The analysis job checks for an absent OAuth token or characters outside visible ASCII before invoking
Claude. It rejects these values rather than silently trimming them and prints only a fixed category.
This checks input shape, not authentication, model access, or available subscription usage. It occurs
after reservation, so a failure still consumes an attempt.

If Claude fails, `cai/diagnostics.py` reads the pinned Action's temporary execution record and emits
a fixed category (and an optional bounded reset timestamp): `authentication_error`, `usage_limit`, `billing_error`, `model_unavailable`, `turn_limit`,
`structured_output_missing`, `upstream_action_failure`, `unknown_analysis_error`, or
`execution_record_unavailable`. These are troubleshooting hints based on the SDK result, not trusted
authorization or semantic verdicts. Only `usage_limit` enters the bounded [quota recovery loop](quota-recovery.md); that workflow
completes as deferred without validating/publishing an AI result. Other failures stay failures.
Result text can itself be model-influenced; a category is not proof about the account's status.

Raw SDK messages, tool output, tokens, and error text are neither echoed nor uploaded by this helper.
Malformed, missing, symlinked or oversized execution records produce `execution_record_unavailable`.
The existing Action's full-output/report switches remain disabled. For token input errors, correct
the Actions secret. Usage limits now persist a wait and resume automatically on the next eligible
trigger, without spending the ordinary task retry. The diagnostic may also export a bounded numeric
`reset_at` from an SDK rate-limit event; prose reset times are ignored.
Do not change input digests merely to obtain additional attempts.
