# Continuous AI Kit

Small, inspectable GitHub Actions workflows for recurring repository maintenance.
Python 3.10+ standard library; Claude Code Action for bounded read-only analysis.

Continuous AI is a feedback loop: **trigger → collect current evidence → ask an agent only where
judgment helps → validate → publish a bounded result → review whether it was useful**.
A schedule alone is not enough. Useful findings, reproducible evidence, safe writes, and a stop
condition matter more than running frequently.

| Lane | Reads | AI result | Deterministic writes |
| --- | --- | --- | --- |
| Source scan | Pinned tracked source and policy | At most three cited improvement candidates | Deduplicated report issues and a state issue |
| PR triage | Current PR/CI/review facts | Priority and next action | Managed labels and one report issue |

This alpha does not edit source, open PRs, approve reviews, merge, or delete branches.
Repository-specific PR review and merge automation remains in each consumer.

## Install a source scan

Clone this **public** repository and choose a reviewed full commit SHA. From a trusted checkout:

```sh
python3 -I /path/to/continuous-ai-kit/install.py /path/to/consumer \
  --ref FULL_40_CHARACTER_COMMIT_SHA --lane docs \
  --adapter text --source-glob 'docs/*.md'
```

Review the generated workflow, JSON configuration, and prompt, then submit them as a PR.
For Lean use `--adapter lean --source-glob 'Kuratowski/*.lean'`.
The installer never commits, pushes, sets secrets, or starts a run.

Set `CLAUDE_CODE_OAUTH_TOKEN` in the consumer's Actions secrets using the official Claude setup-token
flow. Keep secret values out of issues, config files, and chat. The caller passes this secret by name;
only the analysis job receives it. Start manually. Add a schedule only after checking usefulness.

**Required:** one caller `concurrency` group per lane with `cancel-in-progress: false`, full SHA pins,
and matching default branches in the caller and config. Do not create a second scheduled caller for
the same ledger. A public callee is required because the consumer token cannot read a different
private repository. GitHub.com is supported; GHES is not currently supported.

See [scan configuration and operation](docs/scan.md), [PR triage](docs/triage.md),
[security boundaries](docs/security.md), [migration](docs/migration.md), and
[gh-aw comparison](docs/gh-aw-comparison.md).

## Verify changes

```sh
python3 -I -m unittest discover -s tests -v
```

Tests cover source pinning, exact quotations, malformed output, configuration boundaries, recovery,
and compatibility with the original repositories. See the [live-run evidence](docs/adoption-evidence.md)
for observed successes, validation failures, and remaining deployment limits.
AI-reported file coverage does not prove the files were read, and passing validation does not prove
an improvement is correct. Empty reports are valid.

## Provenance

Extracted from [Kuratowski Lean](https://github.com/Maokami/kuratowski-lean) and
[Smarandache–Wellin Lean](https://github.com/Maokami/smarandache-wellin-lean).
See [provenance](docs/provenance.md) for source revisions and retained limitations.
Apache-2.0; human review remains responsible for adopting proposals.
