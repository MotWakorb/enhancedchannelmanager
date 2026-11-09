# Stable Alpine: owned native implementation

Tracks `enhancedchannelmanager-g3vd3` and `enhancedchannelmanager-mgt2t`, under
[ADR-017](../adr/ADR-017-stable-alpine-runtime.md). This is **intermediate build
evidence**, not completion of the [compatibility matrix](stable-alpine-parity.md).
Production `Dockerfile`, publication gates and deployed services are not switched
by this implementation. No image publication or deployment is authorized.

## Implemented providers

### Additional libxml2 advisory reconciliation (source checks only so far)

The official unfixed-aware tracker identified two additional regexp advisories
for review. The primary upstream corrections `76fe08d97de88bfaef2f7d5cd27f11954cc5bee2`
and `e89a8aae4c9b40cdafcf66b3f9e57c62db37bb55` are now included together after
the seven existing libxml2 backports. The second correction fixes allocation
failure handling introduced by the first; no bounds-only runtime candidate is
approved. Both patches apply unchanged with fuzz zero to the pinned 2.13.9
archive, and complete before/intermediate/final file hashes are sealed in the
recipe. The source version, SONAME and configure options remain unchanged.

The recipe/package/source contract subset passes (51 tests), but this alone is
not native execution or advisory clearance. Run `42653686737` subsequently built
the exact new recipe on both native architectures, including the existing
`make check` step, installed it into each full candidate and passed the two
identity/persistence scenarios and 25 functional runtime checks. The full
runtime gates still failed on the three recorded distribution metadata/source
checks. Exact recipe/patch/source witnesses and package receipt joins are sealed
in `stable-alpine-libxml2-regexp-native-paired-20261109.json`. A proposed local
sanitizer diagnostic has **not** been executed and supplies no passing evidence.
Other libxml2 and FFmpeg advisories remain under source-level reconciliation;
missing vectors or tracker scores of zero are not interpreted as safety.

### Additional BusyBox provider: signed native proof in progress

On 2026-11-09 the PO explicitly approved an additional source-pinned, patched
BusyBox provider, preserving applets, signed package ownership and matching
source delivery. The official stable `busybox-1.37.0-r31` reproduces
CVE-2025-60876: a container-local HTTP server observes an injected header when
the wget request target contains raw CR/LF. An ordinary URL succeeds as a control.
The official tracker reports this exact stable source version as `fixed: false`;
its missing CVSS vector/zero score is **unknown severity**, not a safety claim.
Signed stable `dash` and `dash-binsh` explicitly depend on BusyBox, so the tested
transaction cannot simply remove the affected provider. No vulnerability
suppression, removed applet, mixed feed or compatibility shim is approved.

The canonical AMD64 provider build now passes all 913 upstream checks without
additional exclusions. It retains all 304 applet paths, 1,084 settings, the 44
original vendor patches and exact witnesses for 50 touched sources. Only the
three selected runtime packages are installed. The disposable AMD64 runtime
proof passed actual signatures, default-trust denial, an authenticated-control
tampering rejection, exact installed payloads and APK ownership, with temporary
trust removed and runtime trust/repositories unchanged. The bounded runtime
probe rejects all 32 representable C0/space URL bytes, passes five actual local
HTTP transfers and the timezone case. The original official-base failure remains
separate evidence. See `docker/alpine-busybox/README.md` and the builder/parser/
installer/workflow unit tests for executable contracts.

An actual APKv2 negative control reports `BAD signature` under the trusted key;
an unknown key reports `UNTRUSTED signature`. The regression fixture preserves
the real outputs. A parser error or unknown-key error cannot satisfy the patched
package's authenticated-byte negative control. No signature gate is bypassed.

Native run `42555656082` then passed these build/install/runtime checks on both
AMD64 and ARM64, including 913 upstream checks per architecture and three exact
installed control scripts. The independently downloaded results are sealed in
`stable-alpine-busybox-native-paired-20261109.json`; their original-base failures
remain separate evidence.

The subsequent static-source graph binds official utmps 0.1.3.3 and skalibs
2.15.0.0 to their actual signed SDK/static APKs, source commits, upstream inputs,
recipes and ISC notices. Local AMD64 source collection verifies installed SDK
bytes and package ownership before compiling, and retains matching sources.
Source-bound native run `42556901641` subsequently passed both architectures:
913 upstream checks each, the exact official SDK/static-provider/source joins,
delivered-source verification and installed runtime checks. Independently
downloaded wrapper hashes and actual source commits are sealed in
`stable-alpine-busybox-static-native-paired-20261109.json`. Complete
compiler-support/rebuild delivery and independent unfixed-aware advisory
enforcement remain unfinished. Production Dockerfiles are unchanged.

