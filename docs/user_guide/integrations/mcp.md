# MCP Integration: Claude AI Connection Reference

> **Status:** MCP authenticates with a static API key in the
> `Authorization: Bearer` header. Credentials in URLs are rejected.

This is the full operator reference for connecting Claude to ECM via the Model
Context Protocol. The [README MCP section](https://github.com/MotWakorb/enhancedchannelmanager#mcp-server-claude-integration)
covers quick-start setup; come here when you need the step-by-step setup, key
rotation details, or troubleshooting.

## What the MCP key is, and what it cannot do

ECM's MCP integration uses **three separate credentials**. Only the first is
yours to handle:

| Credential | Who holds it | What it is for |
|---|---|---|
| `mcp_api_key` | You, and every MCP client you configure | Authenticates a client to the MCP sidecar. This is the key the Settings page generates. |
| Sidecar backend key | ECM and the sidecar only | Authenticates the sidecar to the ECM backend. Generated automatically, stored owner-only, never displayed. |
| Confirmation key | ECM and the sidecar only | Signs the short-lived tokens that authorize destructive tool calls. Also never displayed. |

The last two are created and rotated by ECM itself. You never copy them into a
client, and `mcp_api_key` is **not** sent to the backend: the backend refuses
it outright.

!!! info "The MCP key is a limited service credential, not an administrator"
    Authenticating with the MCP key does not grant administrator authority.
    Backend capabilities are deny-by-default: routes the sidecar has no explicit
    verdict for are refused, and a defined set is reserved to a signed-in human
    administrator and answers the MCP key with `403`:

    - taking, listing, downloading, deleting or restoring backups;
    - TLS certificate and private-key lifecycle, and the security settings blob;
    - user, identity and authorization administration, including password change;
    - generating or revoking the MCP API key itself;
    - creating, changing, deleting or **testing** outbound destinations
      (cloud-storage targets, sync targets, alert methods), and changing M3U or
      EPG source credentials;
    - running the Channel Pipeline in one shot (`POST /api/channel-pipeline/run`).
      MCP reaches pipeline execution only through the mutation-free
      prepare/commit pair described below.

    The list above is enforced by `MCP_HUMAN_ONLY_ROUTES` in
    `backend/auth/mcp_capabilities.py`.

!!! warning "This list holds while ECM requires authentication"
    The capability matrix is applied inside the backend's
    `require_auth and setup_complete` branch, and the gates behind most of the
    list (`RequireAdminIfEnabled`, `RequireHumanAdminIfEnabled`) allow the call
    through when authentication is off. **With Settings → Authentication →
    Require Authentication disabled (a supported mode), most of the list above
    is reachable by anyone who can open a socket to ECM**, with or without a key.
    `docs/auth_middleware.md` → "What `require_auth: false` permits" is the
    authority on that mode and enumerates it; read it before turning
    authentication off.

    Four things hold in **every** mode, auth-disabled included:

    - **The operator-facing `mcp_api_key` is never a backend credential.** ECM
      refuses it with `403` before any route runs, so a stolen key cannot be
      replayed straight at `/api/*` in any mode. This one has no preconditions:
      the refusal happens before the `require_auth` branch is even consulted.
    - **MCP-key rotation/revocation and the TLS certificate and private-key
      lifecycle stay human-admin-only.** Those two gates carry
      `enforce_when_auth_disabled=True`, so they keep enforcing once the
      instance has an operator identity.
    - **Outbound connection tests stay human-admin-only.** *Testing* a
      cloud-storage target, sync target or alert method is gated by
      `RequireHumanAdminForOutboundTest`, which also carries
      `enforce_when_auth_disabled=True`, again once the instance has an
      operator identity. These are the verbs that make ECM spend a stored
      credential against a host the caller names and report the upstream
      verdict back. Note that *creating, changing and deleting* those same
      destinations is **not** in this group; see below.
    - **Anonymous administrator administration stays blocked.**
      `/api/auth/admin/*` chains `get_current_user`, which rejects a request
      carrying no token in every mode, so a caller off the network cannot create
      an administrator. This is narrower than it may read: `require_admin`
      checks `is_admin` alone, and with authentication disabled the capability
      matrix that normally keeps the sidecar's *private* backend key out of
      these routes is not applied. The guarantee is against the
      operator-facing `mcp_api_key` (refused outright, first bullet) and against
      anonymous callers. It is not a guarantee against every credential ECM
      issues.

    A stolen MCP key is serious in every mode: it can read and modify your
    channel, stream, EPG and pipeline configuration, and it can read your
    household's viewing history (below). While authentication is required it
    additionally cannot exfiltrate a backup, and it cannot create or change an
    outbound destination. With authentication disabled, those two limits stop
    applying to anyone on your network. The credential-oracle limit does not
    follow them: running a connection test against a stored credential stays
    administrator-only in both modes.

!!! danger "What a leaked MCP key exposes, and what to do about it"
    **The viewing history is the most sensitive thing the key reaches.** The MCP
    principal is allowed `GET /api/stats/watch-history`, which returns
    per-connection rows carrying an `ip_address` the caller can also filter by.
    It is also allowed `/api/stats/unique-viewers`,
    `/unique-viewers-by-channel`, `/top-watched`,
    the per-user Dispatcharr and Emby stats routes, and `GET /api/journal`.
    Together that is who in your household watched what, from which device, and
    when. Rotating the key does not un-read any of it.

    **Three properties of the key make a leak worse than it looks:**

    - It **never expires**. It is valid until you rotate or revoke it.
    - It is **one key shared by every client you configure**. There is no
      per-client revocation: rotating to cut off one client cuts off all of
      them, and you must re-enter the new key everywhere.
    - A leak is **not detectable from ECM**. The sidecar's success log line
      records only `auth_method=static_key`; no client IP, no client identity.
      You cannot tell two clients apart, or a client from an attacker.

    **If you believe the key leaked**, rotation is the first step, not the last:

    1. Rotate the key (below) and re-enter it in every configured client.
    2. Read `GET /api/journal` for the exposure window to see what was changed.
    3. List your saved backups and delete any the attacker may have created.
       An artifact taken with **Include credentials** is a credential file.
    4. Re-verify channel, stream, EPG and Channel Pipeline configuration against
       what you expect; a modification made with the key looks like your own.
    5. If authentication was disabled during the window, treat everything in
       `docs/auth_middleware.md` → "What `require_auth: false` permits" as
       reachable too, not just the MCP surface.

Destructive MCP tools additionally require two calls. The first is
mutation-free: it resolves and shows you exactly what would be affected and
returns a token bound to that content. The token expires after 5 minutes, is
single-use, and is invalidated if the arguments or the resolved targets drift.
An agent cannot delete anything in one step.

!!! danger "Enabling TLS in ECM does not protect MCP"
    ECM's **Settings → TLS** feature terminates HTTPS for ECM's own web
    interface on `ECM_HTTPS_PORT` (default `6143`). It does **not** wrap the MCP
    sidecar, which is a separate process listening on its own port (`6101`) with
    no TLS of its own. Turning ECM's TLS on changes nothing about MCP traffic.

    Because `mcp_api_key` travels in an `Authorization: Bearer` header on every
    request, an unencrypted network hop exposes it. The Compose default
    publishes MCP on loopback only, which is why it is safe without TLS. For any
    client on another machine, put an HTTPS reverse proxy in front of port 6101
    and use the remote overlay below. That proxy, not ECM's TLS setting, is
    what encrypts MCP.

## Published image security

The ECM backend and MCP sidecar must use the same `PUID` and `PGID` values
(default `1000:1000`). The backend stores its private sidecar credential with
owner-only permissions, and the sidecar fails readiness rather than reading a
credential owned by a different account. Set `PUID` and `PGID` once in the
Compose environment so both services inherit the same identity.

The published MCP container uses a reviewed, digest-pinned Alpine base. Each
architecture-specific image is scanned before the multi-architecture manifest
is created. Any Critical or High operating-system or Python-library finding
blocks publication, including findings for which the upstream distributor has
not yet published a fix. ECM does not use a blanket `ignore-unfixed` setting or
an MCP vulnerability waiver list. If either architecture fails its scan, keep
the previously published MCP image in service and wait for a remediated build;
do not assemble or publish the manifest manually.

---

## Where the MCP credentials live

ECM publishes MCP credential material into one dedicated directory, set by the
`MCP_SECRETS_DIR` environment variable on the **ecm** service. It normally holds
two persistent files, both written by ECM with mode `0600` and owned by
`PUID`:`PGID`, plus a transient recovery record during public-key transitions:

| File | Contents |
|-|-|
| `api-key` | The public client key you paste into Claude Desktop or Claude Code. |
| `mcp-service.json` | The sidecar's private backend principal key and its separate destructive-confirmation signing key. Never disclosed. |
| `.api-key.recovery` | Owner-only redo record for an interrupted rotation or revocation. `prepared` is startup-inert; `recovery-active` reapplies the already-active new value. Do not edit or delete it while resolving a storage error. |

`api-key` is the single authority for the public key. The `mcp_api_key` field in
`settings.json` is only a compatibility mirror used by older clients and legacy
migration. Generic settings saves ignore a submitted mirror value and copy the
validated `api-key` value back instead. An empty `api-key` is intentional: it is
the durable revocation state, not a missing credential.

The MCP Compose overlay and the published-image recipe in the README both set
`MCP_SECRETS_DIR=/run/secrets/ecm-mcp` and mount a dedicated `ecm-mcp-secrets`
volume there: read-write for ECM, read-only for the sidecar. The sidecar
mounts nothing else, so it cannot read `settings.json`, `auth_settings.json`,
the audit journal, TLS private keys, or backups.

On a fresh supported Compose deployment, ECM provisions the public client key
and both private credentials before the sidecar becomes ready. On upgrade it
republishes the persisted public key into a newly added projection volume
without changing it. The generated public value is never logged or returned by
health checks; use **Regenerate Key** once when configuring a client so ECM can
display the replacement value to you. A key you explicitly revoked remains
revoked across restarts and keeps MCP health fail-closed until you regenerate it.

**Without the overlay, `MCP_SECRETS_DIR` defaults to `CONFIG_DIR`.** That is
deliberate: it keeps a newer ECM backend working under an older Compose file
whose sidecar still reads `/config`. The consequence is that every deployment
writes both files, and on a default deployment they land at
`/config/api-key` and `/config/mcp-service.json`. **If you bind-mount a host
directory at `/config`, `api-key` is a credential file sitting at its top
level.** Exclude it from any host-side backup or sync that sweeps the whole
directory, or move the projection somewhere else by setting `MCP_SECRETS_DIR`
on the ecm service and mounting that path.

If you do move it, `MCP_SECRETS_DIR` must name an absolute path that is a real
directory: not a relative path, not a symbolic link, and not a system directory
such as `/`, `/etc`, `/usr` or `/var`. The container entrypoint chowns and
chmods that path as root before it drops privileges, and both operations follow
a symbolic link to its target, so a link there would re-own whatever it points
at. ECM refuses those shapes during preflight with a named error instead of
acting on them.

### After upgrading from v0.18.1 build 0123 or earlier

Earlier builds kept `mcp-service.json` in `CONFIG_DIR`. Moving the projection
does not remove the old copy, so `<CONFIG_DIR>/mcp-service.json` is left
behind. It authenticates nothing, because the backend reads only the file
under `MCP_SECRETS_DIR`. It is still secret material that a backup tool will
capture. ECM logs a warning naming the file on every start and does **not**
delete it, because deleting credential material on your behalf is
irreversible. Delete it yourself once you have confirmed MCP is working:

```bash
docker compose exec ecm rm -f /config/mcp-service.json
```

---

## Choose your connection method

ECM's MCP server is authenticated with a static API key (`mcp_api_key`) in an
`Authorization: Bearer` header. The Compose default publishes port 6101 only on
the Docker host's loopback interface. Remote clients require the explicit
remote overlay and an HTTPS reverse proxy.

When connecting through a LAN hostname or IP, add that exact value to the MCP
container's comma-separated `MCP_ALLOWED_HOSTS` environment variable and
recreate the container. The built-in allowlist covers `localhost`, loopback
IPs, and the canonical `ecm-mcp` Compose service name. Entries are hostnames or
IPs only. Do not include `http://`, a port, a path, or `*`.

### Remote access requires HTTPS

The default `docker-compose.mcp.yml` publishes MCP as
`127.0.0.1:6101`. For a client on another machine, terminate TLS in Caddy,
nginx, or Traefik and start the explicit remote overlay:

```bash
MCP_ALLOWED_HOSTS=mcp.example.home \
MCP_ALLOWED_ORIGINS=https://mcp.example.home \
MCP_TRUSTED_PROXY_IPS=172.20.0.10 \
docker compose -f docker-compose.yml -f docker-compose.mcp.yml \
  -f docker-compose.mcp.remote.yml up -d
```

Replace the example values with the public hostname and the proxy's exact
source IP or CIDR. The overlay rejects non-HTTPS `/mcp` traffic and Uvicorn
trusts forwarded scheme information only from that proxy. Keep port 6101
blocked at the router/firewall; clients connect to the proxy's HTTPS URL.

| | mcp-remote bridge (Claude Desktop) | Claude Code (`.mcp.json`) |
|---|---|---|
| **Best for** | Claude Desktop users; private/homelab; existing setups | Claude Code in any project |
| **Auth model** | Bearer header populated from a local environment variable | Bearer header populated from a local environment variable |
| **Prerequisites** | Node.js LTS 18+ on the Claude Desktop machine | None. Claude Code speaks Streamable HTTP natively |
| **Config file** | `claude_desktop_config.json` | `.mcp.json` in your project directory |
| **Network** | Private OK: bridge runs on your machine, connects over LAN/VPN | Private OK: Claude Code connects directly from your machine |

- **Using Claude Desktop?** See [mcp-remote bridge](#claude-desktop-mcp-remote-bridge-node-required).
- **Using Claude Code?** See [Claude Code](#claude-code-mcpjson).

---

## Claude Desktop: mcp-remote bridge (Node required)

### What this path is

Claude Desktop speaks stdio for locally-configured MCP servers, so a remote
HTTP server needs a local bridge. The `mcp-remote` npm package is that bridge:
Claude Desktop runs it via `npx`, it connects to ECM's Streamable HTTP MCP
endpoint and presents a standard MCP interface back to Claude Desktop. The
static key is supplied through an environment-backed header, never the URL.

> **Why not use Settings → Connectors → Add custom connector?**
> Claude Desktop's built-in "Connectors" UI requires OAuth 2.1 per the MCP
> spec. ECM does not support OAuth (that offering was retired). Do
> not use the Connectors path; it will not work. The `mcp-remote` bridge below
> is the supported path.

### Prerequisites

Node.js LTS 18+ must be installed on the same machine as Claude Desktop and on
`PATH`. Without it, Claude Desktop's logs show `spawn npx ENOENT`.

Install Node from [nodejs.org](https://nodejs.org/), or via a package manager:

| Platform | Command |
|---|---|
| macOS | `brew install node` |
| Windows | `winget install OpenJS.NodeJS.LTS` |
| Debian/Ubuntu | `apt install nodejs npm` |

Verify after install: `node --version` should print `v18.x.x` or higher.

### Step 1: Generate a client-visible MCP API key in ECM

1. Open ECM in your browser.
2. Go to **Settings → MCP Integration**.
3. Click **Generate Key** or **Regenerate Key**. A Compose deployment may have
   provisioned an undisclosed key already so the sidecar can become ready.
4. Copy the key immediately. It is displayed once. If you miss it, use
   **Regenerate Key** to issue a new one (the old key is invalidated).

> This is your `mcp_api_key`. It is **not** your Dispatcharr API key. Mixing
> them up breaks both: Dispatcharr returns 401, and the MCP container reports
> "API key not configured." See the
> [README field reference](https://github.com/MotWakorb/enhancedchannelmanager#mcp-server-claude-integration)
> for the distinction.

The Settings → MCP Integration panel has a **copy button** for a credential-free
config template. It never places the key in a generated config or URL.

### Step 2: Open the Claude Desktop config file

1. Open Claude Desktop.
2. On **macOS**: click the **Claude** menu (top-left) → **Settings**.
   On **Windows**: click the hamburger/menu icon → **Settings**.
   *(Label may vary across Claude Desktop versions; look for the gear icon or
   Settings entry.)*
3. Click the **Developer** tab.
4. Click **Edit Config**. This opens `claude_desktop_config.json` in your
   default text editor and reveals the file in Finder (macOS) or Explorer
   (Windows).

Config file locations for reference:

| Platform | Path |
|---|---|
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |

If the file doesn't exist yet, create it at that path. Claude Desktop
creates a blank one when you click Edit Config.

### Step 3: Add the ECM server block

Set the operating-system environment variable `ECM_MCP_AUTH` to `Bearer
<your key>` before launching Claude Desktop, then paste this block:

```json
{
  "mcpServers": {
    "ecm": {
      "command": "npx",
      "args": [
        "mcp-remote",
        "http://localhost:6101/mcp",
        "--header",
        "Authorization:${ECM_MCP_AUTH}",
        "--allow-http"
      ]
    }
  }
}
```

Notes:
- `--allow-http` is allowed only for the loopback endpoint above. For remote
  use, replace the URL with your reverse proxy's `https://` URL and omit it.
- If you already have other entries in `"mcpServers"`, add the `"ecm"` block
  alongside them. Do not replace the whole file.
- Do not put the key in `args`; process arguments are visible to other local
  processes on many operating systems.

Save the file.

### Step 4: Fully quit and reopen Claude Desktop

Closing the Claude Desktop window is not enough. The bridge process only
starts at launch. Fully quit the app:

- **macOS**: Claude menu → **Quit Claude** (or Cmd+Q).
- **Windows**: right-click the system tray icon → **Quit**, or use
  Task Manager to end the process.

Then reopen Claude Desktop.

### Step 5: Confirm the tools loaded

After restart, look for the tools/MCP indicator in the chat input bar. It
typically appears as a slider icon or a **"search and tools"** control
(label varies by Claude Desktop version). Click it. The connected `ecm`
server and its tools should be listed there.

If the `ecm` server does not appear, or shows an error, check
Settings → Developer. Claude Desktop surfaces bridge errors there.

**First test prompt:** ask Claude "List my ECM channels." A valid response
(a channel list or a message that no channels exist) confirms the bridge is
working end-to-end.

### Key rotation

The Bearer header uses the key from the authoritative `api-key` sidecar. The
same value is mirrored into `settings.json:mcp_api_key` for compatibility.

To rotate the static key:

1. In ECM: Settings → MCP Integration → **Regenerate Key**.
2. Copy the new key.
3. Update the local `ECM_MCP_AUTH` environment variable.
4. Fully quit and reopen Claude Desktop (Step 4 above).

After upgrading from a build that used Starlette 1.0.0 or earlier, rotate the
key even if MCP was intended to be private. The old key must be treated as
potentially exposed. Regeneration invalidates it immediately; update every MCP
client with the new value afterward. The sidecar re-reads the key on each
request, so it does not need a restart for the credential change itself.

Rotation is a **human administrator** action in the ECM UI. `POST`/`DELETE` on
`/api/settings/mcp-api-key` are reserved routes: a caller presenting the MCP key
gets `403`, so a compromised key cannot rotate or revoke itself, and neither can
an agent acting through MCP.

Rotating `mcp_api_key` replaces only the client-facing credential. The sidecar's
private backend and confirmation keys are separate, are never exposed to a
client, and are not affected. There is nothing for you to update for them.

Do **not** edit `dispatcharr_api_key` (or its legacy `api_key` alias). That is
the Dispatcharr REST API token and is separate from MCP auth.

If you hit `401 Invalid API key` after rotation, see
[Troubleshooting: 401 Invalid API key](#401-invalid-api-key-on-tool-calls).

---

## Claude Code (`.mcp.json`)

### What this path is

Claude Code speaks Streamable HTTP directly: no bridge, no Node.js required.
You register ECM as an MCP server either via the CLI (`claude mcp add`) or by
placing a `.mcp.json` file in your project directory. Both methods result in the
same connection; pick whichever fits your workflow.

### Step 1: Generate your MCP API key in ECM

Same as the Claude Desktop path: **Settings → MCP Integration → Generate Key**.
Copy the key immediately. The Settings → MCP Integration panel has a copy button
for the pre-filled config block with your host and key already substituted.

If you already completed this step for Claude Desktop, reuse the same
`mcp_api_key`. There is one key for both clients.

### Step 2: Register ECM with Claude Code

Choose one method:

---

#### Method A: CLI (`claude mcp add`)

Run this command from the Docker host after setting `ECM_MCP_API_KEY`:

```bash
claude mcp add --transport http --header 'Authorization: Bearer ${ECM_MCP_API_KEY}' ecm "http://localhost:6101/mcp"
```

By default this uses `--scope local`, which registers the server for your
current project directory only (stored in `.claude/mcp.json`, not committed).
You can pass a different scope:

| Scope | Flag | Effect |
|---|---|---|
| `local` | `--scope local` (default) | Just you, in this project directory |
| `project` | `--scope project` | Shared with everyone who checks out this repo (stored in `.mcp.json` at the project root: commit it) |
| `user` | `--scope user` | All your projects on this machine |

Example: register for all your projects:
```bash
claude mcp add --transport http --scope user --header 'Authorization: Bearer ${ECM_MCP_API_KEY}' ecm "http://localhost:6101/mcp"
```

> **Security note:** keep the single quotes shown above. They pass the variable
> reference, not its value, so the key does not enter shell history or process
> arguments. The `.mcp.json` method below uses the same reference.

---

#### Method B: `.mcp.json` file

Create (or edit) `.mcp.json` in your project root:

```json
{
  "mcpServers": {
    "ecm": {
      "type": "http",
      "url": "http://localhost:6101/mcp",
      "headers": {
        "Authorization": "Bearer ${ECM_MCP_API_KEY}"
      }
    }
  }
}
```

Set `ECM_MCP_API_KEY` in the environment that launches Claude Code. For remote
access, replace the URL with the HTTPS reverse-proxy URL.

This is equivalent to `--scope project` from the CLI: Claude Code auto-detects
`.mcp.json` at startup when it is in the working directory.

Host notes:
- Same machine as Claude Code: use `localhost`.
- MCP container on the same Docker network: use the service name (`ecm-mcp`).
- Remote/homelab ECM: use the hostname or LAN IP.

---

### Step 3: Verify the connection

Start (or restart) Claude Code in the project directory, then run the `/mcp`
slash command:

```
/mcp
```

This lists all registered MCP servers and their connection status. You should
see `ecm` with a connected status and the list of available tools.

If `ecm` is listed but shows an error, check that the ECM MCP container is
running and reachable at port 6101. See
[Troubleshooting: MCP server not reachable](#mcp-server-not-reachable-settings-mcp-integration-shows-offline).

**First test prompt:** ask Claude Code "List my ECM channels." A valid response
confirms the connection is working end-to-end.

### Reconnecting after a container restart

If the ECM MCP container restarts mid-session, re-run `/mcp` in Claude Code.
This triggers a fresh connection attempt without requiring a full Claude Code
restart.

### Key rotation

To rotate the static key: **Settings → MCP Integration → Regenerate Key** in
ECM, then update `ECM_MCP_API_KEY` and restart Claude Code (or run `/mcp`).

For the full rotation procedure, see
[Key rotation](#key-rotation) in the Claude Desktop section above. The
ECM-side steps are identical.

---

## Troubleshooting

### `spawn npx ENOENT` in Claude Desktop logs (mcp-remote path)

Node.js is not on `PATH` for the Claude Desktop process. Install Node.js (see
[mcp-remote prerequisites](#prerequisites)) and restart Claude Desktop.

This error only applies to the mcp-remote bridge.

### MCP server not reachable (Settings → MCP Integration shows "offline")

The ECM container probes the MCP container's `/health` endpoint at
`ecm-mcp:6101` by default (Docker DNS on the compose network). If both
containers share `network_mode: host`, set `MCP_HOST=localhost` on the ECM
service so the probe targets the host loopback instead.

Verify manually:
```bash
curl http://localhost:6101/health
```

If the response is `400 Invalid host header` or `421 Invalid Host header` in
remote mode, add the proxy-facing hostname (without scheme or port) to `MCP_ALLOWED_HOSTS` on the
`ecm-mcp` service and recreate that container. Do not use `*`; it disables the
DNS-rebinding boundary the allowlist provides.

If that fails, check the MCP container is running (`docker ps | grep ecm-mcp`)
and that the `ECM_URL` environment variable on the MCP container points at the
ECM container correctly.

### MCP tools fail with "All connection attempts failed"

This is the reverse direction of the probe issue above: the MCP container
cannot reach the ECM **backend**. The MCP server calls ECM's API at the URL in
its `ECM_URL` environment variable, which defaults to `http://ecm:6100` (Docker
DNS on the canonical compose network).

If both containers run with `network_mode: host`, the `ecm` service name has no
DNS entry on a shared host network. It resolves to nothing (or to a wrong
external address), and the ECM backend answers only on the host loopback. Every
MCP tool call then fails with `All connection attempts failed`.

**Fix:** set `ECM_URL=http://localhost:6100` on the MCP service so it reaches
the backend over the host loopback. This is the symmetric partner to the
`MCP_HOST=localhost` setting above: `MCP_HOST` fixes the ECM → MCP probe, while
`ECM_URL` fixes MCP → ECM tool calls.

```yaml
# docker-compose.yml — host-networking deployment
services:
  ecm:
    network_mode: host
    environment:
      MCP_HOST: "localhost"               # ECM → MCP health probe
  ecm-mcp:
    network_mode: host
    environment:
      ECM_URL: "http://localhost:6100"    # MCP → ECM backend API
```

Verify manually from inside the MCP container:
```bash
docker exec ecm-ecm-mcp-1 python3 -c "import urllib.request; print(urllib.request.urlopen('http://localhost:6100/api/health', timeout=5).read().decode())"
```

### MCP server online but "API key not configured"

The MCP container's `/health` endpoint reports `api_key_configured: false`. ECM
projects only MCP credential material through the dedicated `ecm-mcp-secrets`
volume; the sidecar cannot read ECM's `/config` volume. The most common causes:

- **The key was explicitly revoked.** `field_empty` is preserved across
  restarts; open Settings → MCP Integration and click Regenerate Key when you
  intend to re-enable MCP.
- **The projection volume is missing.** Verify both containers mount the
  dedicated `ecm-mcp-secrets` volume as shown in `docker-compose.mcp.yml`.
- **The two containers disagree on identity.** The projection is owner-only,
  so `PUID`/`PGID` must be identical for `ecm` and `ecm-mcp`; a mismatch shows
  up as `invalid_key` (public key) or `wrong_owner` (backend credentials).

The MCP `/health` endpoint surfaces a machine-readable `api_key_status`
(`file_not_found` / `invalid_key` / `field_empty`) plus a setup hint describing
the exact cause. With the supported volume and matching identity, fresh install
and upgrade startup need no manual projection step.

Diagnose:
```bash
curl -s http://YOUR_ECM_HOST:6101/health | python3 -m json.tool
```

### `503` while rotating or revoking the MCP key

Do not retry continuously. A `mcp_api_key_storage_unavailable` response means
ECM preserved an `api-key` or `.api-key.recovery` artifact it could not trust.
Under `MCP_SECRETS_DIR`, verify each name that exists is a regular file rather
than a symlink, has one hard link, is owned by the container's `PUID`:`PGID`,
and uses mode `0600`. Repair the mount, ownership, or mode, then retry the same
operation; ECM automatically revalidates changed paths. If the recovery JSON
itself is malformed, preserve it byte-for-byte and do not guess, rewrite,
replace, or delete it: there is no proof that another copy is fresher, and it
may be the only redo evidence for a completed rotation or revocation. Repair
only the mount or file metadata, then contact support with the preserved
artifact before retrying.

A `mcp_api_key_durability_indeterminate` response is different: the response
states whether the new key or revocation is active now, but the filesystem
refused the durability proof. Keep the returned rotation key, stop further key
changes, and verify that the `MCP_SECRETS_DIR` filesystem is writable and
supports atomic rename plus file and parent-directory `fsync` (avoid network or
FUSE storage that rejects them). Repair or move the storage, restart ECM once
to let `.api-key.recovery` reapply the active value, confirm MCP authentication,
and retry rotation or revocation. Do not delete the recovery record as cleanup.

### `401 Invalid API key` on tool calls

The Bearer value supplied by your local environment does not match
the authoritative `api-key` sidecar.
Regenerate the key in ECM (or copy the current value), update your config, and
restart Claude.

Make sure you are using the **MCP** key (`mcp_api_key`), not the Dispatcharr
REST token (`dispatcharr_api_key` / legacy `api_key`).

### Upgrading from the deprecated SSE transport

The MCP server moved from the deprecated SSE transport (`/sse` + `/messages/`)
to the modern Streamable HTTP transport on a single `/mcp` endpoint. If you have
an existing config with `/sse` or `?api_key=...`, change the path to `/mcp`,
remove the query string, configure the Bearer header, and rotate the exposed
key. The `/sse` endpoint was removed.

---

## Going deeper

- **Architecture**: [`docs/architecture.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/architecture.md), covering the MCP
  Server static-key baseline and `settings.json` credential schema.
- **README**: [MCP Server (Claude Integration)](https://github.com/MotWakorb/enhancedchannelmanager#mcp-server-claude-integration),
  covering quick-start setup and the "choose your method" overview table.
- **Retired OAuth offering**: [`docs/security/threat_model_mcp_oauth.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/security/threat_model_mcp_oauth.md)
  (Superseded): history of the OAuth 2.1 "Custom Connector" offering that was
  retired, and whose code was removed from the tree in v0.17.3.
## Destructive-operation confirmation

MCP destructive and bulk operations use two calls. The first call is a
mutation-free preview and returns a short-lived confirmation token. Repeat the
same call with that token only after reviewing the exact targets.

Channel-pipeline runs, normalization bulk apply, and Emby logo clearing also
create a backend-owned plan. The plan records the exact scope and target/action
set, expires after five minutes, and is single-use. A changed channel, rule, or
Emby lineup rejects the commit before mutation and requires a new preview.
The backend reports authoritative write and unique-target counts from that
exact plan; either count reaching 500 is refused at preparation and checked
again at commit. Preview list lengths are not used as a substitute for these
server counts.
Plans are held only in bounded process memory: restarting ECM invalidates them,
and no API keys, stream URLs, or other credentials are stored in a plan.

Pipeline rules that refresh providers before evaluating channels use a staged
three-call flow: review and confirm the exact provider refresh, then review the
new post-refresh channel write plan and confirm that second plan. The refresh
token cannot authorize channel writes, and a restart or expiry requires starting
again. Pipelines without a pre-refresh remain a two-call flow.

Other state-derived bulk tools freeze their exact identifiers in the MCP
confirmation: struck-out cleanup uses the signed stream and empty-channel IDs,
notification operations use the signed unread/read notification IDs, and dummy
EPG generation uses the signed enabled profile IDs. Channel stream replacement,
stream reordering, and EPG-logo assignment also carry signed read preconditions;
drift is rejected before the first write. Credential-bearing URLs are retained
only in the bounded in-process confirmation record and are redacted from the
human-readable preview.

Dispatcharr does not provide a multi-request transaction. ECM validates the
complete plan read set and reruns the side-effect-free structured planner under
the planned-run lock before the first write. Any difference in its canonical
decision or exact write payload requires a new preview. ECM persists the exact
execution program and target-scoped rollback snapshot immediately before replay,
then compensates reversible writes in reverse order if a later write fails. A
failure response lists target-specific completed writes and any compensation
failures. Deletes and channel-profile membership changes cannot always be
restored with the same upstream identifiers; inspect the execution record and
rollback snapshot before retrying a partially failed commit.
