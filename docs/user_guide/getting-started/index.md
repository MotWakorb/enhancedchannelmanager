# Getting Started

> **Audience:** Operator, day one. You have ECM installed (or are about to) and need to get it talking to Dispatcharr.
>
> **Status:** Stub — articles below are placeholders.

## Section purpose

Get a new operator from "I just installed ECM" to "ECM is connected to Dispatcharr and I can see my channels." Everything in the rest of the user guide assumes a working ECM-to-Dispatcharr connection. If that's broken, this section is what you need.

## Intended audience

- **Operator** running ECM for the first time.
- **Operator** rebuilding after a migration, container rebuild, or Dispatcharr URL change.

End users do not read this section.

## Planned articles

| Article | Purpose |
|-|-|
| `installation.md` | Prerequisites (Docker, a running Dispatcharr, network reachability), pulling the ECM image, the minimum compose snippet, where the persistent `/config` volume should live. |
| `first-run.md` | What you see the first time you load the UI, the initial admin user setup, where to find the version number. |
| `connect-dispatcharr.md` | Entering the Dispatcharr base URL and credentials, what each field means, how to verify the connection succeeded, common reasons it fails. |
| `verify-healthy-connection.md` | What a healthy connection looks like — channels visible, streams visible, no banner warnings — plus the `/health` endpoint as the operator-friendly readiness check. |
| `next-steps.md` | A short "where do I go from here?" — pointing at Channels & Streams for day-to-day work, Auto Creation for power features, and Backup & Restore so a new operator sets up backups before they need them. |

## Going deeper (for now)

Until the articles are filled in, the following developer-facing docs are the closest substitute:

- [`docs/architecture.md`](../../architecture.md) — system overview, ports, where the SPA is served from.
- [`docs/auth_middleware.md`](../../auth_middleware.md) — auth model details if the connection setup is failing on credentials.
- [`docs/dispatcharr_api.md`](../../dispatcharr_api.md) — what ECM expects from Dispatcharr's API surface.
