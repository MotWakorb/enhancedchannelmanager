# Security Policy

Enhanced Channel Manager (ECM) is a solo-maintained open-source project. Security reports are taken seriously and handled privately until a fix is available.

## Supported Versions

Only the latest released version is supported for security fixes. ECM follows a rolling release cadence; older versions are not patched.

| Version | Supported |
|---------|-----------|
| latest  | Yes       |
| older   | No        |

If you are running an older version, upgrade to the latest release before reporting a vulnerability.

## Reporting a Vulnerability

**Please do not open public GitHub issues for security vulnerabilities.**

Report privately via GitHub Security Advisories:

1. Go to https://github.com/MotWakorb/enhancedchannelmanager/security/advisories/new
2. Provide a clear description, reproduction steps, affected versions, and (if possible) a suggested fix or mitigation
3. Include whether you want credit in the published advisory and your preferred name/handle

The private disclosure flow keeps the conversation between reporter and maintainer until a patch is ready.

## Disclosure Timeline

Target response times for a solo maintainer:

| Step | Target |
|------|--------|
| Initial acknowledgment | Within 7 days of report |
| Triage and severity assessment | Within 14 days |
| Patch released for critical issues | Within 30 days |
| Patch released for non-critical issues | Best effort, prioritized against other work |

Once a fix ships, the advisory is published on GitHub with credit to the reporter (if requested). A corresponding note is added to the GitHub Releases entry for the version that contains the fix.

## Scope

In scope:

- The ECM application (backend, frontend, MCP server)
- Published container images at `ghcr.io/motwakorb/enhancedchannelmanager` and `ghcr.io/motwakorb/enhancedchannelmanager-mcp`
- Default configuration and setup flow

Out of scope:

- Vulnerabilities in upstream dependencies unless ECM's usage is the root cause — report those to the upstream project
- Third-party services ECM integrates with (Dispatcharr, external EPG providers, etc.)
- Issues that require attacker-controlled configuration the operator has already knowingly enabled (e.g. intentionally disabling authentication)

## Coordinated Disclosure

Please give the maintainer a reasonable window to patch before public disclosure. The default expectation is 90 days from initial report, or the date a fix ships — whichever comes first. If you believe an issue is being actively exploited in the wild, say so in the report and the timeline accelerates accordingly.
