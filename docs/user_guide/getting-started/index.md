# Getting Started

## Section purpose

Get a new operator from "I just installed ECM" to "ECM is connected to Dispatcharr and I can see my channels." Everything in the rest of the user guide assumes a working ECM-to-Dispatcharr connection. If that's broken, this section is what you need.

## Articles

| Article | Purpose | Status |
|-|-|-|
| [`installation.md`](installation.md) | Prerequisites (Docker, a running Dispatcharr, network reachability), the minimum compose snippet, where the persistent `/config` volume should live, confirming the container is up. | **Shipped** |
| [`first-run.md`](first-run.md) | What you see the first time you load the UI, the initial admin user setup, how the preflight checks and config-persistence warning work. | **Shipped** |
| [`connect-dispatcharr.md`](connect-dispatcharr.md) | Entering the Dispatcharr base URL and credentials, what each field means, how to verify the connection succeeded, common reasons it fails. | **Shipped** |
| [`your-first-channels.md`](your-first-channels.md) | End-to-end workflow tutorial: add an M3U account, add an EPG source, choose which stream groups to sync, refresh, then create channels, channel groups, and stream assignments in Channel Manager. Spans M3U Manager → EPG Manager → Channel Manager. | **Shipped** |
| [`next-steps.md`](next-steps.md) | A short "where do I go from here?", pointing at Channels & Streams for day-to-day work, Channel Pipeline for automation, and Backup & Restore so a new operator sets up backups before they need them. | **Shipped** |

## Planned articles

| Article | Purpose |
|-|-|
| `verify-healthy-connection.md` | What a healthy connection looks like (channels visible, streams visible, no banner warnings), plus the `/health` endpoint as the operator-friendly readiness check. This is currently covered inline in `connect-dispatcharr.md`'s "Confirm the connection is healthy" section; a dedicated article may be split out later. |

## Going deeper

- [`docs/architecture.md`](../../architecture.md): system overview, ports, where the SPA is served from.
- [`docs/auth_middleware.md`](../../auth_middleware.md): auth model details if the connection setup is failing on credentials.
- [`docs/dispatcharr_api.md`](../../dispatcharr_api.md): what ECM expects from Dispatcharr's API surface.
