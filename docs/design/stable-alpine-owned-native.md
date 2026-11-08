# Stable Alpine: owned native implementation

Tracks `enhancedchannelmanager-g3vd3` and `enhancedchannelmanager-mgt2t`, under
[ADR-017](../adr/ADR-017-stable-alpine-runtime.md). This is **intermediate build
evidence**, not completion of the [compatibility matrix](stable-alpine-parity.md).
Production `Dockerfile`, publication gates and deployed services are not switched
by this implementation. No image publication or deployment is authorized.

## Implemented providers

All build stages enforce official stable v3.24 main/community repositories and
native architecture. The Python builder remains the pinned Python 3.12 Alpine
base, not Alpine's newer distribution Python. Source/configuration overrides are
not exposed. Compilation of the six libraries and FFmpeg is networkless after
hash-verified input preparation.

| Provider | Retained contract and implementation |
|---|---|
| Codec2 1.2.0 | Original vendor install patch, all seven modes, baseline symbol surface, explicit codec-local numerical selection and separate initialization of two error accumulators |
| TwoLAME 0.4.0, Shine 3.1.1 | Original source hashes, Shine vendor GCC fix, unchanged bounded MP2/MP3 outputs |
| libxml2 2.13.9 | ABI-preserving `libxml2.so.2`, seven selected security backports with complete pre/post file witnesses and original patches retained |
| Speex 1.2.1 | Original reproducibility/vendor security patches, plus separately owned checked-capacity follow-up described below |
| librsvg 2.63.4 | Baseline version and retained Cargo graph: 352 registry crates and nine workspace packages, offline/locked builds, complete crate/file/lock checksum joins |
| gosu 1.19 | Existing commit/modules and Go 1.26.10, pinned official native Alpine Go builder, static binary and retained Go/module source |
| FFmpeg 8.1.3 | Existing signed source, signer, RTSP patch and all 49 configure arguments; composed owned headers/libraries and explicit GNU iconv header/link selection |

Recipes, source archives, patches, helpers, build inventories and products are
recorded by each component builder. Receipt statuses deliberately say
`BUILT_API_SMOKE_ONLY`, `BUILT_SECURITY_API_ONLY`, `BUILT_GOSU_ONLY` or
`BUILT_FFMPEG_ONLY`, not final-image acceptance. `package_version` fields in the
security recipe are recipe metadata, **not a claim of installed APK ownership**.

### Source distinctions and Cargo configuration

The GNOME librsvg archive differs from the signed baseline repack: upstream
SHA256 `31f07d93…`, baseline SHA256 `5e6b8f8c…`. All 2,239 member content/link/type
records match; 2,231 differences are mode-only. The explicit equivalence record
is retained, rather than relabeling an old hash as a new download.

The retained Cargo archive matches independently signature-verified AMD64 and
ARM64 baseline source payloads. Builds rehash those bytes; they do not pretend to
repeat that selection-time signature check. Meson invokes Cargo from its build
directory, so the builder creates and retains a fresh effective
`CARGO_HOME/config.toml` with an absolute verified vendor directory and offline
mode. Networkless compilation and upstream `--locked` remain enabled.

The seventh provider now has a clean AMD64 build (`jxl-build-01`), retaining
baseline SKCMS rather than the investigation's LCMS2 substitution. Its real
decoder/encoder version is 12000; original RGB24 output, byte-exact JPEG
reconstruction, native threads and all 105+9+1 public exports pass. Runtime,
source and SDK inventories were independently rehashed. The bounded independent
recipe/config/API review found no concrete blocker, not general security or ARM
approval. New FFmpeg composition requires this evidence and rejects `.0.11`
linkage; no incompatible SONAME alias is introduced.

Component receipts now bind the complete SDK (headers, pkg-config inputs and
libraries) as well as runtime products and sources. Earlier receipts without
`sdk_artifacts` cannot satisfy the composition gate and must be rebuilt, not
edited to manufacture new evidence.

### Codec2 numerical correction

The original 2/2,560-sample discrepancy was traced to numerical evaluation, not
packed input, encoder accumulator UB, random seed or changed PCM rounding.
Codec-local changes retain the original conversion to signed PCM:

- `POW10F` uses `(float)pow(10.0, (double)(float)(x))`, explicitly preserving the
  input narrowing of baseline `exp10f`, independent of `_GNU_SOURCE` detection.
- Hidden wrappers evaluate phase and base-10 logarithm in double, then convert
  to float.
