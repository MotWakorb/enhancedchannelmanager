# Native image compatibility gate

Run from the repository root against an **already-built full image ID**:

```sh
python3 scripts/native_image_gate.py --image sha256:<64-lowercase-hex-digits> --report /path/to/native-gate.json
```

The default runs both bootstrap and native checks. `--phase runtime` or
`--phase bootstrap` selects a diagnostic subset; the report always names its
scope. This does not build an image, resolve a tag, pull, publish ports, install
dependencies, or inspect an existing application's configuration.

Each container uses `--pull never --network none`. Test code is mounted read-only.
Runtime media, TLS certificates, and AES keys are synthetic. Bootstrap uses fresh,
uniquely named config volumes for `1000:1000` and `1571:1572`. It checks PID 1's
actual credentials, file ownership, `/run/ecm` mode 0700, executes the configured
absolute-venv healthcheck, then verifies restart and recreation persistence. No
credentials or credential digests are printed. Only this invocation's containers
and volumes are removed. Cleanup failure also fails the gate.

The runtime suite includes the real ECM HTTP relay and MPEG-TS preview command.
Its fixture replaces only upstream HTTP response bytes, following existing
`backend/tests/security/test_stream_outbound.py` support. No production SSRF
validation or network policy is relaxed. Separate AES HLS probe and production
preview checks require decoding real `/resource/<128-bit-token><public-suffix>`
URLs. Known playlist/segment suffixes are retained; an unrecognized key suffix
stays opaque. Provider paths, queries and credentials must not escape, forged
suffixes must not fetch upstream, and upstream UA/auth and cleanup ownership are
checked. The fixture does not inject permissive FFmpeg options. Raw DASH is retained as
an independent decoder check. ECM's pre-existing lack of MPD resource rewriting
is a separate relay gap, not a migration regression.

Required `production_prober_ts` and `production_prober_aes_hls` checks invoke the
real `StreamProber._run_ffprobe_once` and `_parse_ffprobe_data` methods. A
forwarding observer asserts the production `-timeout 5000000`, protocol whitelist,
and JSON/stream/format arguments without changing the spawned command. Synthetic
upstream requests must retain the selected User-Agent, all acquired responses
must close, and parsed metadata must contain 128x72 H264/AAC. The prober is
constructed with `__new__` plus its method's timeout only. No database/client
constructor, save operation, persistence report, or retry-scheduling claim is
made.

`production_prober_cancellation` stalls an actual relay response body, waits for
the owned child to reach that response, then cancels the real prober method.
The gate requires a recorded production kill, awaited reap, return code -9, and
closed upstream responses. Fallback cleanup is bounded, handles only processes
returned to this invocation, and cannot substitute for passing production
cleanup. There is no process-table search or global kill. These checks retain
the synthetic upstream seam and do not claim DNS/redirect-policy coverage.

`software_input_format_support` reads actual `ffmpeg -decoders`, `-demuxers`, and
input `-protocols` inventories. It requires Codec2 (`libcodec2`), JPEG XL (`libjxl`),
SVG (`librsvg`), Sonic registration, `libgme`/`libopenmpt` demuxers, common software
video/audio codecs, and the normal HTTP/TLS/crypto/UDP/RTP/RTMP/internal transports.
Codec descriptors, configure flags, output-only protocols, or unrelated hardware
implementations do not satisfy missing input registrations. Native GSM/GSM-MS
implementations are accepted in place of libgsm names; reviewed v308/v408/v410
MOV-tag-to-rawvideo representation changes are not treated as general input loss.
Rawvideo itself remains required. This is not a pixel-equivalence test or a
promise of arbitrary GPU, device, filter, encoder, or CLI feature parity.

Sonic is registration-only for the preserved FFmpeg 8.1.3 capability contract.
Its baseline decoder is experimental and is not usable by default merely because
it is registered. The gate does not enable `-strict experimental`, claim working
baseline Sonic decoding, or use that limitation to waive other input formats.

Required `synthetic_rare_input_decode` goes beyond registration: it probes and
decodes five original, owned fixtures from the 4,788-byte text-only
`rare_inputs.json`. The loader pins the exact five names, byte sizes, SHA-256
values, generator provenance and expected metadata; substituted/unknown inputs
or altered expectations fail before a decoder runs. The check requires:

| Input | Probed format/codec | Required metadata |
|---|---|---|
| `synthetic.c2` | codec2 / codec2 | mono, 8000 Hz |
| `synthetic.jxl` | jpegxl_pipe / jpegxl | 16x16 video |
| `synthetic.svg` | svg_pipe / svg | 16x16 video |
| `synthetic.vgm` | libgme / pcm_s16le | stereo, 44100 Hz |
| `synthetic.mod` | libopenmpt / pcm_f32le | stereo, 48000 Hz |

Each input runs `ffprobe -show_streams -show_format -of json` and a real
`ffmpeg -i <fixture> -t 0.2 -f framemd5 -` decode, with bounded subprocesses and
generated files only. Nonzero exits fail even with plausible stdout. Valid
positive-duration, positive-size frame rows and matching decoded media kind,
dimensions or audio rate/layout are required for **every** fixture. Empty,
header-only or zero-size output cannot pass. Checksums must be well formed but
are not compared across library versions: there is no claim of bit-identical
PCM/audio, pixels, or arbitrary rare-media compatibility. Experimental decoding
is not enabled; these five fixtures do not include Sonic.

