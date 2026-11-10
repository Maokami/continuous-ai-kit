# gh-aw comparison: use the same task, measure the operating cost

Continuous AI is a recurring evidence-and-feedback process, not a particular vendor or a promise
that an agent will keep inventing useful work. A mature proof repository may benefit more from
release checks and targeted dependency upgrades than an endless refactoring scan. Stop scanning
unchanged inputs; track whether reported candidates were accepted and why others were rejected.

| Concern | This Claude Action kit | GitHub Agentic Workflows |
| --- | --- | --- |
| Tasks | Scans, triage; ordinary Actions can implement other tasks | Similar tasks, with shared authoring and execution framework |
| Model choice | Claude only | Multiple engines behind a shared workflow definition |
| Writes | Python validators and separate jobs maintained here | Built-in safe outputs and separate privileged execution |
| Isolation | Tool restrictions and restricted CLI; no network firewall | Agent sandbox and network policy |
| State and deduplication | Explicit bot-owned issue protocol, consumer compatibility | Framework memory/queue options; exact migration requires adapter work |
| Authentication | Official Claude subscription OAuth or an API variant | Claude engine uses API/WIF or supported Copilot inference, not subscription OAuth |
| Maintenance | Small explicit Python, but we own the safeguards | More generated configuration and framework upgrade dependencies |
| Cost | Existing subscription capacity is shared; Actions costs may apply | Inference billing plus Actions; framework credits are guardrails, not invoice guarantees |

The kit is not a replacement implementation of gh-aw. For these two established lanes it preserves
existing credentials, outputs, and policies. gh-aw becomes more attractive when multi-engine
experiments, a network sandbox, built-in write types, and centralized policy save more maintenance
than adopting its compiler/runtime costs.

## Reproducible pilot

`examples/gh-aw/source-scan.md` is a manual, read-only Claude workflow with staged create-issue output,
32 turns, a 15-minute agent step, and 100 AI credits. The generated agent job allows 60 minutes
including setup and cleanup. The compiled lock file was generated with gh-aw v1.0.1:

```sh
gh aw compile examples/gh-aw/source-scan.md --gh-aw-ref v1.0.1 --validate --no-check-update
```

It is stored outside .github/workflows and is not deployed or scheduled. Compilation succeeds.
The compiler flags the intentionally required ANTHROPIC_API_KEY as a new restricted secret; this
is not evidence that credentials exist or inference ran. The compiler pins framework actions to
an immutable revision. Runtime/container validation and quality/cost comparison require a live run.

For a comparison, freeze one actual kit plan, source SHA, prompt, schema, model, and turn limit.
Provide those same materials under comparison/ to gh-aw; the prompt asks for exactly the same JSON.
Download the staged JSON and run the kit validator against the original source and plan. Record:

- required-file coverage, valid exact quotations, and missing/invalid output;
- candidate accuracy and usefulness, judged independently from source;
- elapsed time, turns, token usage, reported estimated cost, and actual billing separately;
- setup effort, permissions, generated code, failure/retry behavior, and audit evidence.

One paired run demonstrates interoperability and exposes failure modes. It cannot establish a
reliable quality ranking or an expected monthly cost. Existing Kuratowski trial reports found one
valid simplification and one overstated rationale; those are not a fair gh-aw benchmark.

Staged mode previews built-in outputs only. It does not disable custom jobs, arbitrary scripts,
network access, or memory writes. These pilots declare no custom write job or repository memory.
They explicitly disable automatic failure issues and issue creation by missing-tool, missing-data,
incomplete, and no-op reports. The compiled workflow still grants `issues: write` to framework jobs;
do not equate staged mode with a read-only token. Recheck both configuration and generated YAML
when upgrading. See [safe-output reporting controls](https://github.github.com/gh-aw/reference/safe-outputs/).

## Sources (checked 2026-11-10)

- [Continuous AI concept](https://githubnext.com/projects/continuous-ai/)
- [gh-aw authentication](https://github.github.com/gh-aw/reference/auth/)
- [Claude engine](https://github.github.com/gh-aw/reference/engines/claude/)
- [Staged mode scope](https://github.github.com/gh-aw/reference/staged-mode/)
- [AI-credit guardrails](https://github.github.com/gh-aw/reference/cost-management/)
- [Official Claude Actions authentication](https://code.claude.com/docs/en/github-actions)

GitHub's latest release API and local CLI both reported v1.0.1 (published 2026-10-27).
Do not treat earlier conversational references to v1.2.0 as verified release information.

## Frozen Kuratowski pilot

`examples/gh-aw/kuratowski-scan.md` is a ready-to-install manual pilot. It checks out the exact
Kuratowski source from the first kit run (e0cd8279005656d966398615bfd707643036edc8) and materializes
that run's plan, prompt, and schema. The human-readable inputs are also in examples/gh-aw/comparison/.
The host step only decodes these literal public files; it runs no checked-out repository code.
It is outside .github/workflows, so neither its Markdown nor lock file enables a workflow here.

The first Action run 42820181521 reached model success but failed validation: two evidence paths
were absent from reviewed_files. This failure is retained as the comparison baseline, not hidden
by selecting a later successful output. The validator correctly prevented publication. The kit's
one explicit retry is a separate operational recovery test, not the same experimental sample.

To run after API authentication is ready, copy the Markdown to .github/workflows in a reviewed PR,
compile it with gh-aw v1.0.1, review the generated workflow and new secret use, then dispatch once.
Use ANTHROPIC_API_KEY in the executing repository; never reuse a subscription OAuth token.
The default sandbox, staged output, and disabled Bash/edit/GitHub tools remain in effect.
No external writes are performed by the custom input-materialization step. Staging itself is not
what constrains that step; its inspected code does. Python runs with `-I` so the checked-out root
cannot shadow the standard-library imports.

This is a comparison of the two operating setups, not an isolated comparison of the same model.
The Action uses its own prompt wrapper, `--json-schema`, `Read/Glob/Grep`, and explicit denials for
Git metadata, environment files, and `/proc`. gh-aw adds a different wrapper and sandbox, supplies
the schema as a file, and requests JSON in a staged issue body. These differences can affect format
compliance, tool use, quality, and cost even though the source, task prompt, and model name are fixed.

For validation, use a clean Kuratowski checkout at the source SHA above and a separate kit checkout
at `d17b8cf395a9bc6fc48799850f4d80c72386e767`, the validator used by the baseline. Preserve the
original plan under `.continuous-ai/plan.json`; save the staged JSON as a file outside the checkout.
From the source checkout, run the following with absolute paths substituted. This reads local files
and writes only `.continuous-ai/review.json`; it does not call `prepare` or `publish`.

```python
import os
from pathlib import Path
import subprocess

env = os.environ.copy()
env.update(CAI_TOOL_SHA="d17b8cf395a9bc6fc48799850f4d80c72386e767",
           CAI_DEFAULT_BRANCH="main", REVIEW_CONCLUSION="success",
           REVIEW_JSON=Path("/path/to/staged-output.json").read_text())
subprocess.run(["python3", "-I", "/path/to/pinned-kit/cli.py", "validate",
                "--config", ".github/continuous-ai/lean.json"], env=env, check=True)
```

Record a validation rejection as an outcome. Do not repair the model's JSON for a reported success.
Use later prompt or validator versions only in a separately identified experiment.