- Six exact Arm optimized-routines files are retained from commit
  `20859d3fd9e797dde592c489c8ef7d383f778620`; the MIT license option is selected
  and notices retained. The real upstream build-time `test_defs.h` replaces the
  earlier investigation stub. Private target-only symbol renames,
  `USE_GLIBC_ABI=0`, hidden visibility and disabled contraction prevent a libc
  alias/interposition layer. There is no glibc library, shim, preload or replay.
- Both uninitialized `float se` accumulators are separately initialized. They
  were not the decoder discrepancy's cause, as decode-only tests demonstrate.

Actual native AMD64 build `codec-build-04` reproduces the original four smoke
files exactly. A wider fresh-process comparison covers seven modes × five
integer-only input corpora × 16 frames: **35/35 encoded and decoded outputs
match**, and decoding each actual baseline packed stream also matches. Each
matrix covers 153,600 PCM samples. All baseline 568 defined symbols remain;
the musl build has 570 including generated `_init`/`_fini`. The builder rejects
missing baseline symbols or additional math exports. This is a bounded corpus,
not proof of arbitrary-duration, modem/FreeDV, FFmpeg or native ARM parity.

### Speex review-driven correction

Independent source review found that the retained vendor patch alone still
allows negative truncation for a full external buffer and a signed-overflow
capacity-test bypass. The old native library fails the new full-buffer fixture.

The separate owned follow-up uses subtraction-based capacity checks, nonnegative
truncation in both external-buffer and realloc-failure paths, checked bit-count
bounds, and overflow-free flush rounding. The bit-count limit accounts for
validated consumed bytes without changing the original pre-flush capacity policy.
An independent re-review caught a near-`INT_MAX` consumed-buffer data-loss case;
that adjustment and payload-byte checks are included, not waived.

The fixture covers partial/full buffers, consumed bytes, negative input, a valid
lazy read-only `INT_MAX` input mapping, and the near-`INT_MAX` consumed-buffer
boundary. The builder requires both real native-library execution and an
ASan+UBSan build of the entire source with forced realloc failure. Sanitizer
runtimes in the initial AMD64 receipt came from the signed Alpine GCC package.
The first hosted ARM64 run proved native codec output, then failed because its
GCC package lacks those runtimes. The builder now requires native stable
`clang22`/`compiler-rt` for the identical sanitizer fixture on **both** architectures,
rather than skipping or weakening it. No sanitizer suppression or runtime
compiler installation is used. Corrected hosted run `42519704878` passed both
native architectures, including ARM's real Clang ASan+UBSan fixture. This run
predates the seventh provider and SDK/matrix additions; it does not verify them.

Libxml2's selected backports also received independent source review. Its
four-path NONET smoke and supplied 6732 upstream regression do **not** imply that
seven individual native exploit regressions ran. Upstream `make check` reports
missing optional external conformance suites; no complete-suite claim is made.

## Reproduction and native CI

Prepare sources once, into a new location:

```sh
python3 scripts/prepare_alpine_native_cache.py --output-dir /path/to/new-inputs
```

Build `docker/alpine-native/Dockerfile.codecs` with `owned-cache` and
`Dockerfile.security-libraries` with `security-cache` pointing to that location.
Export local evidence directories. Then build `Dockerfile.ffmpeg` with the
`codec-evidence`, `security-evidence` and `ffmpeg-cache` contexts. The composition
helper rehashes complete component product/source inventories and requires the
expected architecture, recipe and status. Build gosu via `Dockerfile.gosu`.

Also build `Dockerfile.libjxl` with `jxl-cache` pointing to the prepared inputs,
and supply its export as `jxl-evidence` when building FFmpeg. Native CI runs
`run_alpine_codec_matrix.py` against immutable local builder image IDs: 35 fresh
encode/decode plus 35 baseline-stream decode-only processes. Its reference is
the unchanged compressed AMD64 corpus; this is not an old-ARM baseline claim.

`.github/workflows/alpine-native.yml` provides separate native AMD64 and hosted
ARM64 compilation/execution jobs, with host-machine assertions, immutable action
pins, retained products/sources and **read-only GitHub permissions**. It has no
QEMU, registry push, deployment or alias for required production checks. Its
successful build is not a substitute for final-image compatibility/security.

The detailed local evidence is under
`/tmp/opencode/ecm-alpine-completion-20261108/`; investigation controls are under
`ecm-alpine-codec2-cause-20261108`, `ecm-alpine-codec2-wider-20261108` and
`ecm-alpine-security-review-20261108`. Preserve earlier failed attempts and
original goldens. Never replace them with a later success receipt.