### Full application integration candidate (native execution pending)

`docker/alpine-native/Dockerfile.candidate` composes the freshly built signed
native and BusyBox products with the 74 hash-enforced Python wheels and the
unchanged frontend/backend application. It retains the production identity,
entrypoint, environment, healthcheck and persistence-directory contracts, while
labeling incomplete source/advisory closure explicitly. The normal native
workflow now builds this candidate on both architectures, records its immutable
image identity, and runs the existing bootstrap and runtime gates without
waivers. Runtime evidence is retained even if bootstrap fails, but either
failure still fails the job. RPM/glibc-specific assertions are not silently
removed: their Alpine replacements must be established from actual failures and
equivalent source/behavior checks. No whole-application pass is claimed yet.

This new gate is additive to the signed-component and live original-image
pairing gates. Source closure for the external APK/Python base, independent
unfixed-aware advisory enforcement, final immutable-image scans and MCP
composition/isolation remain required. The production Dockerfiles and protected
publication/deployment workflow are not switched by this candidate.

`verify_alpine_application_source.py` now seals the 1,080 application input files
against the approved baseline. Its only content exception is the existing
`df80c49d` GNU date-format compatibility adaptation, bound to its exact bytes and
recorded baseline vectors; it is not permission for another application refactor.
Backend tests and Docker-excluded generated/cache files are outside that source
map. Local source verification passed. The same guard runs before native builds
so a PR merge checkout cannot silently substitute different application code.

The first full ARM64 candidate in `42649078033` built successfully and passed
both 1000:1000 and 1571:1572 bootstrap/restart/recreation scenarios. It passed 25
runtime checks, including real prober cancellation, encrypted HLS relay/previews,
resdet, timezone, SQLite and verified/refused TLS cases. The full runtime gate
still **failed** on three distribution-specific checks: glibc's getent version
banner, RPM security-package metadata and the RPM source index. The immutable
image/rootfs and original failures are retained in
`stable-alpine-application-arm64-first-runtime-20261109.json`.

The getent check now retains named lookups and adds actual numeric lookup,
missing-user exit/output and official APK ownership checks on Alpine. The
security metadata adapter verifies the exact current owned recipe, installed
file/link bytes and APK ownership instead of asking for nonexistent RPMs.
Local actual musl getent integration passed; fresh full native adapter execution
is still pending. A Python-3.12 AST manifest proves that all 34 original shared
functions retain their original bodies, except the two explicit metadata
dispatch branches. In particular, the original source-integrity gate and every
functional check remain unchanged. The incomplete source index therefore still
fails: no compatibility or source gate has been waived.

The completed first full-candidate run failed on the same three metadata/source
checks on **both** architectures. Both passed the two identity/persistence
scenarios and 25 functional runtime checks; the original raw AMD64 and ARM64
reports are retained, not replaced by the adapter's unit results.

Live immutable-candidate query output plus the actual installed APK database are
now saved as `application/runtime-inventory.json` by the native workflow. The
new `alpine_source_inventory.py` joins the complete query and database sets and
groups exact origin/commit/version subjects. Its output is explicitly an
**unauthenticated source work plan**, never a replacement source gate. Missing
origins/commits remain unresolved; only the observed `.python-rundeps` record
may be a zero-payload virtual package, with its dependency graph retained and
with any file **or directory** payload rejected. Recorded real-data regression
tests cover omissions, additions, duplicate identities, metadata changes,
checksum changes and virtual-package abuse. Actual local full-capture execution
joins all 94 packages and 68 source origins; this local environment is not the
full application candidate and includes build-tool packages.

Full AMD64 Python execution against the pre-repair test-image snapshot completed
with 16,608 passes, six failures, five unchanged skips and two deselections
(84.19% coverage). Five failures correctly rejected the stale resdet helper hash;
the sixth rejected the newly added workflow controls absent from the closed
manifest. The consumer hash is rebound to the actual unchanged-recipe helper;
all nine added workflow `if` controls were reviewed and recorded, with no removed
controls or new `continue-on-error` entries. The targeted repair suite passes;
the full native rerun remains required. This failure is retained, not waived.

