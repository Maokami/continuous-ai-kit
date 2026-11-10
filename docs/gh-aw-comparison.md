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
32 turns, 15 minutes, and 100 AI credits. The compiled lock file was generated with gh-aw v1.0.1:

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
network access, or memory writes. This pilot declares no custom write job or repository memory.

## Sources (checked 2026-11-10)

- [Continuous AI concept](https://githubnext.com/projects/continuous-ai/)
- [gh-aw authentication](https://github.github.com/gh-aw/reference/auth/)
- [Claude engine](https://github.github.com/gh-aw/reference/engines/claude/)
- [Staged mode scope](https://github.github.com/gh-aw/reference/staged-mode/)
- [AI-credit guardrails](https://github.github.com/gh-aw/reference/cost-management/)
- [Official Claude Actions authentication](https://code.claude.com/docs/en/github-actions)

GitHub's latest release API and local CLI both reported v1.0.1 (published 2026-10-27).
Do not treat earlier conversational references to v1.2.0 as verified release information.
