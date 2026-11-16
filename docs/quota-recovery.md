# Automatic Claude quota recovery

Source scan and PR triage persist quota waits instead of keeping an Actions runner asleep.
New callers include an hourly `17 * * * *` **recovery-only** schedule. It resumes an outstanding
quota wait; it does not start new maintenance work. Start the first task manually, then add a
different cron if you want regular new scans/triage. Keep both triggers in the same serialized caller.

```text
run → usage_limit → persist waiting + next_attempt_at → finish runner
                         ↓ hourly recovery check, no Claude call before due
                 collect current inputs → retry → validate → publish
```

## Policy

- A rejected SDK `rate_limit_event` may provide `resetsAt` (Unix seconds). A bounded future timestamp
  is used with five minutes of margin and 0–15 minutes of deterministic jitter. Prose reset dates are
  not parsed. Missing/invalid reset metadata falls back to 1, 2, 4, 8, 16, then 24-hour intervals,
  plus jitter. Each run makes at most one model invocation; it never polls Claude just to check quota.
- At most 20 quota deferrals or 14 days from the first limit are allowed per recovery episode.
  The next check after that bound reports `quota_recovery_exhausted` and skips inference. The counter
  survives source/runtime changes. A maintainer can dispatch with `reset_quota=true` to start a new
  episode after investigating. Normal subscription resets should require no intervention.
- Only `usage_limit` is deferred. `billing_error` (including SDK `credits_required`), expired/malformed
  credentials, model access, turn limits, missing output and validation failures remain real failures.
  Recovery does not purchase credits, enable extra usage, change credentials, or switch to an API key.
- A deferred run completes without publishing an AI result. Green means the wait was recorded, not
  that analysis succeeded. State issues and the Actions step summary expose the wait/next timestamp.
  If recording fails, the workflow fails rather than claiming a durable wait.

The SDK event shape is documented in the [official TypeScript reference](https://code.claude.com/docs/en/agent-sdk/typescript#sdkratelimitevent).
The pinned Action may omit reset metadata; fallback recovery does not depend on that field being present.
Diagnostics forward only a fixed category and a bounded integer, never provider prose or credentials.
These hints can be model-influenced and are not authorization: they only select a bounded deferral,
while every retry must pass the existing source/snapshot, schema and publication checks.

## State and freshness

Scan stores an optional `quota` object in its existing bot-owned state issue and marks the reserved
batch `waiting_for_quota`. Resuming that batch retains its task attempt number; quota deferrals do
not spend the existing single manual retry for ordinary failures. Deferred batches have priority.
An updated source/package/config is collected into a new plan after cooldown; old output is never
replayed. The existing exact-source validation and report-ID reconciliation still govern publishing.

Triage uses a separate bot-owned `[Continuous AI] <marker_id> quota recovery` issue, created only on
the first quota failure. It re-collects current PR facts when due. If the report already matches or
there are no open PRs, it clears the wait without calling Claude. Otherwise it validates the new AI
output and re-collects before publishing as before. This extra state issue is not the report issue.
The deterministic collect job has `issues: write` for an explicit manual reset; inference remains
read-only and only the inference job receives the OAuth secret.

Keep `cancel-in-progress: false` and one concurrency group per lane. Duplicate/older outcome records
cannot extend or clear a newer wait. Do not use partial job reruns; use the caller's dispatch instead.
Forced cancellation or loss of the state-write job can still leave an unfinished reservation. Recovery
does not automatically infer that such a reservation was a quota failure.

## Adoption, migration and stopping

Use a reviewed **runtime SHA containing this feature** with the updated generator. Old SHA pins do
not gain new inputs automatically. Existing callers need a pin update plus the recovery schedule
and input wiring shown by `onboard.py` preview. Do not generate a second caller for an existing lane.

For a custom caller, add `17 * * * *` and pass:

```yaml
recovery_only: ${{ github.event_name == 'schedule' && github.event.schedule == '17 * * * *' }}
reset_quota: ${{ inputs.reset_quota || false }}
```

Also declare a boolean `reset_quota` dispatch input (default false). If you change the recovery cron,
change its comparison too. Omitting `recovery_only` makes a schedule a normal new-work trigger.
The reusable workflows also honor existing callers: without an hourly check, a due quota wait resumes
at the next normal scheduled/dispatch run. Manual-only old callers cannot wake themselves.

Existing ordinary `error` entries have no trustworthy saved failure cause and are **not** automatically
converted into quota waits. Start a normal run after updating the runtime; use the ordinary explicit
retry only when the same digest still applies. Once a new run records a quota wait, recovery is automatic.
Older runtimes reject the new scan ledger fields/status; disable scheduling and plan state migration
before rolling back. Do not discard seen candidate IDs.

Set `CAI_QUOTA_RECOVERY_DISABLED=true` to stop scheduled runs of generated callers, or disable the
workflow to stop all triggers. This does not cancel an already running job. For the Kit itself,
`CAI_SELF_MAINTENANCE_ENABLED` controls new scheduled work; hourly recovery of an existing pilot wait
is independent of that flag and respects `CAI_QUOTA_RECOVERY_DISABLED`.

GitHub schedules run on the default branch and can be delayed or disabled by GitHub; they are not
exact timers. See [GitHub schedule behavior](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).
Recovery is per lane/repository, not a shared account-wide quota coordinator. Several repositories
using one subscription can still contend; bounded backoff and jitter reduce repeated calls but cannot
guarantee capacity or completion while the account remains unavailable.

## Verification boundary

Clock-controlled tests cover persisted waits, due-time resumption, repeated exhaustion, ordinary
failure budgets, changed source, successful/no-op completion, stale run IDs and malformed/spoofed
state. Workflow condition tests cover deferred publish suppression and deterministic state recording.
These tests do not establish that a real subscription reset and GitHub-scheduled retry succeeded.
Record the actual source/runtime SHAs and run links for that end-to-end pilot separately.
