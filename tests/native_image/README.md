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
the historical 22-check suite, not the current **26 required checks**. The stock
Wolfi candidate is rejected for publication because of source correspondence
and missing rare-format support. Its old green report is not acceptance of the
new guard or release approval. A newly selected source-closed candidate must
pass the complete current suite on both architectures, plus separate whole-image
scanning and source/notices verification. No new image was available for this
coverage extension, and none was pulled or rebuilt.
