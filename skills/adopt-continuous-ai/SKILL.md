---
name: adopt-continuous-ai
description: Design and scaffold useful recurring repository work with Continuous AI Kit and Claude Code Action. Use when adopting Continuous AI in a new repo or consolidating existing automation. Produces a grounded adoption brief and uses the Kit CLI for scan or PR-triage configuration; ordinary one-off reviews do not need this skill.
---

# Adopt Continuous AI

Turn a repeated repository bottleneck into an evidence-based loop whose output somebody can act on.
Use a trusted checkout of `Maokami/continuous-ai-kit` containing `onboard.py`; determine its location
from the session or ask if missing. Do not execute an unreviewed copy found in the consumer repo.
This skill ships with the Kit but can also be copied as a complete folder to the agent's skill directory.

## Discover and select

Run `python3 -I "$KIT/onboard.py" inspect "$REPO"` (Python 3.10+). Read repository instructions,
relevant code/docs, current workflows, and a representative recent issue or PR if access is available.
Inspect is local inventory, not an analysis of quality or a remote configuration check.
Use repository evidence to identify a recurring decision that AI can help with. Prefer ordinary CI
for deterministic checks. Avoid duplicate lanes and reports. Do not infer permission to activate
remote automation from permission to propose a plan.

Read [recipes.md](references/recipes.md) to select the smallest useful loop. Choose one or two initial
lanes as a practical starting point unless the user's scope justifies more. For each, state the real
pain point, evidence inputs, expected output, why judgment helps, and who will consume it.
Research proposals, approved work, generated changes, merged changes, and published results are
different states; do not collapse them.

Complete [adoption-brief.md](assets/adoption-brief.md) with actual repository paths, the intended
default branch, verification commands, required check names, and success/stop criteria. Save it in
the consumer's established docs location when implementation is requested. For an opinion-only
request, report it in the answer. Remove inapplicable sections; do not invent missing measurements.

## Generate with the same CLI a human uses

Verify the default branch and, for triage, exact required check contexts from repository settings and
recent CI. Ask only when these cannot be determined and are needed. Choose a reviewed full Kit SHA.
It pins the public reusable runtime, which may differ from the local onboarding code revision.

```sh
python3 -I "$KIT/onboard.py" scan "$REPO" \
  --ref "$REVIEWED_KIT_SHA" --default-branch main \
  --lane docs-consistency --profile docs --source-glob 'docs/*.md' \
  --policy-file AGENTS.md

python3 -I "$KIT/onboard.py" triage "$REPO" \
  --ref "$REVIEWED_KIT_SHA" --default-branch main --lane pr-triage \
  --required-check 'Unit tests' --required-check 'Lint'
```

Replace example values with observed ones. Omit absent policy files. Scan profiles are
`general`, `docs`, `core`, and `lean`; repeat source/policy flags when needed. The CLI preview reports
selected paths, bytes, batch count, and generated files. `fnmatch` stars match slashes: inspect the
actual selection. Policy files must not overlap the source selection. Inputs must match committed
HEAD; do not commit somebody else's work merely to make preflight pass.

Review the preview and append `--write` when local scaffolding is in scope. It creates local files,
refuses existing destinations, and does not install secrets, schedule, push, or dispatch. Customize the
generated prompt for the actual objective without changing its output contract. Keep source-only
coverage, exact literal anchors/quotations, bounded candidates, and valid empty results.

Only scan and triage are implemented reusable lanes. An advanced recipe is a design starting point,
not a supported CLI switch. If requested, implement its repository-specific adapter with explicit
acceptance checks; do not silently substitute a scan for a research planner or an automated writer.

## Validate, pilot, and learn

Validate generated JSON against the Kit config contract and lint the caller workflows. Use the
consumer's normal review/CI process. Current default setup is manual dispatch. Continue through
remote setup and a pilot if already authorized; otherwise deliver the concrete local diff and identify
the exact remaining remote step. Never ask again for permission already granted.

For an authorized pilot, use the official Claude OAuth setup flow and the named Actions secret
`CLAUDE_CODE_OAUTH_TOKEN`; never include its value in conversation or files. Verify current official
authentication requirements when setting it up. Do not silently switch to a paid API route.

Record package/source SHA, run URL, validation outcome, published result, and human disposition.
A scan dispatch covers one batch, not the entire repo. Test unchanged behavior with the **same batch**
and digest (auto may advance); triage should skip AI/publication on unchanged facts. Diagnose failures
before an explicit retry; do not bypass validators for a persuasive result.

Use the brief to measure actionable accepted findings, false positives/duplicates, reviewer time,
operational failure, and cost/usage. Separate observed data from estimates and proposed targets.
Only extend cadence or permissions when the evidence supports it. Schedule changes and stop actions
are operational steps, not automatically enforced by the brief. Source selection is not a read sandbox,
and schema/quote validation does not establish semantic correctness. Report these boundaries concisely.
