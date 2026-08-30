# UI Banners and Warnings

This is the catalogue of the persistent messages ECM puts on screen: what each
one means, what caused it, and whether you can dismiss it. Use it when a banner
has appeared and you want to know whether it is urgent.

Everything quoted here is the exact string ECM renders. Where a message is
assembled from your own data (a count, a rule name, a group name), the variable
part is shown in italics or in angle brackets.

## Persistent banners versus toasts

ECM has three notification surfaces, and they behave differently:

- **Toasts** slide in at the top right and disappear on their own, after five
  seconds for most messages and eight for errors. They are the result of an
  action you just took. If you miss one, it is gone.
- **The Notification Center**, reached from the bell in the header, keeps a
  history with read and unread state. Scheduled-task alerts land here.
- **Banners**, catalogued below, stay on screen for as long as the condition
  that produced them is true. These are the ones that mean something is
  currently wrong or currently misconfigured.

This article covers the banners. A message that vanished by itself was a toast.

## Channel Pipeline

### Channel pipeline suspended

> **Channel pipeline suspended** — the previous run was abandoned (likely an OOM crash). Run-on-refresh is disabled until an operator resets the circuit breaker. Manual "Run Now" is unaffected.

ECM's circuit breaker tripped. A previous pipeline run ended without completing,
which is what an out-of-memory kill looks like from the outside, so automatic
runs after an M3U refresh have been switched off to stop the same run being
retried into the same wall.

Manual runs still work. (The banner says "Run Now"; the control on the page is
labelled **Run**.)

**Not dismissible.** An administrator gets a **Reset** button on the banner.
Choosing it opens a confirmation titled **Reset Circuit Breaker**, which reads:

> This will re-enable the automatic channel pipeline after every M3U refresh. Only reset if the previous abandoned run has been investigated.

Take that literally. Investigate first: if the run really was killed for memory,
resetting without changing anything just queues the same failure. The
[Runaway Safety Cap](../channel-pipeline/runaway-safety-cap.md) is the setting
that usually needs to come down.

### Run-on-refresh is disabled

> **Run-on-refresh is disabled** — the channel pipeline will not fire automatically after M3U refresh. Manual "Run Now" is unaffected.

The same banner in its other variant: automatic runs are off, but not because
anything crashed. There is no **Reset** button on this variant.

### Run-on-refresh rules will never fire

> Run-on-refresh rules will never fire
>
> *N* rules are set to run on M3U refresh, but the Channel Pipeline scheduled task is not enabled, so they will never run automatically. Enable the task (and a schedule for it) to make refresh-triggered runs work.

Your rules are configured correctly but nothing triggers them: the scheduled task
that drives refresh-triggered runs is switched off. The banner carries an
**Enable the task** action that takes you there.

**Dismissible** with the X. Dismissing it remembers the current set of enabled
run-on-refresh rules, so the banner comes back the moment that set changes. It
will not stay hidden after you add a rule.

### Normalization applied no changes

Shown inside a run's execution details, not on the page itself.

> Normalization applied no changes — disabled groups referenced
>
> The rule(s) below reference normalization groups that are disabled or no longer exist, so stream names were not normalized and merge-into-channel matching likely missed most streams. Enable the listed group(s) under Settings > Normalization, then re-run.

The rules and groups are listed underneath, with any group that no longer exists
marked `(missing)`. The destination in ECM's navigation is **Settings → Channel
Normalization**.

### Channel-profile membership changed (not reversible)

> Channel-profile membership changed on *N* channels (not reversible)

