# Emby Integration

> This is the Emby-specific deep dive. For the at-a-glance comparison of all
> three media-server integrations (Emby, Plex, Jellyfin), start at the
> [Integrations overview](index.md).

ECM polls your Emby server's live-session feed (`GET /Sessions`) and
cross-references it against ECM's own bandwidth telemetry. When a viewer is
watching an ECM-managed channel *through* Emby, ECM otherwise sees only the
Emby server's IP. Every Emby viewer collapses into one "Emby server"
identity. This integration recovers the real Emby username so the Stats page
attributes each session to the person actually watching.

## Prerequisites

Before you start, confirm:

1. **An Emby server you administer.** You need Dashboard access to create an
   API key. A regular Emby user account is not enough.
2. **Network reach from the ECM container to the Emby server.** ECM makes an
   *outbound* connection to the Emby URL you configure. Emby does not need
   to reach ECM. See [Network requirements](#network-requirements).
3. **ECM admin access** (when ECM authentication is enabled). The Emby
   settings and the Test Connection action are admin-only, because saving and
   testing involve handling a secret (the API key).

## How to get an Emby API key

1. Open the **Emby Dashboard** (the gear/settings menu, top-right in Emby
   Web).
2. Go to **Advanced → API Keys** (older Emby builds: **Expert → API Keys**).
3. Click **New API Key** (the **+** button).
4. Give it a recognizable name so it's identifiable in Emby's audit log
   (e.g. `ECM attribution`).
5. Copy the generated key. It is a 32-character hex string.

This is a **server-local API key**, not a user password and not a
Connect/account credential. It grants programmatic access scoped to *this*
Emby server. ECM only ever calls the read-only `/Sessions` endpoint with it.
If you ever need to revoke ECM's access, delete this key from the same
Dashboard screen. ECM's attribution simply stops; nothing else breaks.

> **Token model in one line:** one server-local API key per Emby server,
> created by you in the Emby Dashboard, used by ECM only to read the live
> session list.

## Configure it in ECM Settings

1. In ECM: **Settings → Integrations → Emby**.
2. **Enable** the Emby toggle.
3. **Base URL**: the URL of your Emby server as reachable *from the ECM
   container*. Examples:
   - `http://emby:8096` (Docker service name on the same network)
   - `http://192.168.1.50:8096` (LAN IP)
   - `https://emby.example.com` (reverse-proxy with TLS)
   - `http://proxy.example.com/emby` (reverse proxy with a sub-path: the
     sub-path is preserved)
4. **API Key**: paste the key from the Emby Dashboard.
5. Click **Test Connection**. A green result means ECM reached Emby and the
   key authenticated. See [Troubleshooting](#troubleshooting) for failure
   modes.
6. **Save**.

Attribution begins on the next telemetry poll cycle (within ~5 seconds; see
[How matching works](#how-matching-works)). You do not need to restart ECM.

### Re-saving without re-entering the key

The API key field uses a **preserve-on-omit** contract: if you change the
toggle or Base URL and save *without* re-typing the key, ECM keeps the
previously stored key. You only need to re-enter the key when you actually
want to rotate it. The Settings response never returns the stored key back to
the browser. It only reports whether one is configured.

## What attribution looks like in Stats

Once Emby is enabled and Test Connection succeeds:

- **Connected Clients (Active Channels):** each viewer row shows the Emby
  username with a **"via Emby"** badge instead of a bare IP. When several
  people watch the same channel through Emby, all their names appear on that
  channel's row.
- **User Stats / Watch Time:** per-user watch-time aggregates use the Emby
  username as the display name. The `attribution_source` field marks the row
  as Emby-attributed so the UI can render the badge.
- **Client IP:** ECM surfaces the *real* requesting-device IP that Emby
  reported for the session (`RemoteEndPoint`) as a separate field, distinct
  from the Dispatcharr connection IP ECM itself observes.

If a session can't be matched to any Emby viewer, the row simply shows the IP
as before. Attribution is additive: it never hides a session, it only
enriches the ones it can identify.

## How matching works

ECM caches the Emby `/Sessions` response for **5 seconds** (the cache TTL,
matched to the telemetry poll cadence). Within that window every internal
lookup hits the cache, so enabling Emby adds at most one `/Sessions` request
to Emby roughly every 5 seconds regardless of how many channels or viewers
are active. Concurrent lookups during a cache miss collapse to a single
upstream request (a thundering-herd guard), and a brief Emby outage falls
back to the last-known session list rather than blanking attribution.

For each active ECM session, ECM matches against the live Emby sessions in
tiers: channel-name match first (Emby renders live TV as
`"<number> | <channel>"`, e.g. `"408 | ESPN"`), then channel-number, then a
fuzzy stream-name fallback. The fuzzy fallback is the loosest tier and is
used only for direct same-host playback; the strict channel tiers do the
heavy lifting for normal live-TV viewing.

### Safe poll rate

You do not configure the poll rate. It is driven by ECM's bandwidth
telemetry cycle and bounded by the 5-second session cache. **The cache is the
rate limiter:** even under heavy concurrent attribution, Emby sees about one
`/Sessions` call every 5 seconds. There is no operator knob that can make ECM
poll Emby faster than the cache TTL allows, so there is no thundering-herd
risk to your Emby server from this integration.

## Network requirements

- ECM initiates an **outbound** HTTP(S) connection from the ECM container to
  the Base URL you configure. Emby never initiates a connection to ECM.
- The Base URL must be reachable from *inside* the ECM container, which is not
  always the same as from your desktop. To verify reach from the container:

  ```bash
  docker exec ecm-ecm-1 python3 -c "import urllib.request; print(urllib.request.urlopen('http://192.168.1.50:8096/System/Info/Public', timeout=5).read().decode())"
  ```

  The ECM image ships no `curl` and no `wget`, so a `docker exec` into it that
  calls either one answers `executable file not found`, which says nothing
  about Emby. `python3` is always present; it is what runs ECM.

  A JSON blob means the container can reach Emby. A `URLError` or a timeout
  means it cannot (firewall, wrong hostname, Emby not on the container's
  network). An `HTTPError` means something *did* answer, so the network is fine
  and the port or path is wrong.
- **Loopback and link-local addresses are rejected** by Test Connection as a
  safety measure: `localhost`, `127.0.0.1`, `::1`, and the cloud
  instance-metadata address `169.254.169.254` are blocked. Point ECM at
  Emby's LAN IP, Docker service name, or public hostname instead. Private
  LAN ranges (`10.x`, `172.16–31.x`, `192.168.x`) are intentionally allowed
  because that's where home media servers live.
- Connect/read timeouts are 5 s / 10 s: a misconfigured URL fails Test
  Connection promptly rather than hanging the UI.

## Token storage and privacy posture

The Emby API key is stored **in plaintext** in ECM's settings file
(`/config/settings.json` inside the container). This matches how ECM stores
every other integration credential (the Dispatcharr API key, SMTP password,
Plex token, Jellyfin key). ECM does **not** encrypt secrets at rest in this
release, and it does **not** transmit the key anywhere except to the Emby
server you configured.

Practical implications:

- Treat the ECM config volume as sensitive: anyone with read access to
  `/config/settings.json` (or a backup of it) can read the Emby key.
- The key is **never** returned to the browser by the Settings API and is
  **never** written to logs (logs record the Base URL, not the key).
- Rotating is cheap: delete the key in the Emby Dashboard, create a new one,
  paste it into ECM, Test Connection, Save.

## Troubleshooting

Test Connection is designed to show you the *actual* failure message inline
rather than a generic error. Common results:

| What you see | Likely cause | Fix |
|---|---|---|
| **"401 unauthorized — check API key"** | Wrong, revoked, or mistyped API key | Recreate the key in the Emby Dashboard and paste it again. |
| **"Emby request failed: …" / connection refused / timeout** | ECM container can't reach the Base URL | Verify reach with the `docker exec … wget` command above; check hostname/port and the container network. |
| **"Invalid URL scheme — must be http or https"** | Base URL used `file://`, `ftp://`, etc. | Use an `http://` or `https://` URL. |
| **"Invalid host — Destination IP … is denied by SSRF policy (…)"** | The Base URL resolved to an address your outbound policy denies. Link-local (`169.254.x.x`) is denied in every mode. Loopback (`localhost`, `127.0.0.1`, `::1`) and private LAN ranges are denied *only* in "Public internet only" mode; the default LAN-friendly mode permits them. | Check which mode is in the parentheses. If it says `public_only` and you meant to reach a LAN or loopback address, switch **Where backups can be sent** back to LAN-friendly under **Settings → Backup & Restore**. If it says `lan_friendly`, the address is link-local and is never permitted: use Emby's LAN IP, Docker service name, or public hostname. |
| **"Emby /Sessions returned 5xx"** | Emby server-side error | Check the Emby server logs; confirm Emby is healthy. |
| Test passes, but **no usernames appear in Connected Clients** | Integration enabled but no Emby-mediated playback yet, or the channel names don't line up | Start playback through Emby; confirm the ECM channel name matches the Emby channel display name. Connected Clients updates within ~5 s (the session-cache TTL). |

If usernames still don't appear after successful playback, ECM emits a
rate-limited diagnostic line in its logs (prefix `[EMBY-RESOLVER]`) showing
exactly what each match tier compared and rejected. This is useful for support.

## See also

- [Integrations overview](index.md): all three media-server integrations
  side by side, plus the multi-viewer and same-IP limitations.
- [`docs/api.md`: Enhanced Stats § per-channel attribution fields](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/api.md)
- [`docs/architecture.md`: User Attribution Pipeline](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/architecture.md)
