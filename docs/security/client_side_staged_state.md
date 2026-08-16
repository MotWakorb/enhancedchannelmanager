# Operator-Binding of Client-Side Staged State

**Beads:** enhancedchannelmanager-r93hq (epic), enhancedchannelmanager-jazna
**Date:** 2026-08-16
**Status:** Shipped. Recorded here because ECM had no prior security note covering browser-held operator work.
**Related:** `docs/architecture.md` ("The staged ledger: one persisted unit, three derived views"), `docs/auth_middleware.md`.

---

## Why this exists

Edit Mode holds staged channel work in memory and writes nothing until **Apply All**. In-app navigation, browser Back/Forward and Sign Out all pass through the exit guard, so none of them can discard that work silently. One path cannot be guarded, because the application is not what is leaving: the **session** is. A failed token refresh, or a `401`/`403` from `/me`, clears the user, the app swaps itself for the login page, and the in-memory ledger dies with it. Apply is impossible at that moment, because every commit call would be rejected by the very session that just ended.

The chosen remedy is persistence, which means operator work now sits in `sessionStorage` rather than only in memory. That is a new asset in the browser, and it carries a new question: **whose work is it?**

## The control

The persisted ledger is stamped with an operator key derived from the authenticated identity that staged it, qualified by auth provider so that a local account id and a Dispatcharr account id cannot collide. On read, a ledger whose stamp does not match the current operator is **destroyed, not withheld**, in the first render before any persistence effect can run.

Destruction rather than concealment is the deliberate choice, and it is also why the storage key is fixed rather than per-operator: **a foreign ledger has to be findable in order to be destroyed.** A per-operator key would leave one operator's staged edits sitting in a shared workstation's tab, invisible but intact.

## The risk it addresses

Two people share a workstation. Operator A stages a few hundred channel edits and their session expires. Operator B signs in on the same tab. Without the binding, B is offered A's staged work, and the natural next action is **Apply All**. Every one of those changes then reaches Dispatcharr under B's credentials, and the Journal attributes all of them to B. The damage is not confined to the lineup: it is an audit trail that names the wrong person, which is worse than no audit trail because it will be believed.

This is an integrity and attribution concern rather than a confidentiality one. B could not read A's staged edits in any useful form before applying them, and the substantive harm is the misattributed write.

## What the ledger holds

Audited at build time. The persisted operations carry channel and group names, channel numbers, entity ids, `tvg_id`, `tvc_guide_stationid` and `logoUrl`. They carry **no credentials, no tokens and no file bytes**.

One forward-looking caveat is recorded here because it is not obvious from the data as it currently stands: `logoUrl` is presently constrained to remote URLs only by a dead prop in `ChannelsPane`, while the logo picker itself accepts `data:` and `blob:` URIs. If that prop is ever re-wired, `logoUrl` must be re-audited against what may enter `sessionStorage`, since an inline image URI would put file bytes into browser storage that this audit says are not there.

## Residual limits

- `sessionStorage` is readable by any script running in the page's origin. This control binds the ledger to an identity; it does not defend against script injection, which is out of scope for it and addressed by ECM's normal input-handling and CSP posture.
- A twelve-hour age bound covers a tab left open across a suspend. It is a staleness control, not a security boundary; the operator binding is what makes a foreign ledger unusable, at any age.
- A restored ledger is validated against the current lineup before it is offered, and consent recorded for a duplicate channel number is withdrawn when the channels it named have changed. That is a correctness control, not a security one, but it is the reason a restore is *offered* rather than applied silently.
