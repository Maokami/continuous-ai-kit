# Trust and execution boundaries

The workflow definition and implementation are pinned together: each job checks out the consumer
at github.sha and the callee at job.workflow_repository/job.workflow_sha. Scan configuration verifies
the package HEAD against the executing workflow SHA. Python entry points use isolated mode and
only import the explicitly checked-out package. Consumer Python code is never executed by a scan.

The analysis job has contents:read, OAuth, Read/Glob/Grep, empty strict MCP configuration,
restricted mode, and explicit .git/.env denials. Deterministic jobs have only the write permissions
needed for state, reports, or labels and receive no OAuth secret. No id-token permission is requested.

`--restricted` confines file tools to the workspace and ignores file-based settings. The pinned
Action's parser treats empty --setting-sources as its default; we therefore pass `user` explicitly
and rely on restricted mode to ignore all file-based settings. Source content is untrusted. This
is not an OS/network firewall equivalent to gh-aw's sandbox. Never claim tool restrictions make
prompt injection impossible. Keep sensitive files out of the checked-out repository.

Validated output is checked again before writing, against source SHA, digest, file coverage, exact
quotations, secret values/patterns, and a fresh remote head. Mentions and HTML markers are neutralized.
The ledger assumes repository writers are trusted; bot-authored issues are not a cryptographic
attestation against a maintainer editing their contents. A compromised workflow or maintainer is
outside this boundary.

References:
- [GitHub callee identity contexts](https://github.blog/changelog/2026-09-03-github-actions-early-september-2026-updates/)
- [Reusable workflow permissions](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows)
- [Claude CLI restrictions](https://code.claude.com/docs/en/cli-reference)

Workflow lint currently ignores only actionlint v1.7.12's unknown job.workflow_sha and
job.workflow_repository properties, introduced after its context table. All other lint checks run.
Live execution, rather than that suppression, must verify those contexts on GitHub.com.
