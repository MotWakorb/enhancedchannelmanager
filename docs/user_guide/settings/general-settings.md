# General Settings

General Settings is the first destination under **Connections** in the
Settings navigation. It holds the Dispatcharr connection, stats-polling
cadence and timezone, and backend/frontend logging levels.

> First connecting ECM to Dispatcharr for the very first time? See
> [Getting Started](../getting-started/index.md) instead. This article
> covers changing an existing connection, not the initial setup.

## Common tasks

### Update your Dispatcharr connection

Use this when Dispatcharr's URL changed, you rotated credentials, or you're
pointing ECM at a different Dispatcharr instance.

1. Go to **Settings → General**.
2. In the **Dispatcharr Connection** card, click **Edit**.
3. Update the Server URL, auth method, and credentials as needed.
4. Save.
5. If you're switching to a *different* Dispatcharr server (not just
   rotating credentials on the same one), click **Reset Statistics** to
   clear channel/stream statistics carried over from the old server. Old
   numbers attributed to a different backend are misleading, not just
   stale.

**Result:** The Dispatcharr Connection card shows the new Server URL,
auth method, and (if username/password) username. The password field
always displays as `••••••••` regardless of length.

### Change how often ECM polls Dispatcharr for stats

1. Go to **Settings → General**.
2. Under **Stats Polling**, set **Poll interval (seconds)**. Lower values
   update bandwidth and channel statistics more often but use more
   resources.
3. Save.

**Result:** The new interval takes effect on save; no restart is required.

### Set the timezone used for daily stats and scheduled probes

1. Go to **Settings → General**.
2. Under **Stats Polling**, open the **Timezone** dropdown and choose your
   timezone (defaults to UTC).
3. Save.

**Result:** "Today" in bandwidth statistics now rolls over at midnight in
the selected timezone, and any scheduled probe times you configure under
[Maintenance](maintenance.md) run at the configured clock time in this
timezone rather than UTC.

### Turn up logging to debug an issue

1. Go to **Settings → General**.
2. Under **Logging**, set **Backend Log Level** to see more detail in the
   container's Docker logs, or **Frontend Log Level** to see more detail in
   the browser console (F12 DevTools). Both changes apply immediately, with
   no restart needed.
3. Save.

**Result:** Subsequent log output at or above the selected level appears
in the relevant log (Docker logs for backend, browser console for
frontend).

### Generate a debug bundle to attach to a bug report

1. Go to **Settings → General**.
2. Under **Logging**, click **Generate App Debug Bundle**.
3. Download the resulting `.tar.gz`.

**Result:** A tarball covering channels, rules, settings, recent logs, and
a channel groups diagnostic (with URLs, passwords, and tokens redacted),
ready to attach to an issue. For a Channel Pipeline-only bundle (smaller,
scoped to rule execution), use the Pipeline Debug Bundle button on the
Channel Pipeline page instead.

## Going deeper

- [Getting Started](../getting-started/index.md): first-time Dispatcharr connection setup.
- [`docs/architecture.md`](../../architecture.md): how ECM's polling and stats pipeline works end-to-end.
- [`docs/api.md`](../../api.md): API reference if you want to read or update these settings programmatically.
