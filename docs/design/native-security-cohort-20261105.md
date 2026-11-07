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
* Refresh only 12 stale direct descriptors per architecture, preserving 35/34.
* Select canonical official feeds, with exact reviewed repomd identity checks
  after GPG refresh in bootstrap, builder and runtime. Changing rolling metadata
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
* Collect the entire actual final-runtime RPM source graph freshly. Reuse only
  the original two native Packman SRPMs through their architecture-specific
  manifest hashes and full repeated source authentication/completeness checks.
  Do not reuse the old all-origin carrier indexes.
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