Native run `42543103180` subsequently passed **16,614 tests on each architecture**,
with five unchanged skips and two deselections. Both full gates used all 74
unchanged pins, hash-enforced installation and installed-byte verification;
no test exclusions were added. Later Python run `42547435116` and resdet-inclusive
native composition run `42547435177` also completed successfully on both native
hosts at `7545f73d`. These results do not include the later BusyBox replacement
or establish full ECM/MCP runtime/security approval.

### Independent original-image comparison and native ARM numerical correction

`docker/alpine-native/original-baselines.json` seals the retained CI images from
run `42438805097`, including their OCI manifest, configuration and rootfs
identities. Build-input paths in source `ceb96ba86c714b48bca28d4c945e7b741fa45cb7`
are identical to approved application baseline `06cc4bf5a1b9eb1f6ef29f617513d237cda2f531`;
the workflow repeats that git comparison before consuming them.

`prepare_alpine_baseline.py` verified the actual retained ZIPs and OCI identities
for both architectures locally without executing ARM code. Original-only mode
also passed against the retained ARM OCI. The native workflow pairs each
**current** signed APK with its actual original image using 35 codec cases,
four fresh processes per case, five complete framemd5 outputs and six complete
capability listings. Run `42537579406` passed the actual AMD64 historical-package
comparison (142 processes); the corresponding ARM comparison failed with 54
codec-check differences and only `synthetic.c2` differing among the five media
outputs. The immutable failure artifacts remain retained. The separate
historical-package diagnostic cannot approve a current candidate and is now
explicitly opt-in; current signed packages still undergo the mandatory live
original-image comparison on both native hosts. Tests in
`test_alpine_native_workflow.py`, `test_compare_alpine_baseline.py` and
`test_prepare_alpine_baseline.py` enforce wiring and rejection paths; they are
unit/configuration tests, not native compatibility proof. Verified original
transport ZIPs are retained even when a subsequent comparison fails.

`fetch_original_baseline.py` also supports explicitly pinned 14-day retention
mirrors when GitHub reports the one-day primary artifact unavailable (404/410).
Both wrapper and inner original ZIP hashes are mandatory; hash mismatches or
permission failures are not treated as expiry. Actual AMD64 mirror dogfood
forced a recorded primary 404, downloaded the real pinned mirror and recovered
the identical original ZIP hash. OCI/config/rootfs verification remains a
separate mandatory step, not bypassed by transport recovery.

Native ARM investigation `42538393688` tried eight bounded numerical variants
without editing the original recipe or references. Only `fast-selected-pow`
matched **all 70** original-ARM checks; the unchanged `off-selected-pow` retained
54 differences. This isolates contraction semantics, rather than a new tolerance
or replacement libc. The recipe now selects `-ffp-contract=fast` on AArch64 and
retains `off` on AMD64. Patch and complete post-patch source witnesses were
recomputed from the unchanged original archive. Fresh native run `42539263198`
subsequently passed signed-package installation, all 70 recorded-native codec
processes and all 142 live original/candidate processes on **both** architectures,
including complete media outputs. Each APK owns 568 exact files. Independently
downloaded artifact hashes and actual subjects are retained in
`stable-alpine-native-original-paired-20261109.json`. This closes the bounded
codec/media gap, not whole-application/source/security acceptance. Resdet and
later BusyBox work are not included in that run.

`stable-alpine-native-original-arm64-20261109.json.gz` records the actual old-ARM
execution and media results bound to its original OCI identity. It supplements,
never modifies, the original AMD64 recording or fixtures. The architecture-aware
codec and media gates reject using the differing AMD64 bytes as an ARM proxy.
The four other media outputs and all six capability listings are unchanged
across the independently executed original architectures.

All build stages enforce official stable v3.24 main/community repositories and
native architecture. The Python builder remains the pinned Python 3.12 Alpine
base, not Alpine's newer distribution Python. Source/configuration overrides are
not exposed. Compilation of the seven libraries and FFmpeg is networkless after
hash-verified input preparation.

