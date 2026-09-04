# Backup & Restore: Overview

---

## What a backup is

A **Backup & Restore** backup is a single `.zip` artifact that captures your complete Dispatcharr + ECM configuration. It contains everything needed to bring a fresh Dispatcharr instance to the same operational state as the one that produced it, or to recover from accidental data loss on the same instance.

Backups are built by the `dbas_backup` task (scheduled or on-demand) and stored locally under `/config/backups/`. They can optionally be uploaded to off-host cloud storage for durability.

---

## What a backup contains (13 categories)

A backup covers the following configuration categories. All are included by default; a selective restore can opt individual categories out.

| Category | What is included |
|-|-|
| **M3U accounts** | Source URLs and account settings. The whole provider credential, username as well as password, is removed by default (see [Credentials and passphrase encryption](#credentials-and-passphrase-encryption)). |
| **EPG sources** | Source URLs and refresh settings. The whole provider credential, username as well as password, is removed by default. |
| **Channel groups** | Group names and structure. |
| **Channel profiles** | All channel profile definitions. |
| **Stream profiles** | All stream profile definitions. |
| **User agents** | Configured user-agent strings. |
| **Core settings** | ECM settings (`settings.json`). |
| **DVR rules** | Any configured recurring DVR recording rules. |
| **Upcoming recordings** | Recordings that are scheduled but have not started yet, so a restored instance still records them. Two kinds of recording are deliberately left out — see [Which recordings are backed up](#which-recordings-are-backed-up) below. |
| **Comskip config** | Comskip commercial-detection configuration. |
| **Users** | Dispatcharr user accounts (opt-in: see [User restore semantics](#user-restore-semantics)). |
| **Channels (with embedded streams)** | The full channel list, with their embedded stream assignments. |
| **Logos** | Logo files uploaded through ECM's own Logo Manager, plus their URL-mapping inventory. See [where an uploaded logo's bytes actually live](#where-an-uploaded-logos-bytes-actually-live) below; they are not stored under ECM's own `/config/uploads/logos/`. |

> **Plugins are not backed up.** Plugin state is excluded from v0.18.0 backups. This is a deliberate safety decision. Plugin restore semantics are not yet defined. If you rely on plugins, document your plugin configuration separately.

---

## Which recordings are backed up

A DVR recording is a scheduled slot on a channel. ECM backs up the ones a restored instance can still act on, and says so when it leaves one out — a backup run's message names the count.

**Backed up: recordings that have not started yet.** These are portable. A scheduled recording is a start time, an end time and one channel, all of which survive the trip to another instance. Restoring the backup schedules them again, so a restored instance does not silently miss a recording you had lined up.

**Not backed up: recordings that have already started or finished.** A finished recording *is* its video file, and that file lives on the disk of the Dispatcharr that recorded it. No backup ECM can take carries it, and Dispatcharr will not accept a recording scheduled in the past. A recording that is currently running is left out for the same reason plus one more: scheduling it on a second instance would start recording a programme that is already half over.

> **If you need those files, copy them yourself.** They are under Dispatcharr's own recordings directory on the source machine — the same path Dispatcharr shows for each recording. Copy them across to the restored instance's recordings directory before or after the restore; the restore itself will never touch them.

**Not backed up: recordings a recurring rule created.** Nothing is lost here. Those recordings are generated *from* a recurring rule, and the **DVR rules** category above carries the rule. Once it is restored, the destination Dispatcharr regenerates its own upcoming recordings from it, on its own schedule and in its own timezone. Backing up the generated recordings as well would give you two copies of each.

**A backup taken long ago will restore fewer recordings than it holds.** Recordings are pinned to absolute dates. If the backup is older than the recordings in it, those slots have since passed, and the restore skips them rather than failing. Nothing is wrong; the recordings simply are not upcoming any more.

---

## What a backup does not contain

- **Live stream content**: a backup captures *definitions* (which streams are assigned to which channels), not the streams themselves.
- **The SQLite WAL file**: ECM checkpoints the write-ahead log before building the artifact, so `journal.db` in the archive is self-contained, but the WAL itself is not included.
- **Dispatcharr's own database**: ECM backs up the configuration it manages. Dispatcharr's internal database (viewer history, its own task state, etc.) is outside ECM's scope.
- **Recognized provider credential fields and credential-bearing URL values, in a standard backup**: ECM replaces structured password and username fields and URLs whose userinfo or query parameters carry credentials. It cannot recognize an arbitrary secret typed into a free-text name or note. See [What a standard backup does not carry](#what-a-standard-backup-does-not-carry) for the complete rules, and [Credentials and passphrase encryption](#credentials-and-passphrase-encryption) for the migration path that preserves the structured credentials.

---

## What a standard backup does not carry

A **standard** backup is the default artifact: unencrypted, and the one the `dbas_backup` task produces on a schedule. It is built to replace recognized structured credentials and remove account data before the bytes reach the archive. That processing is not optional and there is no switch that turns it off. Operator-authored configuration is kept, so this is not a promise that every remaining value is suitable for public disclosure.

Three rules do the work, and it takes all three because none of them alone is complete:

1. **Credential-class field names.** Any field named like a secret (`password`, `api_key`, `smtp_password`, `plex_token`, `emby_api_key`, `jellyfin_api_key`, `telegram_bot_token`, and the rest of the same class) becomes `***REDACTED***`.
2. **Provider identity, not just provider secrets.** `username` is removed too, everywhere it appears: on the M3U account row, inside `profiles[].custom_properties.user_info`, on EPG sources, and in core settings. For an Xtream Codes provider the username is half the credential pair and the half that names your subscription, so a backup that kept it was not a redacted backup. The one deliberate exception is the **Dispatcharr users** category, which lists your own Dispatcharr accounts rather than a third party's. The restore creates each of those accounts by username and checks for collisions on it, so replacing it would break the restore rather than protect anything.
3. **Credentials hidden inside URLs.** A URL that carries a credential in its userinfo (`https://<username>:<password>@host/...`) or in a query parameter (`get.php?username=...&password=...`) is caught by its *value*, not by the name of the field holding it. A URL that carries no credential is left alone, because the restore needs the address.

On top of that, the copy of ECM's own database (`journal.db`) inside the artifact is reduced to a fixed list of tables that hold configuration a restore needs. Everything else is dropped outright rather than filtered. So a standard artifact carries **none** of the following:

- **Your ECM accounts.** Usernames, email addresses, password hashes, live sessions, password-reset tokens, and any linked OIDC / SAML / LDAP identity.
- **Your journal and history.** Journal entries, notifications, task run history, Channel Pipeline execution history and snapshots, M3U change logs and snapshots.
- **Telemetry.** Session telemetry (which includes Emby, Plex, Jellyfin and Dispatcharr account names), unique client connections (viewer IP addresses and usernames), bandwidth and popularity data.
- **Notification and storage targets that hold credentials.** Cloud storage targets and sync targets are dropped whole, even though their stored credentials are already encrypted at rest.
- **Personal data belonging to other people.** M3U digest settings are dropped because they hold an email recipient list.

Alert methods themselves are kept, because they are configuration you authored, but the credential and identity values inside them (SMTP username and password, Telegram bot token and chat ID, webhook URLs, and ntfy topic and access token) are removed.

A standard backup exports no credential-verifiable marker for an ntfy
destination. When a Full Backup restore replaces `journal.db`, an authenticated
ntfy target therefore requires the operator to re-enter both its topic and
token. A same-instance unauthenticated target may retain only its local topic,
and only when the restored row has the same ID, method type, and server and the
local target has no token. These checks prevent an unrelated restored row from
inheriting a local ntfy publishing capability.

!!! warning "A backup now fails rather than shipping an unscrubbed database"
    If ECM cannot open, read, or rewrite its copy of `journal.db` while removing this data, the whole backup **fails** and no artifact is written. That is deliberate. The alternative, which is what earlier builds did, was to fall back to shipping the database as-is behind a successful-looking result. A failed backup is a problem to investigate; an artifact you believed was redacted and was not is worse. See [If a backup fails while removing sensitive data](take-a-backup.md#if-a-backup-fails-while-removing-sensitive-data).

The **Full Backup (legacy `.zip`)** format follows the same minimum
confidentiality boundary. Its `journal.db` carries the same fixed configuration
table list, and its `settings.json` masks credential-class fields. ECM also
omits the complete `tls/` and `m3u_uploads/` trees: the former can contain TLS
private keys and the latter can contain provider credentials inside playlist
URLs. Logos remain included. **A full backup is therefore not a copy of your TLS
certificate.** Copy `/config/tls` yourself, or plan to reissue the certificate,
before you rebuild a host. A `.zip` taken by an earlier build still contains
those trees and is still restored in full, so nothing you already hold loses
material, and restoring a current `.zip` leaves an existing `tls/` directory
untouched rather than clearing it.

One thing the legacy `settings.json` still keeps, and it is the reason this
format is not the one to hand out. It masks the credential-class fields by name,
so it keeps your Dispatcharr **username** beside the removed password. A
credential embedded inside a URL *value* is no longer among them: `settings.json`
now runs the same value-aware URL scrubber the DBAS formats use, so any URL
setting carrying a credential (the Dispatcharr address, but equally an Emby,
Jellyfin or Plex base URL) is replaced by the redaction marker. Restoring that
backup keeps whatever address the destination already has, so a same-instance
restore loses nothing; on a fresh install you re-enter the address once.

The **Full Backup card** on Settings warns that the file is plaintext, is not a
redacted artifact, and should be treated as a secret. That warning is the
accurate description of this format. Use a standard DBAS backup when you need the
credential-redacted format, and inspect operator-authored free text before
sharing either plaintext format. That last instruction is doing real work: a
credential written into free text, where ECM has no name or shape rule that could
recognize it, still travels verbatim. The clearest example is an Xtream Codes
URL, whose credential sits in the path (`/live/<user>/<pass>/<id>.ts`) rather
than in the userinfo or the query string. ECM does not guess at path segments,
because a wrong guess would destroy an address a restore needs, so such a URL
typed into a free-text configuration field comes back in the archive exactly as
you entered it.

### Confidentiality policy by artifact class

| Artifact | Persisted or off-host policy | Recovery/key strategy |
|-|-|-|
| Standard DBAS ZIP (manual or scheduled) | Plaintext, structurally redacted; omits ECM accounts, password/session/reset hashes, storage credentials, TLS private keys, and raw uploaded playlists. Local ZIP and checksum sidecar are `0600`. | No key is required. Restore configuration, then re-enter destination credentials or keep those already configured. Run a dry-run preview before apply. |
| Encrypted DBAS migration ZIP | Authenticated whole-artifact encryption; may include credentials only with the explicit **Include credentials** option. Local ZIP and checksum sidecar are `0600`. | Manual-only passphrase stored by the operator in a password manager. Verify by decrypting through a restore preview before relying on it. |
| Legacy full ZIP | Plaintext and redacted only at its legacy seams; omits ECM account state, TLS files, and uploaded playlists, and scrubs credential-bearing URL values, but keeps the Dispatcharr username in `settings.json` and any credential written into operator-authored free text. Treat it as a secret. A saved local copy is `0600`. | No key is required. TLS material, uploaded playlists, and destination credentials are re-established after restore. |
| YAML export (downloaded or scheduled) | Plaintext structured export with the shared credential redactor; it contains no file trees or ECM authentication database. A scheduled local file is `0600`. | No key is required; re-enter redacted values after restore. |

### What this means in practice

A standard backup replaces recognized provider and notification credential fields and credential-bearing URL values. It removes ECM login accounts and viewer history. Operator-authored free text, such as source names and rule notes, may still travel verbatim and could contain a secret ECM cannot recognize; credential-free provider addresses also remain. Inspect that content before attaching the artifact to a support ticket, posting it in a forum, or copying it to a machine you do not control.

Two consequences follow, and both are expected behaviour rather than faults. Both concern `journal.db`, so they apply to a **Full Backup (legacy `.zip`)** restore and to the first-run "restore from backup" path. The **Restore DBAS Backup** flow never writes `journal.db` at all, so it does not touch your ECM accounts in either direction.

- **Restoring onto an instance with no accounts leaves you at first-run setup**, because the artifact carries no accounts to restore. Create your admin account, then sign in. To carry accounts between instances instead, use an encrypted backup with **Include credentials** enabled.
- **Restoring onto an instance that already has accounts leaves those accounts alone.** ECM snapshots the destination's own accounts before it replaces the database and puts them back afterwards, so restoring a backup never signs you out of the instance you are restoring.

The restore response carries a **notice** for each of these when it applies, and ECM shows it after the restore finishes. The notices are read from your instance after the restore rather than predicted from the artifact, so they name only what this instance actually lost. If you never configured a cloud storage target, you are not told to go re-establish one.

---

## The artifact format

Each backup is a `.zip` file containing:

- `manifest.json`: a cleartext header with `schema_version`, `app_version`, creation timestamp, and a per-member SHA-256 hash list.
- `categories/<name>.yaml`: one YAML file per configuration category.
- `journal.db`: a reduced copy of the ECM SQLite database. In a standard backup it carries only the fixed list of configuration tables described in [What a standard backup does not carry](#what-a-standard-backup-does-not-carry); everything else is dropped, and the file is compacted so the dropped rows are not recoverable from it.
- `binary/logos/<file>`: per-image logo files, streamed one at a time.
- `binary/metadata.json`: logo inventory.
- `binary/url-mappings.json`: logo filename to source-URL map.

A `.sha256` sidecar file is written alongside the ZIP, containing the SHA-256 of the whole artifact. ECM verifies this hash before any restore begins.

### Where an uploaded logo's bytes actually live

A logo uploaded through ECM's Logo Manager (`POST /api/channels/logos/upload`) is written to **Dispatcharr's** `/data/logos/<filename>` inside the Dispatcharr container, not to ECM's own `/config/uploads/logos/`. ECM's own upload directory stays empty even when uploaded logos exist. Only uploaded logos get their bytes into the artifact's `binary/` subtree: for a source with one uploaded logo alongside ten remote-URL logos, `binary/metadata.json` records `logo_count: 1`. Remote CDN logos referenced by a URL are restored from that URL at restore time and are never fetched into the artifact.

This matters for operators: a logo uploaded through ECM will not be found anywhere under ECM's own config volume. Look in Dispatcharr's `/data/logos/` instead, or restore the backup to get it back.

### Schema version and forward compatibility

The `manifest.json` contains a `schema_version` integer (distinct from the ECM app version string). A restore that receives an artifact whose `schema_version` is newer than the running ECM build refuses with "Unsupported backup version" and does not attempt a partial restore of an incompatible artifact.

When restoring a backup produced by an older ECM onto a newer ECM, the schema version is accepted (older ≤ current = accepted). This means backups are forward-compatible: an artifact from ECM v0.18.0 can be restored onto a later ECM build.

### Restore a new backup with this ECM build or a newer one, never an older one

!!! danger "Read this before you roll ECM back, or restore onto an older instance"
    The full-redaction change described above did **not** move `schema_version`, because the artifact's structure is unchanged. Nothing therefore refuses the combination below, and you have to avoid it yourself.

    **A backup taken by an ECM build that has full redaction should only be restored by that build or a newer one.** Restoring one with an older ECM has three known problems, none of which can be fixed retroactively, and all three arrive behind an apparently successful restore:

    1. **You can be signed out of your own instance.** Older ECM has no step that preserves the destination's accounts across a restore, and a new artifact carries none of its own. Restoring a new artifact onto an older instance that *has* accounts can therefore leave it with none, dropping you at first-run setup on an instance you were administering.
    2. **Alert-method usernames and chat IDs are written as the literal text `***REDACTED***`.** Older ECM restores the password half of an alert method's configuration correctly but does not know the identity half was ever removed, so it writes the placeholder in as if it were the value.
    3. **An alert method whose configuration could not be read at backup time is left as the literal text `***REDACTED***` in its entirety**, and stops sending notifications until you reconfigure it. Newer ECM normally preserves the destination's configuration instead. For ntfy, a standard backup redacts the topic and token, so an authenticated target requires both values to be re-entered. Only a same-instance unauthenticated target may retain its local topic, and only when the restored row has the same ID, method type, and server and the local target has no token. An encrypted backup with **Include credentials** preserves the ntfy topic and token.

    If you need to roll ECM back, roll back to a backup taken **before** the upgrade rather than restoring a newer artifact onto the older build.

---

## Credentials and passphrase encryption

By default, backups apply all three structured redaction rules: recognized credential fields, provider-identity fields such as usernames, and credential-bearing URL values are replaced in a standard artifact. See [What a standard backup does not carry](#what-a-standard-backup-does-not-carry) for the complete rules and the free-text limitation. A restore from this artifact re-uses whatever credentials are already configured on the destination, or leaves the credential unset on a fresh install. It never writes the sentinel into a credential field, and the restore report names each field that needs re-entering.

If you are migrating to a new install and want credentials to travel with the backup, use the **Encrypted Backup** option:

1. In **Settings → Backup & Restore**, open the **Encrypted Backup** card.
2. Check the **"I understand a lost passphrase makes this artifact permanently unrecoverable"** acknowledgement.
3. Set a passphrase of at least 12 characters. The passphrase is never stored, so keep it somewhere safe.
4. Enable **Include credentials** to carry M3U/EPG passwords and alert-method credentials, including ntfy topics and access tokens, inside the encrypted artifact.

**A passphrase alone does not preserve the structured credentials.** The two settings are separate: encryption protects the artifact, and **Include credentials** is what preserves the recognized credential fields and credential-bearing URL values. An encrypted backup taken *without* **Include credentials** applies the same structured redaction rules as a standard one. With it enabled, the artifact carries everything a standard one removes, ECM's own accounts included, which is what makes it the migration path and also what makes it a file to guard.

An encrypted backup uses scrypt (N=2¹⁵, r=8, p=1) for key derivation and ChaCha20-Poly1305 for authenticated encryption, applied as a chunked streaming pass over the whole artifact. Before scrypt runs, ECM requires the exact supported envelope version, KDF and AEAD identifiers and validates fixed bounds for the KDF parameters, salt, nonce, and chunk size. A malformed header is rejected without performing attacker-selected KDF work. The passphrase is never logged or stored.

> **Warning: lost passphrase = permanently unrecoverable artifact.** There is no recovery path. Store your passphrase in a password manager before taking an encrypted backup.

Encrypted backups are manual-only, because a passphrase is never persisted in the task schedule store.

---

## Retention model

ECM automatically prunes old local backups (and old off-host copies at each configured cloud destination) after each verified-successful backup run. The default policy is:

- **Keep the newest 7 backups**, regardless of age.
- **Additionally prune** any backup beyond the newest 7 that is older than 30 days.

The newest-N floor is always respected: even if a backup is older than 30 days, it is kept if it is within the newest 7. A failed or partial backup run never prunes anything. Retention only runs when a verified-successful new backup has been written.

---

## User restore semantics

!!! note "These are Dispatcharr's accounts, not ECM's"
    The **Users** category holds your **Dispatcharr** user accounts. Usernames are kept in a standard backup for this category alone, because the restore creates each account by username and checks for collisions on it. Your **ECM** accounts are a different thing entirely and are never in a standard backup; see [What a standard backup does not carry](#what-a-standard-backup-does-not-carry).

Restoring user accounts is **opt-in**. Users are not selected by default in the restore modal. When you do restore users:

- The **current admin account** is never overwritten. ECM identifies the account its own Dispatcharr credentials authenticate as and skips it, so you cannot lock yourself out via a restore.
- A user account that already exists on the destination with the same username is **skipped, not updated**. An existing account is never overwritten by a restore.
- **No password travels with the backup.** Dispatcharr never hands out a password or a hash to begin with, so a restored account is created with a random password that ECM discards immediately and is unusable until you set one yourself. The restore report flags each restored account as needing that reset.
- **Every restored account is created without privileges**, whatever the backup claims. Superuser, staff, and user-level flags are not carried. Re-grant them by hand if you need them.

See [Restore a backup](restore-a-backup.md) for the full restore flow.

---

## Recommended backup cadence

- **Daily scheduled backup**: sufficient for most operators. Configure a `dbas_backup` task schedule in **Settings → Scheduled Tasks**.
- **Before any major change**: take a manual backup before reconfiguring M3U sources, bulk-editing channels, or running a major Channel Pipeline rule change.
- **Before a restore**: always take a fresh backup of the current state before restoring an older artifact, so you can roll back if the restore does not produce the result you expected.

---

## Going deeper

- [Take a backup](take-a-backup.md): step-by-step backup workflow.
- [Verify a backup](verify-a-backup.md): dry-run preview before committing.
- [Restore a backup](restore-a-backup.md): full restore flow with safety semantics.
- [Configure cloud destinations](configure-cloud-destinations.md): off-host storage for durability.
- [Migrate to a new install](migrate-to-a-new-install.md): end-to-end migration walkthrough.
- [`docs/security/threat_model_dbas_import.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/security/threat_model_dbas_import.md): security context for import and restore, for operators evaluating the trust boundary.
