# Getting Started

## Section purpose

Get a new operator from "I just installed ECM" to "ECM is connected to Dispatcharr and I can see my channels." Everything in the rest of the user guide assumes a working ECM-to-Dispatcharr connection. If that's broken, this section is what you need.

## Articles

| Article | Purpose |
|-|-|
| [Install ECM](installation.md) | Prerequisites (Docker, a running Dispatcharr, network reachability), the minimum compose snippet, where the persistent `/config` volume should live, confirming the container is up. |
| [First Run](first-run.md) | What you see the first time you load the UI, the initial admin user setup, how the preflight checks and config-persistence warning work. |
| [Connect ECM to Dispatcharr](connect-dispatcharr.md) | Entering the Dispatcharr base URL and credentials, what each field means, how to verify the connection succeeded, common reasons it fails. |
| [Set Up Your First Channels](your-first-channels.md) | End-to-end workflow tutorial: add an M3U account, add an EPG source, choose which stream groups to sync, refresh, then create channels, channel groups, and stream assignments in Channel Manager. Spans M3U Manager → EPG Manager → Channel Manager. |
| [Next Steps](next-steps.md) | A short "where do I go from here?", pointing at Channels & Streams for day-to-day work, Channel Pipeline for automation, and Backup & Restore so a new operator sets up backups before they need them. |

## Going deeper

- [`docs/architecture.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/architecture.md) (in the repository, not part of this published guide): system overview, ports, where the SPA is served from.
- [`docs/auth_middleware.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/auth_middleware.md) (in the repository, not part of this published guide): auth model details if the connection setup is failing on credentials.
- [`docs/dispatcharr_api.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/dispatcharr_api.md) (in the repository, not part of this published guide): what ECM expects from Dispatcharr's API surface.