| Provider | Retained contract and implementation |
|---|---|
| Codec2 1.2.0 | Original vendor install patch, all seven modes, baseline symbol surface, explicit codec-local numerical selection and separate initialization of two error accumulators |
| TwoLAME 0.4.0, Shine 3.1.1 | Original source hashes, Shine vendor GCC fix, unchanged bounded MP2/MP3 outputs |
| libxml2 2.13.9 | ABI-preserving `libxml2.so.2`, seven selected security backports with complete pre/post file witnesses and original patches retained |
| Speex 1.2.1 | Original reproducibility/vendor security patches, plus separately owned checked-capacity follow-up described below |
| librsvg 2.63.4 | Baseline version and retained Cargo graph: 352 registry crates and nine workspace packages, offline/locked builds, complete crate/file/lock checksum joins |
| libjxl 0.12.0 | Authenticated baseline SKCMS and original vendor patch; threads/CMS, JPEG transcoding, boxes and original public exports |
| gosu 1.19 | Existing commit/modules and Go 1.26.10, pinned official native Alpine Go builder, static binary and retained Go/module source |
| FFmpeg 8.1.3 | Existing signed source, signer, RTSP patch and all 49 configure arguments; composed owned headers/libraries and explicit GNU iconv header/link selection |
| resdet 2.4.3 / libresdet 3.2.0 | Original commit, source SHA, static KISS FFT, Y4M-only readers, pixel ceiling and checked Y4M multiplier; unchanged builder gains a rehashed offline-cache location |

Recipes, source archives, patches, helpers, build inventories and products are
recorded by each component builder. Receipt statuses deliberately say
`BUILT_API_SMOKE_ONLY`, `BUILT_SECURITY_API_ONLY`, `BUILT_GOSU_ONLY` or
`BUILT_FFMPEG_ONLY`, not final-image acceptance. `package_version` fields in the
security recipe are recipe metadata, **not a claim of installed APK ownership**.

### Source distinctions and Cargo configuration

The original resdet recipe now has an offline stable-Alpine component wrapper.
Local AMD64 execution (`resdet-component-04`) passed the original version/reader,
static KISS FFT and ELF checks, retaining its archive, helper, licenses, actual
configuration and source/runtime inventories. It is required in the next signed
owned APK composition. Native ARM resdet execution and fresh signed-package
installation remain pending; this is not a full-image approval.

The separate `alpine-python.yml` workflow uses each architecture's complete
74-wheel hash-locked selection, verifies installed bytes and runs the unmodified
full backend gate on real native hosts. Preparation passed both complete local
caches and a fresh HTTPS acquisition of all 74 ARM wheels. This workflow adds no
application-test exclusions or publication permissions. Its first native CI
execution is pending.

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

The later [native CI receipt](evidence/stable-alpine-native-ci-20261108.json.gz)
retains verified artifacts from run `42522607374`, PR merge source
`3c21293a507ec68799815b0590ddba64de46fb9d` (head `fd087770`). Its ARM64 job
completed all seven providers, SDK-bound signed FFmpeg and gosu. Both
architectures passed all 70 codec processes and the faithful JXL API smoke.
Complete delivered runtime/source/SDK maps, hashes, modes and symlinks were
independently checked. The AMD64 job was cancelled during FFmpeg compilation by
the next push: no completed AMD64 FFmpeg/gosu receipt is invented. This receipt
predates signed APK installation and installed-media checks, and is not an
independent old-ARM-baseline or final security approval.

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
also includes untrusted-key and damaged-package denials. The subsequent
[signed AMD64 receipt](evidence/stable-alpine-signed-amd64-20261108.json.gz)
now proves a complete composite APK install in a clean pinned Alpine Python
image: all 564 files retain exact content and APK ownership, runtime key and
repository inventories remain unchanged, and both signature-denial controls
pass. Its FFmpeg source was freshly compiled against independently rehashed CI
SDK exports in a disposable older builder, **not an exact current Dockerfile
build**. Current native CI must still prove the full Dockerfile path on both
architectures. External APK/source attribution and advisory denial remain
separate unfinished gates.

`verify_alpine_ffmpeg.py` supplements the existing rare-input gate with hashes
of all five complete decoded framemd5 outputs and six complete capability
listings from the original actual AMD64 image. The reference passes against
that immutable baseline; the known stock-JXL hash is explicitly rejected. The
actual signed AMD64 installation matches all five complete decoded outputs and
all six complete listings. It runs after installation, not just against a
builder SDK. Matching
these recorded outputs on ARM is still not independent old-ARM-baseline proof.

The same receipt records a separate real native AMD64 build of the unchanged
resdet script/source/configuration. The original version/Y4M-only reader contract,
existing over-pixel-ceiling rejection and four deterministic CLI outputs match
the actual baseline exactly. This is not ARM proof or final package/application
integration of resdet, which remains required.

