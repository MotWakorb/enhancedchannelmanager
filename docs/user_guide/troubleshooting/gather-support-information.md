# Gather Support Information

Collect these before you ask for help. Everything on this list is cheap to
produce, and a report that includes it usually gets a useful answer on the first
reply instead of the third.

Work top to bottom. The first three items belong in every report; the rest depend
on what broke.

## 1. Your exact version

The version string is shown in the header, inside the service status pill. The
authoritative copy comes from the backend, on an endpoint that needs no
credentials:

```bash
curl http://<host>:6100/api/health
```

```json
{"status":"healthy","service":"enhanced-channel-manager","version":"0.18.1-0005","release_channel":"dev","git_commit":"a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"}
```

Quote what `/api/health` returns. Prefer it over the header pill: if you have
ever deployed a frontend build and a backend build separately, the two can
disagree, and the backend's answer is the one that describes the code actually
serving your requests.

Quote the whole string, build suffix included. `0.18.1` and `0.18.1-0006` are not
the same thing: the four-digit suffix is a CI build number, and a fix can be in
one build and not the one before it. `git_commit` pins it exactly.

See [`docs/versioning.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/versioning.md) for how to read a version string
and check whether a specific fix is in your build.

## 2. What you did, what you expected, what happened

Three sentences. The one people leave out is the second.

Include the exact wording of any error message or banner. Do not summarise it:
ECM's messages name the field or subsystem that refused, and that detail is
usually what identifies the bug. [UI banners and warnings](ui-banners-and-warnings.md)
is the catalogue if you want to check what you saw against what it means.

## 3. A debug bundle

ECM builds a redacted diagnostic archive for you. This is the single highest-value
attachment on a bug report, and it removes several rounds of "can you also send
me...".

Two bundles exist, with different scopes.

### The App Debug Bundle

Go to **Settings → General**, scroll to **Logging**, and choose **Generate App
Debug Bundle**.

![The Logging section of General Settings, showing the Backend Log Level and Frontend Log Level dropdowns and the App Debug Bundle description above its Generate App Debug Bundle button.](../../images/user_guide/troubleshooting/2-logging-settings.png)

The download is a `.tar.gz` covering the whole application: channels, rules,
settings, recent log lines, and a channel-groups diagnostic.

### The Pipeline Debug Bundle

On the **Channel Pipeline** page, use **Pipeline Debug Bundle**. It is smaller
and scoped to rule execution. Use this one when the problem is a rule that is not
doing what you expect, and the App bundle when the problem is anything else.

The Pipeline bundle has a second use: it can be analysed without touching your
live installation. Somebody helping you can run the rule analyzer against the
bundle you sent them, on their own machine, and never see your live channel data.
See [Debugging Rules](../channel-pipeline/debugging-rules.md#bundle-mode).

### What the bundle redacts, and what it does not

Know this before you attach one to a public issue.

Redacted, replaced with `***REDACTED***`:

- Your Dispatcharr password and API key
- The SMTP password
- The Telegram bot token and chat id
- The Discord webhook URL
- The MCP API key
- Your Dispatcharr username

Obfuscated rather than removed:

- Stream URLs, everywhere they appear
- The log lines included in the bundle

Kept deliberately:

- **The Dispatcharr host and port.** Any username or password embedded in the URL
  is stripped, but the address itself is retained because it is diagnostically
  useful. If your Dispatcharr address is something you do not want in a public
  issue, say so and ask where to send the bundle privately.

The bundle does not know which of your channel and group names you consider
private. Skim it if that matters to you.

### Attaching a backup artifact

A **standard** backup, meaning an unencrypted one, is the only backup format
appropriate to consider for a support ticket. It carries no part of a provider
credential (not the password and not the username), none of your ECM
accounts, no journal or telemetry, and no notification or cloud-storage
credentials. See
[What a standard backup does not carry](../backup-restore/backup-overview.md#what-a-standard-backup-does-not-carry)
for the full account of what is removed and what is kept.

What it does still carry is your configuration itself: channel and group
names, rule definitions and notes, and any provider address that did not have a
credential in it. An address that did carry one, in its userinfo or its
query string, is replaced whole rather than trimmed, so it is not there to
leak. Review the artifact for text and addresses you do not want to disclose.
If any of that content is private, say so and ask where to send the artifact
privately.

!!! danger "Never attach an encrypted backup taken with Include credentials"
    That artifact is the migration path and it deliberately carries
    everything: provider passwords, your ECM accounts and their password
    hashes, cloud-storage credentials. The passphrase is the only thing
    protecting it. Send a standard backup instead.

    An artifact taken with a passphrase but **without** Include credentials
    is redacted like a standard one, but nobody can tell the two apart from
    the filename. If you are not certain which you have, take a fresh
    standard backup and send that.

## 4. A log slice

If you are not sending a bundle, send logs. Bound them to the window in which the
problem happened rather than dumping everything:

```bash
docker logs --since 30m ecm-ecm-1 > ecm-log-slice.txt 2>&1
```

If you know the failing request, filter to its `trace_id` instead, which gives a
short and highly readable excerpt. See
[Read the logs](read-the-logs.md#following-one-request-through-the-logs).

**Scrub before you paste.** Log lines can carry your Dispatcharr hostname,
internal IP addresses, provider account names, and stream URLs with credentials
in the query string. Unlike the debug bundle, raw `docker logs` output is not
redacted for you.

## 5. Journal entries, when data changed unexpectedly

If the complaint is "something changed and I don't know what changed it", the
Journal is the record. Filter to the relevant category and time window, expand
the entry, and include the before/after values.

![The Journal filtered to the Channel category, with one Update entry expanded to show its BEFORE and AFTER panels side by side.](../../images/user_guide/troubleshooting/4-journal-before-after.png)

The **Source** column matters as much as the diff: it tells you whether a change
came from the UI, from an automation, or from an integration. See
[Journal](../journal/index.md).

## 6. For a UI bug: browser and console

- Your browser and version.
- The browser console output. Open developer tools with F12 and copy anything
  logged around the failure.
- The `x-request-id` response header of the failing request, from the network
  panel. That value lets whoever is helping you find the exact request in the
  backend logs.

Turning **Frontend Log Level** up to `DEBUG` on **Settings → General** before
reproducing gives a much more useful console transcript. Turn it back down
afterwards.

## 7. Your Dispatcharr version, when the problem crosses the boundary

If the symptom involves channels, streams, or EPG data not matching between the
two systems, include the Dispatcharr version.

**ECM does not display it.** Get it from Dispatcharr's own interface. Do not
report ECM's version as if it were Dispatcharr's.

## A copyable checklist

```
ECM version:            (from the header pill or /api/health, including the build suffix)
Deployment shape:       (direct, or behind a reverse proxy: which one)
Dispatcharr version:    (from Dispatcharr, if the problem crosses the boundary)
What I did:
What I expected:
What happened:          (exact message text)
Attached:               (debug bundle / log slice / journal entries / console output)
```

## Going deeper

- [Escalation paths](escalation-paths.md): where to send all of this.
- [Read the logs](read-the-logs.md): producing a useful log slice.
- [General Settings](../settings/general-settings.md): the Logging controls and the App Debug Bundle in their own settings context.
- [`docs/versioning.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/versioning.md): reading a version string and checking whether a fix is in your build.
