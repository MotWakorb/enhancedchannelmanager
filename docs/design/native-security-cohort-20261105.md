# Native security cohort refresh: 20261105

Scope: the librsvg, Speex, Mozilla CA and Brotli P1s only. No waiver, production
rollout, new signer, vendor fork or weakening of existing security gates.

The November 7 bounded official survey supersedes the old ARM package/source
availability hold for these four groups. Both binary and correct per-architecture
source feeds now expose all fixes in signed Tumbleweed 20261105 cohorts under
SUSE fingerprint `AD485664E901B867051AB15F35A2F86E29B700A4`. Complete selected
binary/SRPM downloads, embedded signatures, whole-archive hashes and declared
Source/Patch completeness were checked. That is availability/source evidence,
not verification of a delivered application image.

## Minimal connected change

* Preserve the exact recovered base identity, FFmpeg archive/signature/patch,
  gosu/modules/toolchain, configure flags and both Packman input/key sets.
* The security refresh changed only 12 stale direct package identities per
  architecture, preserving the other 35/34 identities. The later AMD transport
  repair below changes descriptor URLs, not those identities or checksums.
* Select the reviewed official binary transport, with exact repomd identity checks
  after GPG refresh in bootstrap, builder and runtime. Changing signed metadata
  is an explicit failure requiring another reviewed transaction.
* Every subsequent solve uses global `--no-refresh`: bootstrap Python,
  builder update/install, delayed local codec installation, runtime
  update/install and resdet dependencies. A previously checked repomd is not
  permission for auto-refresh to replace unpinned transitives while keeping all
  explicit package identities unchanged. The enabled repository's automatic
  refresh setting cannot override those transaction-local no-refresh options.
* Runtime `clean --all` removes metadata before resdet-builder inherits the
  filesystem. Resdet therefore performs a new explicit GPG-enforced refresh and
  the same exact cohort hash check, then its no-refresh install. If the current
  vendor cohort differs, it fails before solving; a cold cache is not handled by
  simply adding no-refresh to an install that has no metadata.
* Install six exact fixed runtime RPM identities in the builder and propagate
  them alongside the actual recursive ELF provider graph. Record security
  packages separately; CA is not an ELF provider.
* Collect the entire actual final-runtime RPM source graph. Reuse
  the original two native Packman SRPMs through their architecture-specific
  manifest hashes and full repeated source authentication/completeness checks.
  The one expired AMD release SRPM has its separate exact retained-byte handoff;
  other SUSE origins remain fresh. Do not reuse old all-origin carrier indexes.
* Keep the source consumer's frozen recipe binding current and the strict SBOM
  Dockerfile contract exact for this reviewed source mode. Generated SBOMs and
  final versioning belong to the integrating leader.

See [native inputs](../../docker/native/README.md) and
[source delivery](../security/native-source-delivery.md) for exact interfaces.

## Acceptance and nonclaims

Actual compatibility review must cover LAME 4.0, HarfBuzz 15.0, SVT-AV1 4.3 and
libopenmpt 0.8.10. Native AMD and ARM execution, source/live-provider matching,
all 28 native checks, both bootstrap identities and complete unsuppressed image
scans remain mandatory. Both TLS contexts retain G3's exact negative fingerprint
and unaffected G1 positive control; an additional negative subject check rejects
the advisory's other named root, AAA Certificate Services. Unaffected/admin
roots are not replaced.

The old native-image or source-carrier passes do not validate this cohort. Primary
Critical-scored Brotli/Speex risk is not cleared by a scanner's Medium label.
Rolling repository/source retention and full immutable input hosting remain
separate architecture work; no fallback service is introduced here.

The previous CI recovery design describes the original 20261022 historical
repair. Its old source-carrier dependency is deliberately not the source path for
this refreshed security cohort. Existing CI may still pass an unused legacy
source context; removing that unnecessary recovery invocation is an integration
optimization, not permission to weaken any verification.

## AMD binary transport repair: same signed cohort, complete metadata

A subsequent local build on `9425528f` accepted the retained AMD release source
but failed resdet's cold GPG refresh: canonical repomd still matched the reviewed
SHA256 while its exact AppStream object redirected to a mirror returning 404.
The source-builder was cancelled by that separate stage failure; retention was
not the cause.

The established official history binary repository
<https://download.opensuse.org/history/20261105/tumbleweed/repo/oss/>
serves **identical repomd bytes**: SHA256
`36db62d4ce6887faa6ec09802a5b1311c1e14e075e5d7011fc590ceccbdabbe6`,
signed by the same SUSE fingerprint. All 20 advertised metadata objects were
freshly downloaded and checksum-verified. The missing AppStream gzip is
9,351,050 bytes, SHA256
`37b199756ac434e14faa8784f6a737e3425d87992284dd89982918d51bf49aa4`;
its SHA512 is exactly the checksum in that pinned signed repomd.

All **47 AMD builder and six security descriptors** were joined against that
signed primary. Every selected binary was actually downloaded from history,
whole-byte checksum-verified and independently authenticated with native RPM
tools: exact name/epoch/version/release/architecture/SOURCERPM and `openSUSE`
vendor, with no provider substitution. Metadata availability is not inferred
from a 200 directory listing. A small recorded real projection lives in
`backend/tests/fixtures/native_runtime/history-20261105-transport.json`.

`official_repository` continues to record the canonical official origin;
AMD `builder_repository` and all 53 corresponding binary descriptor URLs now
select the literal history/20261105 transport. The helper rejects canonical,
other-date, HTTP or mixed-descriptor transport. Bootstrap and runtime select
this same repository; resdet inherits runtime's repository and still performs
its own cold GPG refresh and identical cohort hash check. No fallback is added.
All seven following solves retain their global `--no-refresh` constraints.

**This is not a byte-identical descriptor change:** the 47 builder and six
security descriptor URLs change explicitly, including the 35 previously
unchanged AMD descriptors. Every non-URL field and checksum remains unchanged.
Manifest bytes/hash and the exact consumer/generator bindings necessarily change;
parent-owned generated SBOM descriptors must be regenerated before delivery.

ARM manifest/policy/URLs are unchanged. Base, packages, versions, cohort, keys,
security floors, FFmpeg/gosu inputs/configuration and Packman inputs remain
unchanged. **Binary history is not source history:** the canonical per-arch
source feeds and single retained AMD SRPM remain exactly as before; unavailable
historical source routes are not selected by this repair.

Bounded disposable native refresh/solve proofs do not replace new exact-head
full application/image/source/native/scanner verification. Earlier passes and
running CI results remain evidence only for their original heads, not this edit.
