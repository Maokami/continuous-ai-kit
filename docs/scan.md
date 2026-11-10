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
Each digest is scanned once per batch. Automatic selection chooses the least recently visited batch.
A failed batch has one explicit retry: dispatch its numeric `batch` with `retry_failed: true`.
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