### Current native CI: package and installed-media gates passed

Run **`42524783410`** passed both native jobs for head `29f4075f`, checked out as
PR merge source `47093e5751efffc3233ccde030d5ad144d096ea3`. The
[durable package CI receipt](evidence/stable-alpine-native-packages-ci-20261108.json.gz)
records all five complete component builds, SDK-bound FFmpeg, 70 fresh codec
processes, actual signed installation, both signature-denial controls, unchanged
runtime trust/repositories, and all five complete framemd5 / six capability
listing comparisons on **both** architectures. Both downloaded artifact ZIP
digests and complete component runtime/source/SDK maps, modes and symlinks were
independently checked, along with the component-to-package receipt joins.

Each current CI package owns **566** files. The older local proof owns 564:
the new `Dockerfile.packages` and `ffmpeg-baseline.json` were subsequently added
to Codec2's complete delivered recipe directory. The receipt preserves that
source-only difference; no file was omitted to force an old count, and no media
golden or tolerance changed.

This completes the bounded native owned-package gate, **not the migration**.
Production Docker/MCP/source/SBOM integration, complete external APK and wheel
source/advisory attribution, native application and old-ARM-baseline comparisons,
identities/recreation/persistence/trust/isolation and final unsuppressed image
scans remain required. No protected merge, publication or deployment occurred.

### Musllinux GNU runtime correspondence

