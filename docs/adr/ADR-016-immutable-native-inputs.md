# ADR-016: Retain closed native binary/source input transactions

**Status: Proposed — PO approval required before implementation.**
Date: 2026-10-25. Bead: `enhancedchannelmanager-293n8`.
Tier: small-team (`ci-release`), spanning home-lab `ecm-core`.

## Problem and observed evidence

Digest-pinning a base image does not freeze subsequent package solves. Exact
builder pins expired in rolling repositories; binary cohort 20261023 and source
cohort 20261024 ceased supplying matching HarfBuzz/release SRPMs. PR #1040's two
authenticated mirror exceptions recovered those exact sources, but do not promise
future retention. Its successful AMD verification covered 156 runtime sources;
the separately missing fixed ARM packages remain a blocker, not a cache problem.

PR #1041's clean Compose build independently failed to resolve baseline exact
HarfBuzz, LAME and Python builder pins after 791 MCP tests passed. Warm image
build success is therefore not a cold-rebuild guarantee. Freezing only the main
image workflow would leave the isolation-build consumer exposed.

Official [dated history documentation](https://doc.opensuse.org/documentation/tumbleweed/updating_upgrading_reverting/)
and the [history listing](https://download.opensuse.org/history/) support an AMD
binary acquisition/recovery route. The inspected historical source route returned
403, and the bounded ARM-history probe returned 404. Neither proves universal
absence; neither establishes complete, indefinitely retained binary/source
history for both architectures or Packman. A dated URL is not a retention SLA.

## Proposed decision (not existing enforcement)

Capture a **complete, content-addressed native input bundle per architecture**,
then advance a single reviewed descriptor selecting both bundle digests. Prefer
existing GHCR/OCI facilities plus an independently verified backup over operating
a new repository service. OCI is a data carrier, not a new vendor authority.

Each bundle must retain:

- Immutable base index/child/config/blob identities and original bytes.
- Original signed SUSE metadata and checksum/open-checksum bindings for one
  selected binary cohort per architecture; reconcile inherited base packages
  with that cohort rather than preserving an arbitrary mixture.
- Complete bootstrap, builder, runtime and resdet package closure, not just the
  47 direct builder descriptors. Record name/epoch/version/release/arch/vendor/
  SOURCERPM, exact hashes, signer and stage membership.
- The separately approved Packman input vector with its exact binary/source
  identities; do not pretend independent vendors publish one atomic snapshot.
- Exact corresponding sources, signature/header/Source-Patch validation evidence,
  owned FFmpeg/gosu/Go/resdet/Python inputs and corresponding notices. Same-name
  noarch packages or SRPMs are not assumed byte-identical across architectures.
- Recipe/policy/helper hashes, original and actual retrieval provenance, complete
  inventories and independent validation receipts. Output-image identities belong
  in a later input-to-output receipt, avoiding circular manifests.

The two architectures may use different explicitly selected eligible cohort dates;
one architecture may not mix arbitrary SUSE cohorts. No unsupported overlay,
substituted source, broader vendor key, re-signed repository or rolling fallback.

## Transaction and trust boundary

1. Select and quarantine candidate cohorts without advancing the production
   descriptor. Authenticate frozen metadata using already approved signer policy.
2. Resolve/acquire the complete closure natively; missing binaries or exact sources
   reject capture. Source SPECs remain data, not executable build authorization.
3. Authenticate every object, upload/read back by digest, verify the second copy,
   then mark the bundle complete. Partial staging is not selectable.
4. Review one descriptor diff covering both targets and all transitive changes.
5. On every consumption, recheck hashes, vendor signatures, headers and source
   closure. Storage receipts do not replace fresh authentication. PR jobs receive
   read-only archive access; untrusted code gets no capture/delete authority.
6. Prove empty-cache native replay with vendor networks denied, including Compose
   isolation. A missing bundle member must fail rather than fetch a rolling
   replacement. This is native-input replay, not a claim of a fully hermetic or
   bit-identical application build.
7. Preserve all current application/native/media/UID/trust/source and unsuppressed
   security gates, then use existing exact-SHA promotion without rebuilding.

“Atomic” means pointer-last selection of an already complete reviewed pair; it
does not claim a distributed transaction across Git, registry and backup.

The collector's existing cache seam already rechecks source signatures, but a
cache miss currently goes online. Sealed offline consumption, complete package
closure and producer/consumer bundle provenance are **new implementation work**.
Existing source pins remain independent; never relabel cache retrieval as a fresh
vendor request or accept a cache-provided expected hash as approval.

## Alternatives and operating cost

| Option | Disposition |
|---|---|
| Official history only | Acquisition aid; complete ARM/source/Packman retention unproved. |
| GHA cache/artifacts only | Reject as archive: eviction, run deletion and expiry remain. |
| GHCR content-addressed bundles + backup | Recommended; portable existing facilities, but deletion/access loss still possible. |
| Release assets/immutable releases | Viable alternative requiring separate setting/tag policy; split assets under the documented 2 GiB/file limit. |
| New full repository/mirror service | Disproportionate until closure-sized bundles demonstrably fail requirements. |

Measured AMD runtime SRPMs alone total **966,661,098 bytes** across 156 archives;
this excludes ARM, builder sources, binaries, bases and owned inputs. Full capacity
and transfer cost must be measured before provisioning. Public GitHub Packages'
documented free usage is not a perpetual price or retention promise.

Retain selectable/current and previous eligible input pairs, staged reviews and
every bundle referenced by still-distributed releases under an approved source
retention policy. No automated deletion of release-referenced inputs is proposed.
GC starts as a reference-aware dry run with explicit approval. Backup recovery
must restore original identities and repeat current verification before use.

Retained vulnerable snapshots remain useful evidence but ineligible for promotion
under newer security floors. No stale scan boolean, lowered floor, omitted source,
cross-architecture image pairing or missing fixed ARM package is rescued by storage.

## Approval and implementation acceptance

PO decisions: approve the closure/descriptor model; choose GHCR scope/visibility
versus Release assets; name capture, retention and backup owners; approve measured
capacity, retention, freshness and recovery policies. Confirm the exact existing
binary/metadata signer inventory and rotation/revocation handling. These choices
are not authorized merely by approval to investigate this bead.

Before implementation is accepted, tests must reject wrong arch/cohort/hash/key/
header/source, missing transitive inputs, malicious cache indexes, interrupted
capture/upload, incomplete pointers and deletion of referenced objects. Require
real signed fixtures, independent backup recovery and both native cold builds,
including Compose, followed by unchanged full output gates. None of these proposed
new mechanisms has been implemented or exercised in this ADR.

References: [cache eviction](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching),
[artifact deletion](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/remove-workflow-artifacts),
[OCI registry](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry),
[release limits](https://docs.github.com/en/repositories/releasing-projects-on-github/about-releases),
[immutable release limits](https://docs.github.com/en/code-security/concepts/supply-chain-security/immutable-releases).
