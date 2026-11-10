# Migration without resetting history

## Kuratowski

Replace the existing lean-improvement workflow with one reusable caller, retaining its
`lean-improvement` concurrency group, weekly schedule, manual batch/retry inputs, and named OAuth
secret. Configure `legacy: "kuratowski-v1"`, `adapter: "lean"`, `source_globs: ["Kuratowski/*.lean"]`,
the existing prompt, and `policy_files: ["AGENTS.md", "CLAUDE.md"]`. Explicitly ask the model to read
AGENTS.md because restricted mode does not load project settings. Keep PR review/merge automation.

Existing state/report/ID markers and candidate IDs are preserved. The new package digest causes
a fresh sweep of all batches, but seen IDs and posted-report recovery suppress duplicate candidates.
The report title can change without changing deduplication. Keep old scripts for rollback initially;
only one scheduled implementation may run at once.

## Smarandache–Wellin

Replace only the PR triage caller with pr-triage.yml and the default TriageConfig. Preserve the
`claude-pr-triage` concurrency group and existing two-hour schedule. Snapshot config_id prevents
publishing with different configuration; old snapshots work only with default configuration.
Labels, report markers, Korean output, and facts digest remain compatible.

Do not move the write-capable hunter, its GitHub App token, or human approval/merge policy.
The migration PR follows the repository's human review requirement. A reusable triage workflow
never grants an agent permission to merge its own PR.

## A non-Lean repository

Use the installer with adapter text and docs/*.md or a small source glob. Start manually, inspect
the first artifact and report, and measure whether candidates save review time. No language-specific
core change is required. See examples/non-lean for a small source fixture.

SW migration uses an owner-created ordinary branch (not claude/ or a bot branch), since its scope
guard correctly rejects workflow edits from autonomous research PRs. Preserve human approval.
The old shell test extracted an inline publish step; replace it with caller-contract tests. The
actual stale-exit regression moves to the kit. Keep the old script and compatibility tests for
rollback, and restore the original caller if the first run fails.