The complete wheel archive survey is **14 GNU-bearing wheels, 18 member copies,
12 distinct repaired ELF objects** (not the earlier Beads note's 16 wheels).
All twelve now have whole-byte transformation matches, with no target bytes
edited or normalized. Eight GCC 14.2 objects / twelve copies join to signed
Alpine v3.22 `libgcc`/`libstdc++` 14.2.0-r6 originals; four GCC 12.4 objects / six
copies join to original ELF files in digest-verified official
`ghcr.io/rust-cross/rust-musl-cross` layers. The initial PyPA musllinux 1.1 image
investigation did **not** contain those GCC 12.4 originals and is retained as a
negative control, not misattributed as their provider.

The replay uses hash-pinned comparison-only patchelf 0.17.2 or 0.19.3 as required
by the observed full-byte outputs. RapidFuzz's auditwheel 6.6 profile performs
SONAME then NEEDED without RPATH; greenlet's 6.7 profile adds `$ORIGIN` between
those operations. Cryptography and hypothesis match with patchelf 0.19.3.
These controls prove correspondence, not the exact producer command history.
Original mismatches, failed controls and incomplete experiments remain retained.
The [GNU correspondence receipt](evidence/stable-alpine-musllinux-gnu-20261108.json.gz)
retains all ten controls, raw immutable OCI metadata and layer joins, original
APK authentication/public verification keys, candidate-source hashes, and the
actual repository verifier's passing binary / failing source receipts.

The curated map is `docker/python-third-party/artifact-index/musllinux-gnu.json`.
Its loader and both comparison tool binaries are hash-pinned by
`scripts/verify_musllinux_gnu.py`; tests are in
`backend/tests/unit/test_verify_musllinux_gnu.py`. In an isolated native AMD64
comparison run it rehashes all **96** selected wheel archives and rejects added
or missing wheels, including changes that leave the 18 known GNU members
untouched. This is still only GNU correspondence, not other bundled-component
source clearance. The [complete-selection receipt](evidence/stable-alpine-musllinux-gnu-selection-20261108.json.gz)
binds the current helpers/map and retains the earlier narrower receipt unchanged.
In an isolated native AMD64
comparison container, prepare a hash-named cache of the four original APKs, two
rust-cross toolchain layers and two tool wheels named in that map (never install
them in a runtime). Then run:

```sh
python scripts/prepare_musllinux_gnu_inputs.py \
  --manifest docker/python-third-party/artifact-index/musllinux-gnu.json \
  --cache-dir /inputs/cache --output-dir /proof/prepared
python scripts/verify_musllinux_gnu.py \
  --manifest docker/python-third-party/artifact-index/musllinux-gnu.json \
  --wheel-dir /inputs/wheels --original-dir /proof/prepared/originals \
  --tool-dir /proof/prepared/tools --work-dir /proof/binary-work \
  --output-file /proof/binary-receipt.json --binary-only
```

Preparation rechecks compressed APK control/index and payload-datahash joins,
OCI compressed/diff-id hashes, exact regular archive members and tool bytes.
The earlier default verifier, **without** `--binary-only`, correctly failed
source closure after preserving the twelve successful replays. Those denials
remain immutable historical receipts. It cannot accept source status labels
without independently hashing a complete delivered source tree. The subsequent
bounded GNU source closure below is not final production source clearance.

GCC 14.2 source archive and all 40 local source inputs match the Git-object-
verified APKBUILD at `fbf60319be3bbaf6dd32ef55cc6fb7189e05c266`. GCC 12.4's
official OCI layers retain exact `config.mak` and the unpinned clone command, but
delete the checkout and log. Source candidate `227df8b99103f9c59f6570babf892978e293082f`
and its hash-matching upstream archive were retrieved; **the original build
recipe revision is not yet authenticated**. Corresponding source is now proven
independently below, without pretending to recover that removed revision.
Cryptography ARM's raw
`NotpineForGHA` producer PURL is preserved separately from the independent
original-byte proof. None of these results clears source delivery, other wheel
components/crates, external APK advisories, image SBOMs or final security scans.

### GNU source reproduction and actual delivery

The [source-reproduction receipt](evidence/stable-alpine-gnu12-source-reproduction-20261108.json.gz)
retains both complete source-build transcripts, all eight hash-checked upstream
inputs, pinned musl-cross-make recipe/configuration and the failed controls.
On native AMD64 the complete source-built `libgcc_s.so.1` equals official
rust-cross original `f685abf10f5e598c5fe7869915bfe476984bb73bee0fbf11a9c2bbb4ebd8a32e`.
The ARM-target build initially differs in 129 bytes, confined to version tables;
that candidate is **not accepted by section equality**. GCC's upstream
`mkmap-symver.awk` iterates version roots in an awk-dependent order. With mawk,
the source-generated map and unchanged source-built objects/compiler/linker
produce the complete original ARM ELF
`0bf60adc8e990861bcc846bdb16ce7e31ba0d82c01be2d6338e41ff14a6b6b52`.
BusyBox awk and gawk controls differ and remain retained. No version-label edit,
ELF normalization, target binary substitution or new tolerance is used.

This is a native AMD64 compiler host and an ARM **cross-target artifact** proof,
not ARM application execution. The original publisher checkout identity stays
unknown; independently reproduced corresponding source is labeled
`REPRODUCED_CORRESPONDING_SOURCE`, distinct from signed-provider source identity.
The exact generated ARM linker-map input and reproduction instructions are
retained in `docker/python-third-party/gnu-source-assets/` along with both pinned
recipe archives, original configurations and verbatim GCC license/exception texts.

`collect_musllinux_gnu_sources.py` delivers the reviewed **27** source archives,
recipes, notices and proof files for the two GNU families, without installing
anything, unpacking archives or upgrading a candidate's source status. After the
input-preparation command above, run:

```sh
python scripts/collect_musllinux_gnu_sources.py \
  --manifest docker/python-third-party/artifact-index/musllinux-gnu.json \
  --cache-dir /inputs/gnu-source-cache --output-dir /proof/sources \
  --receipt-file /proof/source-copy.json
python scripts/verify_musllinux_gnu.py \
  --manifest docker/python-third-party/artifact-index/musllinux-gnu.json \
  --wheel-dir /inputs/wheels --original-dir /proof/prepared/originals \
  --tool-dir /proof/prepared/tools --work-dir /proof/source-work \
  --output-file /proof/source-receipt.json --sources-dir /proof/sources
```

The cache must contain every reviewed `source_files[].sha256` input named in the
curated map; neither collector nor verifier downloads or trusts missing inputs.
The actual offline, read-only/nonroot stable-Alpine run passed the default
`GNU_SOURCE_ATTRIBUTED` gate: all 96 whole wheels, 18 GNU members, 12 complete ELF
replays and 27 delivered source/recipe/notice/proof files. A separate delivery
copy with only `COPYING.RUNTIME` changed fails after the same twelve replays.
The [delivery receipt](evidence/stable-alpine-gnu-source-delivery-20261108.json.gz)
binds the current curated map/helpers, complete delivered inventory, successful
default gate and real corrupted-notice denial. Original earlier source denials
and the 129-byte ARM candidate remain preserved in their separate receipts.
This is only GNU subset closure. Production-image delivery/source consumers,
other wheel components/crates, external APK attribution/advisories, SBOMs and
the complete application/native-ARM/security matrices remain unfinished.