The [compressed AMD64 receipt](evidence/stable-alpine-native-amd64-20261108.json.gz)
retains all 35 matrix results/commands, actual process loader/library hashes,
component product/source identities, original golden hashes, security behavior
and compiler/privilege-drop results. Security build `security-build-06` passed
the final native-library and forced-realloc-failure sanitizer fixtures, including
the consumed-boundary correction. The receipt does not invent an ARM result.

Local targeted native/policy/source/SBOM/publication regressions: **808 passed**.
The existing source-manifest SBOM index was regenerated to bind the changed
shared helper; it still describes the unchanged production path, not Alpine
runtime package/source coverage.

## Still required before the production switch

- Final current-recipe native provider/FFmpeg receipts and native ARM execution.
- Exact APK/owned-package installation, ELF-provider attribution, source and
  notice closure, actual musllinux bundled-native/GCC attribution and matching
  SBOM/source consumers. A component SDK copied into a disposable builder is not
  an accepted runtime packaging strategy or a new trust decision.
- Independent unfixed-aware APK/owned-source advisory denial and complete,
  unsuppressed immutable final-image scans. Trivy's missing unfixed Alpine
  records are not repaired by setting `ignore-unfixed: false`.
- Final resdet/GNU utility integration, complete ECM/MCP images, both identity
  pairs across restart/recreation, trust consumers, persistence and real MCP
  isolation on both architectures, plus the full application/frontend matrix.
- Production Docker/CI/source consumers must switch together without weakening
  their existing gates. Independent final review remains required.

## Additional PO decisions after the first native CI run

- **Owned APK signing approved:** use a build-only signing key, retain its public
  key with package evidence, and remove temporary installation trust. Final
  runtime trusted keys must be unchanged. No unsigned/`--allow-untrusted`
  installation, permanent additional key or extra repository is approved.
- **Seventh owned provider approved: libjxl 0.12.0, including threads/CMS.**
  Actual process/API/package inspection found baseline 0.12.0 versus Alpine
  0.11.2-r1, not the initially assumed equal versions. The older ordered dither
  differs from baseline's channel-offset blue-noise dither. Direct-library
  experiments explain all 233 differing RGB24 bytes. A scratch 0.12.0 build
  restores all five complete rare-input framemd5 outputs and all six capability
  listings. This is approval to implement/verify the provider, not acceptance
  of the scratch configuration, source/security closure or native ARM behavior.
  Preserve the authenticated baseline CMS source/configuration during integration;
  a passing sRGB fixture alone cannot justify changing its color engine.

## Signed package implementation (verification in progress)

`package_alpine_native.py` requires all five component exports, checks the exact
reviewed recipes/status/architecture and complete product/source inventories,
and requires that FFmpeg consumed the same codec/security/JXL receipts being
packaged. It inspects actual ELF SONAME/NEEDED entries to declare APK providers
and external dependencies, without fabricating `.0.11` aliases. Matching source,
notices and component receipts accompany the products in one owned APK. The
package name/version is content-bound to those component receipts; it is not
presented as a vendor APK or an upstream vulnerability-free version.

The APK is signed by a temporary RSA-3072 key in an automatically removed private
directory. Only the public key, package and evidence are exported. No runtime
key/repository is added and no unsigned, overwrite or solver-force option is
used. `install_alpine_native.py` verifies default-trust denial, then verifies and
installs offline with an external temporary `--keys-dir` containing vendor keys
and that public key. Temporary trust is removed even on failure. It checks the
unchanged runtime trust/repository inventories, exact installed bytes/modes/links,
complete APK contents and ownership of every delivered file. The musl loader
configuration is itself package-owned, preserving real `/usr/local/lib64`
FFmpeg paths without `LD_LIBRARY_PATH` or SONAME aliases.

The actual stable APK3 signing/verification probe passed; its recorded fixture
also includes untrusted-key and damaged-package denials. This is not yet proof
that the complete composite APK installs: `Dockerfile.packages` and native CI
must establish that with real complete exports on both architectures. External
APK/source attribution and advisory denial remain separate unfinished gates.

`verify_alpine_ffmpeg.py` supplements the existing rare-input gate with hashes
of all five complete decoded framemd5 outputs and six complete capability
listings from the original actual AMD64 image. The reference passes against
that immutable baseline; the known stock-JXL hash is explicitly rejected. It
runs after actual signed installation, not just against a builder SDK. Matching
these recorded outputs on ARM is still not independent old-ARM-baseline proof.
