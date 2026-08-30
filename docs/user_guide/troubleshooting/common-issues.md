# Common Issues

Start here when something in ECM stops behaving. Each section below gives you
the symptom, the first things to check, and where to go if the quick checks
don't resolve it.

Everything quoted here is a string ECM actually produces. If you see a message
that isn't in this guide, read it literally: ECM's error text names the field
or the subsystem that refused, and that is usually enough to pick the right
section.

## The container won't start

ECM runs a set of preflight checks before it starts listening. On a healthy
start, the last preflight line is:

```
All preflight checks passed!
```

Each individual check prints a `✓` on success. A check that fails prints a `✗`
and the container **exits without starting**. Read the last twenty lines before
the exit to see which check failed:

```bash
docker logs --tail 40 ecm-ecm-1
```

The checks cover user/group identity, the Python environment, the config
directory (exists and is writable), the frontend build, port availability, and
whether the application module imports.

One preflight result is **advisory** and does not stop startup: the
config-persistence check. If `/config` is not on a mounted volume, ECM warns
but starts anyway, and everything you configure is destroyed the next time the
container is recreated. See
[Install ECM](../getting-started/installation.md#give-ecm-a-place-to-keep-its-data)
for the fix. A healthy instance logs:

```
✓ Config directory /config is on a mounted volume (data persists across recreates)
```

## ECM can't reach Dispatcharr

The fastest check is the readiness endpoint, which needs no credentials:

```bash
curl http://<host>:6100/api/health/ready
```

Look at `checks.dispatcharr`. It reports one of three statuses:

| `status` | `detail` looks like | What it means |
|-|-|-|
| `ok` | `reachable (HTTP 200)` | ECM got an HTTP response from Dispatcharr's base URL. |
| `skipped` | `not configured` | No Dispatcharr URL is saved yet. This is a valid first-run state, not a failure. |
| `fail` | `timeout after 3.0s`, or an exception class name such as `ConnectError` | ECM could not get a response. |

The whole endpoint returns **503** when any required check fails, and `200`
when everything is `ok` or `skipped`. The Dispatcharr result is cached for 30
seconds, so a fix will not show up on the very next poll.

In the UI the same failure shows up indirectly: channels and streams fail to
load or populate across the workspace. ECM has settings but nothing to manage.
There is **no dedicated "Dispatcharr is down" banner**, so do not wait for one.

To retest the connection interactively, go to **Settings → General**, open the
**Dispatcharr Connection** card, and choose **Edit**. That opens the
**Dispatcharr Connection Settings** dialog, whose **Test Connection** button
runs a live probe. **Save** stays disabled until the test succeeds, so you
cannot store a connection you have not proved.

The messages that button can return, verbatim:

| Message | What it means | What to do |
|-|-|-|
| `Connection successful` | Dispatcharr answered and accepted the credentials. | Save. |
| `Could not connect to server` | No HTTP response at all. | Check the URL, the port, and that Dispatcharr is running. |
| `Connection timed out` | Dispatcharr accepted the TCP connection but did not answer in time. | Check whether Dispatcharr itself is healthy or under load. |
| `Invalid API key` | The API key was rejected. | Regenerate the key in Dispatcharr and paste it again. |
| `Authentication failed: 403` (the number is the HTTP status Dispatcharr returned) | Dispatcharr answered but refused the credentials. | Re-check the username and password, or switch to API key auth. |
| `Dispatcharr is rate-limiting login (3/min per IP). Wait a minute or switch to API key auth.` | You have tested password auth too many times in a row. | Wait a minute, or switch the auth method to API key, which is not rate-limited this way. |
| `Dispatcharr rejected this server by network policy` | Dispatcharr's own outbound policy refused. | This is a Dispatcharr-side setting, not an ECM one. |

## "Invalid host" when saving a URL

ECM validates every outbound base URL it stores: the Dispatcharr URL and the
Emby, Plex, and Jellyfin base URLs. If the host is refused, the save fails with
a 400 and a message shaped like this:

```
Invalid Dispatcharr URL: Invalid host — Destination IP 169.254.169.254 is denied by SSRF policy (lan_friendly)
```

The field name at the front changes with the setting (`Invalid Emby base URL:`,
`Invalid Plex base URL:`, `Invalid Jellyfin base URL:`). The **Test Connection**
button returns the same explanation without the field prefix.

Two things about this policy are worth knowing before you start changing
settings:

- **Loopback, private LAN, and RFC 6598 shared addresses are allowed by default.**
  `http://localhost:9191`, `http://127.0.0.1:9191`, and RFC1918 addresses such
  as `192.168.1.50`, plus shared carrier/VPN addresses from `100.64.0.0/10`,
  are accepted under the shipped mode, which is called
  `lan_friendly`. If you have read older documentation claiming loopback is
  always rejected, that documentation is stale: it described a bug that was
  fixed under
  [GitHub issue #754](https://github.com/MotWakorb/enhancedchannelmanager/issues/754).
- **Some addresses are refused in every mode, and no setting will change that.**
  Link-local (`169.254.x.x`, which includes cloud metadata endpoints),
  IPv6 unique-local, multicast, and `0.0.0.0/8` are denied
  unconditionally. So are schemes other than `http` and `https`.

The one operator-facing knob is on **Settings → Backup & Restore**, in the
**Where backups can be sent** card. It has exactly two choices:

![The Where backups can be sent card on the Backup and Restore settings page, showing the two radio options: Allow your home network (recommended), which is selected, and Public internet only.](../../images/user_guide/troubleshooting/1-where-backups-can-be-sent.png)

Leave it on **Allow your home network (recommended)** unless you deliberately
want ECM to refuse every address on your own network. Switching to **Public
internet only** is what makes a loopback, LAN, or `100.64.0.0/10` peer URL start failing.

One deliberate exception on the settings path: a host that cannot be **resolved**
at all is not treated as a policy denial. A LAN media server that happens to be
powered off stays saveable, and the connection is re-validated before ECM ever
actually connects to it.

## Requests fail in bursts behind a reverse proxy

**Symptom:** a UI action fails, hangs, or applies only partly, and the logs
carry repeated lines reading `Exceeded concurrency limit.` The behaviour
reproduces behind your reverse proxy and does not reproduce when you drive the
same action directly against ECM's port.

```bash
docker logs ecm-ecm-1 2>&1 | grep "Exceeded concurrency limit"
```

That line comes from uvicorn, not from ECM, so grep for it literally. It means
a burst of requests exceeded `ECM_LIMIT_CONCURRENCY` (default 100) in a single
instant, and everything past the limit was refused with a 503.

The reason it only shows up behind a proxy is about **connection multiplexing**,
not about anything being broken:

- A browser talking HTTP/1.1 straight to ECM caps itself at roughly six
  connections per origin. A large burst queues up in the browser and trickles
  in, so it rarely reaches ECM's limit. The browser is doing throttling ECM
  never has to do.
- A reverse proxy speaking HTTP/2 to the browser (nginx, Caddy, Traefik, or
  similar) multiplexes many requests over one TCP connection with no per-origin
  cap. The same burst arrives at ECM all at once.

Running ECM behind an HTTP/2 proxy is a normal, supported deployment. Do **not**
"fix" this by disabling HTTP/2 or removing the proxy: that only reinstates the
browser's incidental six-connection throttle as a workaround, and a large enough
burst will exceed the limit over HTTP/1.1 too.

The right first question is whether the burst size scales with your data. If a
single UI action fires one request per rule, per channel, or per stream, that is
an ECM bug to report (it should be using a bulk endpoint), not a limit to tune
around. That is exactly what
[GitHub issue #755](https://github.com/MotWakorb/enhancedchannelmanager/issues/755)
was. If the burst is a genuine one-off spike, raising `ECM_LIMIT_CONCURRENCY` is
the appropriate response. The full triage lives in the
[request-timeout runbook](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/runbooks/request-timeout.md) in the ECM
repository.

## The Channel Pipeline isn't creating channels

Work through these in order. The first three cost seconds and account for most
reports.

1. **Is the pipeline suspended?** Open **Channel Pipeline**. If a banner reads
   `Channel pipeline suspended` or `Run-on-refresh is disabled`, automatic runs
   after an M3U refresh are switched off. See
   [UI banners and warnings](ui-banners-and-warnings.md#channel-pipeline-suspended)
   for what each variant means and how to clear it. Manual **Run** is
   unaffected either way, so a manual run that works while refresh-triggered
   runs do nothing is a strong signal you are in this state.
2. **Is the scheduled task enabled?** If a banner reads
   `Run-on-refresh rules will never fire`, your rules are marked to run on M3U
   refresh but the Channel Pipeline scheduled task is not enabled, so nothing
   triggers them. The banner carries an **Enable the task** action.
3. **Is the rule enabled, and does it match anything?** The rules list shows a
   status and a match count per rule. A rule at `0` matches is not firing
   because nothing satisfies its conditions.

If the rule is enabled and the pipeline is running, the problem is usually in
the rule itself rather than in the engine. The most common causes are a regex
that matches everything, a `Contains` condition holding regex anchors, or an
OR branch that dropped its guard. Those are catalogued with worked examples in
[Debugging Rules](../channel-pipeline/debugging-rules.md).

Two run outcomes are easy to misread:

- A run whose status badge reads `Capped` stopped early because it would have
  created more channels than the per-run safety cap allows. The channels it
  already created stay in place, and re-running continues from where it stopped.
  The cap lives at **Settings → Channel Pipeline**, under **Runaway Safety Cap**.
  See [Runaway Safety Cap](../channel-pipeline/runaway-safety-cap.md).
- A run reporting channels updated but `0` created, when you pointed it at a
  fresh group, is usually a merge-scope problem rather than a matching problem.
  See
  [`MERGE_SCOPE_NOT_TARGET_GROUP`](../channel-pipeline/debugging-rules.md#merge_scope_not_target_group).

## Channel names weren't normalized

**Symptom:** a Channel Pipeline run completed, but the channel names are the raw
provider names, and merging into existing channels matched far fewer streams
than you expected.

Open the run in **Execution History** and look for this warning:

> Normalization applied no changes — disabled groups referenced

The rules listed underneath reference normalization groups that are disabled or
no longer exist, so stream names were never normalized before the rule
conditions ran. Enable the listed groups under **Settings → Channel
Normalization**, then re-run.

If normalization is enabled and the names still don't match what you typed,
suspect a Unicode mismatch before you suspect the rule. ECM canonicalises stream
names, strips certain invisible characters, and folds superscript letters and
digits to ASCII **before** any condition is evaluated, so a pattern typed
against the raw bytes will not match. The
[Unicode suffix surprises](../channel-pipeline/debugging-rules.md#unicode-suffix-surprises)
section explains how to confirm this with the Test Rules trace.

## EPG problems

EPG has its own symptom table covering missing guide data, wrong programmes,
stuck downloads, Schedules Direct limits, and dummy EPG template output. Go
straight to [Troubleshoot EPG issues](../epg/troubleshoot-epg.md).

## Restore problems

Restore failures also have a dedicated article, with the exact refusal messages
and what each one means: see
[Troubleshoot a Restore](../backup-restore/troubleshoot-restore.md).

One restore outcome deserves attention here because of what it asks of you.
If a restore ends with:

> Restore failed — state could NOT be fully rolled back

then a failure occurred and ECM's automatic rollback could not remove everything
it had created. The instance is in an indeterminate state, and the banner lists
the residue under **Manual cleanup required**. Work through that list before
using the instance again.

## Alerts aren't arriving

If an alert channel is configured but nothing reaches you, the cause is nearly
always a toggle rather than a delivery failure. Work the checklist in
[Notifications & Alert Methods](../notifications/index.md), which orders the
causes by how often they are the answer.

If every toggle is correct, the destination provider is probably rejecting the
message. That surfaces in the backend logs under `[ALERTS-SMTP]`,
`[ALERTS-DISCORD]`, or `[ALERTS-TELEGRAM]` at warning or error level. See
[Read the logs](read-the-logs.md) for how to filter to those tags.

## Going deeper

- [Read the logs](read-the-logs.md): the log format, the severity levels, and the tag vocabulary used above.
- [UI banners and warnings](ui-banners-and-warnings.md): the full catalogue of persistent banners, including the ones referenced here.
- [Recovery patterns](recovery-patterns.md): once you know what went wrong, how to undo it.
- [Gather support information](gather-support-information.md): what to collect before asking for help.
- [`docs/runbooks/`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/runbooks/README.md): incident-grade procedures for when a troubleshooting session has become an outage.