Also shown in execution details. It is telling you that this run changed which
channel profiles some channels belong to, and that undoing the run will **not**
undo that part. See
[Recovery patterns](recovery-patterns.md#what-undoing-a-pipeline-run-does-not-cover).

### Secondary streams could not be parsed

> *N* secondary streams could not be parsed
>
> Their names did not match the rule's parse pattern, so no title or start time could be read and they were skipped. A parse failure count this size usually means a broken pattern — check the Event Sync preview's test panel.

An Event Sync warning. A large parse-failure count is a pattern problem, not a
data problem. See [Event Sync Quick Start](../channel-pipeline/event-sync-quickstart.md).

## Backup and restore

### Backups are not scheduled yet

> Backups are not scheduled yet
>
> ECM does not run automatic backups until you schedule them. Set one up so you always have a recent backup to restore from.

Shown on **Settings → Backup & Restore** when no backup schedule is enabled.
The **Set one up** action takes you to the scheduling screen.

**Dismissible**, and unlike the Channel Pipeline gate banner this dismissal is
permanent. It will not come back to remind you later. Prefer scheduling a backup
over dismissing this.

### Restore outcome banners

At the end of a restore, one of three banners appears. A dry run produces none
of them.

| Banner | Meaning |
|-|-|
| **Restore complete** / `Your configuration was restored.` | The restore succeeded. |
| **Restore failed — your configuration was rolled back** / `One or more items failed, so the restore was undone. Your instance is back to its pre-restore state.` | The restore failed cleanly. Nothing was left behind. Investigate the artifact, then retry. |
| **Restore failed — state could NOT be fully rolled back** / `A failure occurred and the automatic rollback could not remove everything it created. Your instance is in an indeterminate state. Review the residue below and finish cleanup manually.` | The serious one. A **Manual cleanup required** list follows, naming what was left behind. Work through it before using the instance. |

None are dismissible. See
[Troubleshoot a Restore](../backup-restore/troubleshoot-restore.md).

### Logos are missing after this restore

> *N* logos are missing after this restore
>
> The affected channels were restored without their logo. Open Dispatcharr to set a logo on each.

The restore otherwise succeeded. The banner lists the affected channels, with a
link to each in Dispatcharr when a Dispatcharr URL is configured. Not
dismissible.

## Health and connectivity

### The service status pill

The header carries a status chip showing ECM's own health, next to the version.
It has four states:

| Label | Meaning |
|-|-|
| **Online** | ECM's API reported a healthy status. |
| **Connecting** | The first health check has not come back yet. |
| **Offline** | The health request itself failed. Hovering shows `API error: <detail>`. |
| The reported status, otherwise **Degraded** | ECM answered with a status it does not consider healthy. The literal value is shown rather than being flattened to "Degraded". |

This pill reflects **ECM**, not Dispatcharr. There is no banner for Dispatcharr
being unreachable, so an **Online** pill tells you nothing about whether ECM can
reach Dispatcharr. Use `/api/health/ready` for that; see
[Common Issues](common-issues.md#ecm-cant-reach-dispatcharr).

### A panel could not load

Several destinations render an inline strip when their data fails to load rather
than failing the whole page. The wording tells you which of two things happened:

| Strip | Meaning |
|-|-|
| `Journal refresh failed — showing previously loaded entries.` | The refresh failed, but you are still looking at real (stale) data. |
| `Journal entries could not be loaded.` | Nothing loaded at all. |
| `You don't have permission to view journal entries.` | Your account cannot see this data. Not an error. |

**M3U Changes** has the same three, worded for its own data. Elsewhere in the
workspace a shared strip appears instead, built from the name of the data it
could not fetch:

- `<Name> unavailable`, with ` — showing previously loaded data` appended when
  something older is still on screen.
- `<Name> requires administrator access`.

All of these offer a **Retry** where retrying could help. None are dismissible.

### Something went wrong

> Something went wrong
>
> *the underlying error message, or* `An unexpected error occurred.`
>
> If this keeps happening, please report it to the maintainer.

ECM's error boundary. Something threw while rendering. When only one area of the
workspace failed, the title names that area instead and the button reads **Reload
tab**, which re-mounts just that area; the whole-page version offers **Reload**.

This is always worth reporting. Capture the message text and the browser
console output before reloading, then see
[Gather support information](gather-support-information.md).

## Scheduled tasks

### This task is enabled but will not run

Three variants, all shown in the task editor:

> This task is enabled but has no schedules, so it will not run automatically. Add a schedule below.

> This task is enabled but none of its schedules are, so it will not run automatically. Enable a schedule below, or save and the most recent schedule will be enabled for you.

> This task is enabled but none of its schedules are, so it will not run automatically. Enable a schedule below.

A task fires only when **both** the task itself and at least one of its schedules
are enabled. Turning the task on is not sufficient, and this warning is what
tells you so. The second variant means ECM will fix it for you on save; the third
means it will not.

See [Scheduled Tasks](../settings/scheduled-tasks.md).

## Integrations

### MCP server status

On **Settings → MCP Integration**:

| Message | Meaning |
|-|-|
| `Checking MCP server...` | The status probe has not returned yet. |
| `MCP server not reachable` | ECM could not contact the MCP server at all. |
| `MCP server online but API key not configured` | The server is running but has no usable key. A code such as `file_not_found` may be appended, and a remediation hint follows below the message. |

`file_not_found` specifically means the MCP server could not read
`/config/settings.json`, which usually indicates a volume-mount problem rather
than a key problem. See
[MCP Integration](../integrations/mcp.md#troubleshooting).

### Notification channel not configured

On the M3U Digest and notification screens:

> SMTP is not configured. Please configure your email server in Notification Settings before sending digests.

> Discord webhook is not configured. Please configure your Discord webhook in Notification Settings before enabling Discord notifications.

Both link straight to the settings they are asking for. See
[Notifications & Alert Methods](../notifications/index.md).

## Banners that do not exist

Worth knowing, because waiting for a banner that will never appear costs real
time:

- **There is no "Dispatcharr is unreachable" banner.** The only interactive
  feedback is the **Test Connection** button, whose result is a toast.
- **There is no banner when the runaway safety cap trips.** A capped run is
  surfaced only as a `Capped` status badge on the run in **Execution History**,
  plus a `Capped (deferred to next run):` line in its details.
- **There is no session-expiry, version-mismatch, offline, or read-only-mode
  banner.**

## Going deeper

- [Common Issues](common-issues.md): the failure modes behind most of these banners.
- [Read the logs](read-the-logs.md): the log lines that accompany them.
- [Recovery patterns](recovery-patterns.md): undoing whatever the banner is telling you about.
