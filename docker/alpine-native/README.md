# Incremental owned Alpine library builds

Tracks `enhancedchannelmanager-g3vd3`. The PO approved maintaining pinned,
reviewed Alpine-native sources/backports for seven library providers: Codec2,
TwoLAME, Shine, libxml2, librsvg, Speex and libjxl. Separate builders also build
the existing signed FFmpeg and gosu contracts. Native hosted AMD64/ARM64 CI is
in `.github/workflows/alpine-native.yml`. Production images remain unchanged;
component builds do not complete final-image packaging/source/security work.

## Inputs and maintenance

- `codecs.json` records exact upstream URLs, SHA256, sizes, configuration and
  ordered patches. Its full bytes are pinned in `scripts/build_alpine_codecs.py`;
  changes require updating/reviewing both. CLI inputs select locations and native
  target only, not versions, flags or alternative sources.
- All three upstream archives were downloaded and independently matched against
  the exact signed baseline source-RPM payloads. Live upstream latest-release
  responses on 2026-11-08 still reported these three versions. The recorded
  payload fixture is `backend/tests/fixtures/alpine/codec-sources.json`.
- Codec2's install-binaries patch and Shine's GCC 15 prototype correction are
  retained byte-for-byte from those authenticated payloads. Each application
  requires exact whole-file before/after witnesses and zero fuzz. The Shine
  artifact contains mixed CRLF: its `.gitattributes` entry prevents byte-changing
  text normalization; the recipe verifies its original SHA256.
- No RPM binary, rolling feed, glibc compatibility library or alternative codec
  implementation is installed. Shared standard-library fetch/extract/ELF/hash
  utilities are reused from `build_native_runtime.py`; none of its SUSE package
  installation/build entrypoints is invoked.
- Keep ordinary stable APK updates intentional. This proof builder records its
  actual APK inventory/compiler, but **does not yet retain/authenticate the full
  builder APK/source closure** or make mutable APK feeds reproducible.

## Disposable native x86 proof

Use a new content-addressed cache directory with the three original archives
named by their `sha256` in `codecs.json`. Independently verify their bytes against
the reviewed pins before populating it. The builder rehashes cache objects again;
invalid existing cache bytes fail rather than falling back to a fetch.

```sh
docker buildx build --platform linux/amd64 --progress plain \
  --build-context owned-cache=/absolute/owned/cache \
  --file docker/alpine-native/Dockerfile.codecs \
  --output type=local,dest=/absolute/new/proof-output .
```

Use `linux/arm64` only on an actual native ARM runner. The helper rejects a
machine/target mismatch and wrong release; this does not independently detect
emulation. Native runner placement remains an operational requirement.

`Dockerfile.codecs` checks the repository policy before installing build tools.
Compilation runs with `--network=none` from the supplied original archives. The
helper also checks actual stable repository files, disjoint/new work paths,
metadata versions, architecture, SONAME, musl-only direct dependencies and absence
of RPATH/RUNPATH. The only direct dependency observed in all three x86 products is
`libc.musl-x86_64.so.1`. Additional providers require explicit review, not an
expanded permissive match.

| Output | Scope |
|---|---|
| `root/` | Selected shared-library families and original COPYING notices only; no compiler or unrelated system tree |
| `sdk/` | Builder-only installed headers, pkg-config files and optional tools; never copy this whole tree into the runtime |
| `sources/` | Original archives, retained patches and actual helper/config/smoke recipe |
| `smoke/` | Deterministic input's Codec2 encoded/decoded vectors, TwoLAME MP2 and Shine MP3 for paired comparison |
| `build-output.json` | `BUILT_API_SMOKE_ONLY`, actual inventory/compiler, patched-source/file hashes and output/source/vector identity maps; **not** a runtime PASS |

TwoLAME's optional libsndfile frontend is not built. The baseline runtime had
`libtwolame0`, not that CLI or libsndfile. Codec2/Shine standalone programs remain
builder-only. This retains the baseline library integrations; final FFmpeg
configuration and required media behaviors must still be demonstrated separately.

## Verification and historical differences

`test_build_alpine_codecs.py` covers recipe/patch/smoke tampering, source-cache
corruption, repository and platform mismatch, unsafe locations, patch witnesses,
glibc/wrong-architecture dependencies, runtime paths and meaningful smoke output.
The glibc-denial regression also failed against an isolated dangerous mutant that
allowed `libc.so.6`; it is not merely a source-text assertion.

Two real x86 offline builds passed bounded public-API operations. Their vectors
match one another. The same C fixture against the actual `06cc4bf5` baseline
image produced byte-identical Codec2 encoded bytes, TwoLAME MP2 and Shine MP3.
However, **Codec2 decoded PCM differs at two of 2,560 signed 16-bit samples, by
one unit each**. An independent repeated baseline run is byte-identical to its
first run. Actual baseline loaded library paths/hashes were checked through
`/proc/self/maps`, not assumed from filenames.

That historical discrepancy is now corrected by source-level numeric selection,
with unchanged goldens: seven modes × five corpora and baseline-stream decoding
match exactly on native AMD64. `scripts/run_alpine_codec_matrix.py` repeats 70
fresh native processes against the pinned recorded corpus. ARM equality to that
AMD64 corpus is not independent old-ARM-baseline proof. No normalization or
tolerance is permitted. Earlier failed receipts above remain preserved.

Libjxl retains authenticated baseline 0.12.0/skcms sources, its original vendor
patch, JPEG transcoding and boxes. It checks all 105+9+1 baseline public exports,
original RGB24 bytes, JPEG reconstruction and native thread creation. FFmpeg
requires all three evidence contexts (`codec-evidence`, `security-evidence`,
`jxl-evidence`) and rehashes runtime products, sources **and SDK compiler inputs**.
Old receipts without SDK inventories cannot satisfy the new composition gate.

Prepare all nine remote inputs with `scripts/prepare_alpine_native_cache.py`.
Build JXL via `Dockerfile.libjxl` with a `jxl-cache` context before FFmpeg. For
the complete reproduction sequence, selected security backports, approvals and
remaining gates see [owned native design](../../docs/design/stable-alpine-owned-native.md).

`Dockerfile.packages` adds a separate build-only signed APK and offline
installation proof using all five component contexts. It exports only the APK,
public key and receipts, never its private key. Runtime trusted keys/repositories
must remain unchanged; delivered files must retain exact bytes and APK ownership.
Its installed FFmpeg check compares all five complete rare-input outputs and
six capability listings without replacing the existing production tests.
This package proof does not cover external APK source/advisory closure or the
complete application, persistence, trust-consumer and MCP-isolation matrix.

Native CI run `42524783410` passed this complete component/package/media path on
both hosted architectures. Its [durable receipt](../../docs/design/evidence/stable-alpine-native-packages-ci-20261108.json.gz)
records verified artifact identities and all installation/behavior gates. This
does not mark the production migration or independent old-ARM-baseline matrix
complete; those remaining gates are listed in the design document.

Detailed raw commands, downloads/signatures, both build records and paired/repeat
receipts: `/tmp/opencode/ecm-alpine-owned-native-20261108/`. Complete scanner,
final corresponding-source closure and independent review remain outstanding.
