# Continuous AI adoption brief

Replace the prompts below with observed repository facts and explicit choices. Remove sections that
do not apply. This is a design/evaluation record, not an automatically enforced runtime policy.

## Repository and objective

- Repository, default branch, inspected source SHA:
- Repeated bottleneck and affected person/workflow:
- Recent concrete examples (file/issue/PR links):
- Why AI judgment helps; deterministic checks already available:
- Existing automation and possible overlap:

## Lane contract (repeat for each selected lane)

- Name and route: supported CLI profile or adapter to implement:
- Expected useful result and its owner:
- Inputs: exact source globs, policy files, task/feedback sources:
- Required ground truth: source quotations, current CI facts, oracle, or external primary sources:
- Trigger and scope per run; manual pilot before chosen cadence:
- Allowed output and writes; explicitly identify whether source changes are permitted:
- Validator and follow-up verification commands; what those checks do not establish:
- State identity, duplicate suppression, stale-input behavior:
- Retry behavior: automatic quota recovery, ordinary failures, exhaustion/reset owner:
- Runtime bounds actually configured (batch/turn/time/output/open-task limits):
- Operational budget/usage target and who monitors it (not automatic enforcement unless implemented):
- Human acceptance decision; relation to implementation, merge, and publication:
- Proposed success criteria and review date (targets, not measured outcomes):
- Stop/narrow conditions and who performs the stop action:

## Installation record

- Local onboarding code revision and reviewed reusable runtime SHA:
- Generated config, prompt and workflow paths:
- Source selection count/bytes/batches; policy files excluded from source coverage:
- Exact required CI contexts for triage:
- Local validation and review results:
- Remote setup status: default branch, Actions policy, named secret present (never its value):
- Pilot authorization/status and proposed cadence after evaluation:

## Pilot observations

Use one row per actual run; leave unmeasured values explicitly unknown. Do not count an accepted
proposal as a merged fix, or a merged fix as verified publication. A useful empty result is valid.

| Run URL; source/package SHA; batch | Validator/publication result | Candidates; accepted/rejected/duplicate/pending | Follow-up issue/PR and outcome | Reviewer minutes | Runtime/usage and measurement source |
| --- | --- | --- | --- | --- | --- |

- Unchanged-input check (same batch for scan), model invocation and write behavior:
- False-positive or missing-context examples; prompt/scope correction:
- Reported API-equivalent estimate, actual invoice and subscription usage kept distinct:
- Baseline/comparison method before claiming time saved:
- Decision: keep manual / enable cadence / narrow scope / stop; reason and owner:
