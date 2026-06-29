# Runaway Safety Cap

> **Audience:** Operator whose auto-creation run stopped early with a "capped"
> result, or who is about to run a large rule and wants to understand the limit.

## What the cap is

Auto Creation has a built-in **per-run safety cap** on how many channels a
single run will create. It exists because a too-broad rule (or an upstream M3U
that suddenly balloons — think a PPV/event night where one provider exposes
hundreds of temporary streams) can otherwise try to create thousands of
channels in one run. That has crashed installs in the past by exhausting
memory, so the cap is a deliberate guardrail, not a bug.

When a run reaches the cap it **stops early** rather than continuing:

- The channels it already created **stay** — nothing is rolled back.
- The run is marked **"capped"** (you'll see this status on the run in the
  Auto Creation executions view) and a **"Capped"** notification is raised.
- The remaining matched streams are simply **not processed in that run**.

The default cap is **500 channels per run**.

## You can just run it again

Auto Creation is **idempotent**: re-running it does not duplicate the work it
already did. The channels created by the capped run are recognized on the next
run, so a second run **continues from where the capped run stopped**, creating
the next batch (up to the cap again). For a one-time large import you can simply
run Auto Creation a few times until a run completes without being capped —
without changing any setting.

This is usually the right response to a capped run. Raise the cap only if you
routinely create more than the cap allows in a single run and you understand the
memory cost.

## Where to change it

The cap is in **Settings → Auto Creation → Runaway Safety Cap**:

| Field | What it controls |
|-|-|
| **Max channels created per run** | The runaway safety cap described above. Default **500**. Set to **0** to disable the cap entirely (not recommended). |
| **Max execution-log entries per run** | A secondary memory guard: how many per-stream trace entries each (non-dry-run) run keeps in memory. The trace is the dominant memory consumer on a runaway run. Default **500**. Set to **0** to disable. Dry-runs always keep the full trace regardless of this value. |

Setting either field to **0 disables** that cap. Disabling the channel cap
removes the protection against a runaway run — only do this if you have a
specific reason and understand the risk.

> **Admin only.** These are install-wide safety settings, not per-user
> preferences. Only an administrator can change them. A non-admin (or an
> automated MCP client) that tries to change the cap is rejected — the
> Settings UI shows the inputs as read-only for non-admins.

## Older versions

Before this control existed, the only way to change the cap was to hand-edit
`max_auto_created_channels_per_run` in `/config/settings.json` and restart ECM.
That is no longer necessary — use **Settings → Auto Creation** instead. (The
underlying settings key is unchanged, so an install that previously set it by
hand keeps its value and now shows it in the UI.)