### Owned fixture generation provenance

The recorded generator is `compatibility-fixtures/generate_and_check.py`, SHA-256
`269b1801fff716faf591ecc10f6794a9b1a5af158a33230284320114b4818ff0`.
`rare_inputs.json` records its immutable published Debian baseline image and
`bdef595bafc40128e7df49b8dcb55dde98e9aee2` commit, not a mutable tag. Codec2 and
JPEG XL were generated by that baseline's FFmpeg with these arguments (plus
`-hide_banner -loglevel error -y`), into an owned output directory:

```text
-f lavfi -i sine=duration=0.3:sample_rate=8000 -ac 1 -c:a libcodec2 -f codec2 synthetic.c2
-f lavfi -i color=c=0x2468ac:s=16x16:r=1 -frames:v 1 -c:v libjxl synthetic.jxl
```

The other three complete original recipes are:

```python
import struct
from pathlib import Path

Path("synthetic.svg").write_text(
    '<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16">'
    '<rect width="16" height="16" fill="#2468ac"/></svg>\n'
)
data = bytes([0x50, 0x80, 0x50, 0x10, 0x50, 0x90]) + bytes([0x62]) * 60 + bytes([0x66])
header = bytearray(64)
header[:4] = b"Vgm "
for offset, value in {4: len(header) + len(data) - 4, 8: 0x150, 12: 3579545,
                      24: 44100, 36: 60, 52: 12}.items():
    struct.pack_into("<I", header, offset, value)
Path("synthetic.vgm").write_bytes(header + data)
mod = bytearray(1084 + 1024)
mod[:18] = b"ECM synthetic test"
mod[950] = 1
mod[1080:1084] = b"M.K."
Path("synthetic.mod").write_bytes(mod)
```

The exact recorded bytes are retained as base64, not binary test files. No
private/provider payload, downloaded song, or third-party artwork is used.
The immutable baseline has passed this check with five nonempty decode results;
that establishes baseline behavior, not final-candidate or full-gate approval.

`checks.json` is the ordered required-check contract. Missing binaries, a nonzero
exit, malformed/blank JSON, an incomplete/contradictory result, or wrong image or
bootstrap identity fail closed. Failure emits no success report on stdout;
`--report` preserves synthetic diagnostics and any completed bootstrap evidence.

Version, license notices, readers, and resdet's 8,847,360-pixel ceiling are
recorded/checked. Both the default system SSL context (including inherited
`SSL_CERT_FILE`) and explicit certifi context must exclude the reviewed emSign
G3 DER fingerprint while retaining the unaffected G1 positive control. These
checks run on each image architecture; they do not infer trust from versions.
**This is not a vulnerability scan or CVE clearance**, and a
successful run proves only the tested image architecture. Whole-image scanning
and separate amd64/arm64 runs remain required by the release pipeline.

Host-wrapper self-tests (Docker CLI fixtures, not real compatibility proof):

```sh
.venv/bin/python -m pytest --no-cov backend/tests/unit/test_native_image_gate.py
```

The prober self-tests also exercise the actual backend method/relay/parser and
cancellation orchestration using owned Python stand-ins for the native binary.
Those unit stand-ins are never used by the real image gate and do not establish
native-decoder or final-image compatibility.

The older 2026-10-23 amd64 Wolfi working image (`sha256:036da469…977e7e`) passed
the historical 22-check suite, not the current **28 required checks**. The stock
Wolfi candidate is rejected for publication because of source correspondence
and missing rare-format support. Its old green report is not acceptance of the
new guard or release approval. A newly selected source-closed candidate must
pass the complete current suite on both architectures, plus separate whole-image
scanning and source/notices verification. No new image was available for this
coverage extension, and none was pulled or rebuilt.

## Final-image source and artifact integrity

Required check 28, `native_source_and_artifact_integrity`, checks the running
image's fixed delivery paths, not a source-builder's installed package graph:

The runtime receipt retains the checked RPM source index, native-build record and
`native-sources.json` object, including their **raw-file** SHA-256 hashes, under
`source_bindings`. `native_sources_sha256` must equal the producer's
`source_files_sha256`; it is not a canonical/reformatted JSON digest. The bound
`ffmpeg_config_log` retains the actual complete text, path, hash and byte size,
matching its source-index entry. This is compiler-argument evidence for **manual**
review of the non-`CHECKED` condition, not an automatic keyword verdict: configure
diagnostic/test mentions of `CHECKED` do not establish selected compiler flags.
This preserves the actual
per-architecture source/package/artifact correspondence for advisory review in
CI's 14-day evidence artifact; it is not an advisory disposition or attestation.

- `/usr/share/ecm/sources/rpm/index.json`: exact image architecture, canonical
  package/source sets, pinned SUSE/Packman fingerprints, nonempty complete source
  records and every full archive's size/SHA-256. Its seven-column package set
  must equal a fresh query of the **running image's RPM database**. Only correctly
  shaped public-key rows are excluded. Duplicates, omissions, extra archive
  files, unsafe paths and symlinks fail.
