# Escalation Paths

When the earlier articles in this section have not resolved the problem, this is
where you take it. Pick the destination that matches what you have, not the one
that feels most urgent: a report sent to the wrong place mostly costs you a
round trip.

Bring the material from
[Gather support information](gather-support-information.md) with you. Every
route below is faster with a version string and a debug bundle attached.

## Decide where to go

| What you have | Where it goes |
|-|-|
| ECM behaved incorrectly, and you can describe how to reproduce it | A GitHub issue. |
| ECM is missing something you need | A GitHub issue, described as the outcome you want rather than the implementation you imagine. |
| You are not sure whether it is a bug or your configuration | Read the relevant user-guide section first, then open an issue and say plainly that you are unsure. An issue that turns out to be a configuration question is still useful: it means the documentation did not answer it. |
| Something is actively broken at scale, and you run ECM with an on-call responsibility | The runbooks. See below. |
| A security vulnerability | Do not open a public issue. See below. |

## GitHub

The repository is the project's system of record:

**<https://github.com/MotWakorb/enhancedchannelmanager>**

Issues live at `/issues` on that repository. Both destinations are also reachable
from inside ECM: the header carries a **?** icon linking to the user guide and a
GitHub mark linking to the repository.

Before you open an issue, search the existing ones. ECM ships frequently, and a
symptom you are seeing today is often a known issue with a fix already in a build
you have not pulled. Check your build against the fix with
[`docs/versioning.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/dev/docs/versioning.md) before reporting a regression.

What makes an issue actionable, in rough order of value:

1. The exact version from `/api/health`, build suffix included.
2. A debug bundle.
3. The verbatim error text or banner, not a paraphrase.
4. Reproduction steps that start from a state somebody else can reach.
5. Whether ECM is behind a reverse proxy, and which one. This changes the
   concurrency profile ECM sees and is the difference between reproducing and
   not; see
   [Common Issues](common-issues.md#requests-fail-in-bursts-behind-a-reverse-proxy).

## Security issues

If you have found something that could compromise an ECM instance or the
credentials it holds, **do not describe it in a public issue.** ECM's security
policy asks for a private report through GitHub Security Advisories:

**<https://github.com/MotWakorb/enhancedchannelmanager/security/advisories/new>**

Include a description, reproduction steps, and the affected versions. The
conversation stays between you and the maintainer until a patch is ready. Note
that only the latest release is patched for security issues, so upgrade before
reporting. The full policy, including target response times, is in the
repository's `SECURITY.md`.

The same applies to anything you attach. A debug bundle redacts credentials, but
raw log output does not; see
[what the bundle redacts](gather-support-information.md#what-the-bundle-redacts-and-what-it-does-not).

## Discord

Release notes for ECM are published to a Discord channel, and other operators
gather there. It is a reasonable place to ask "is anyone else seeing this?"
before formalising a report.

This guide does not publish an invite link, because the repository does not
carry one. If you are not already in that community, get the invite from the
project rather than from a link found elsewhere.

Discord is for conversation. A defect discussed there and never filed does not
get fixed, so once you know it is a bug, open the issue.

## The runbooks

`docs/runbooks/` is a separate tree of incident-grade procedures, written for
somebody responding to an active problem under time pressure. Each one follows
the same shape: trigger, symptoms, diagnosis, resolution, escalation.

They are not a substitute for this section. This section is for the operator
configuring ECM; the runbooks are for the operator whose ECM is on fire. Reach
for them when the answer to "is this urgent?" is yes.

The ones an operator is most likely to need. Each one opens in the ECM
repository on GitHub:

| Runbook | When |
|-|-|
| [Request timeouts, concurrency, CPU offload](https://github.com/MotWakorb/enhancedchannelmanager/blob/dev/docs/runbooks/request-timeout.md) | 503s, 504s, `Exceeded concurrency limit.`, or requests that hang. |
| [Readiness availability](https://github.com/MotWakorb/enhancedchannelmanager/blob/dev/docs/runbooks/readiness_availability.md) | `/api/health/ready` failing, across one sub-check or several. |
| [Infra-side cache invalidation](https://github.com/MotWakorb/enhancedchannelmanager/blob/dev/docs/runbooks/infra-cache-invalidation.md) | The UI is stale after an upgrade and a hard reload does not fix it, because a proxy or CDN in front of ECM is serving old assets. |
| [Disaster recovery restore](https://github.com/MotWakorb/enhancedchannelmanager/blob/dev/docs/runbooks/disaster-recovery-restore.md) | Rebuilding an instance from a backup after losing the original. |
| [Database size warning](https://github.com/MotWakorb/enhancedchannelmanager/blob/dev/docs/runbooks/database-size-warn.md) | The database has grown beyond its expected envelope. |

The full index is in [`docs/runbooks/README.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/dev/docs/runbooks/README.md).

## What to do while you wait

Escalating does not have to mean stopping. Two things are usually available:

- **Undo the change that broke it.** See [Recovery patterns](recovery-patterns.md).
- **Take a backup before you experiment further.** If the instance is in a
  strange state, capturing that state is worth more than preserving it: it means
  you can try things without making the eventual diagnosis harder. See
  [Take a Backup](../backup-restore/take-a-backup.md).

## Going deeper

- [Gather support information](gather-support-information.md): what to bring, whichever route you take.
- [Recovery patterns](recovery-patterns.md): getting back to a working state while you wait.
- [`docs/runbooks/`](https://github.com/MotWakorb/enhancedchannelmanager/blob/dev/docs/runbooks/README.md): the full incident-response index.