- `/usr/share/ecm/provenance/native-build.json`: producer PASS, architecture,
  frozen recipe/source identities and every copied owned artifact's actual
  hash, mode or symlink target. FFmpeg, ffprobe, gosu and the seven FFmpeg shared
  library families are required. The checked-in recipe checksum binds FFmpeg
  8.1.3, the accepted ordered patch identities/before-and-after source witnesses,
  configure assets, gosu modules and Go compiler identity without introducing a
  second recipe schema. Producer program records must match the delivered files.
- `/usr/share/ecm/provenance/native-sources.json`: `sources/...` paths map to
  `/usr/share/ecm/sources/owned/...`; all source/recipe files must be indexed and
  hash/size-match, including the build helper, patches and patched-source index.
  The sole reviewed upstream alias, `gosu/.dockerignore -> .gitignore`, is allowed
  only at that exact path with that exact relative target, resolving to the
  contained indexed regular `gosu/.gitignore` with matching hash/size. The producer
  omits aliases from its regular-file index, so only this validated alias is
  omitted from the regular-file set comparison. All other source aliases,
  directory links, escapes and omissions remain rejected; RPM, Python and recipe
  paths gain no general link allowance.
- `/usr/share/ecm/sources/resdet/resdet.tar.gz`: full archive SHA-256 must equal
  the **delivered** `native-dependencies.json` pin. The actual pinned build script
  and SBOM/config helper must accompany it. Existing resdet behavioral checks
  remain independent.
- `/usr/share/ecm/sources/python/` and
  `/usr/share/doc/ecm/python-third-party/`: the reviewed source/notices
  `SHA256SUMS` manifests and all their named files must be present and match.
  The manifests themselves are pinned, so deleting entries cannot produce a
  vacuously successful check. The actual source texts remain accessible.
- `/usr/share/doc/ecm/python-third-party/artifact-index/linux-{amd64,arm64}.json`:
  select and verify the approved index for the **running** architecture, bind its
  lock hash to `/app/requirements.txt`, and require the 74 installed distribution
  names/versions using `importlib.metadata`. All 22 native distributions must
  have matching installed METADATA and every indexed native file must hash-match
  within `/opt/venv/lib/python3.12/site-packages`, with a matching 64-bit
  little-endian ELF machine (62/x86-64 or 183/AArch64). The complete on-disk ELF
  set must equal the approved inventory: 39 on amd64, 36 on arm64. Duplicates,
  empty/reduced inventories, unexpected ELF files and symlink escapes fail.
  Only hashes/ELF headers/metadata are read; extensions are never imported by
  this check and no network access or wheel downloads are needed. The recorded
  archive hashes bind approved attribution, not a fresh archive download check.

`filesystem_name_search` adds receipt-bound **name-only evidence**, not a 29th
check or an advisory disposition. It traverses the complete running-image rootfs,
including `/app`, `/opt/venv` and copied/unowned paths, excluding and recording only
`/proc`, `/sys`, `/dev` (virtual filesystems) and `/native-image` (external fixture
mount). It records nonzero path count, deterministic path/type/link-target digest,
case-insensitive selectors, matching paths, all symlink names/targets, successful
completion and an empty error list. Directory symlinks are never followed; any
scandir/stat/readlink error fails check 28 instead of returning partial evidence.
Selectors cover `cjson`, `libsndfile`/`sndfile`/`sf_open`, Perl-prefixed names,
`Archive/Tar.pm`/`Archive/Tar/` and `.pm` providers, and `libtiff`/`tiffcrop`.
Both path names and symlink targets are searched. No archive payload or arbitrary
file/binary contents are searched or opened. Matches are data for review (including
false positives such as syntax-highlighter filenames); empty matches are **not
universal static-code absence proof**. Existing shared FFmpeg configuration and
approved native-wheel hashes retain their separate, bounded attribution scope.

The helper streams hashes; it does **not** extract or parse RPM/tar payloads,
execute delivered sources, fetch anything, or repeat GPG verification. Signature,
header and complete-payload authentication remain the trusted source-builder's
responsibility. This is delivery/correspondence proof, **not** an OS-scanner
clearance for custom binaries or a replacement for advisory review. ARM-specific
wheel attribution now has its own independently audited expected index; a run on
x86 neither executes ARM binaries nor proves a future ARM image matches it.

Checksum pins in `source_checks.py` are reviewed delivery inputs, not values
learned from the image being tested. The notice pin covers the complete sorted
556-entry manifest, including the six added ARM metadata JSONs; regenerate the
real `notices/SHA256SUMS` before building. Future approved recipe, wheel-index or
source/notice changes require updating the corresponding pin and focused tests.

Focused temp-tree tests use compact real collector projections plus explicitly
synthetic owned archive/artifact bytes; they are not final-image acceptance:

```sh
.venv-crypto/bin/python -m pytest --no-cov backend/tests/unit/test_native_source_delivery.py backend/tests/unit/test_native_image_gate.py
```
