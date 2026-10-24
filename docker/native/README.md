# Maintained native builder inputs

`scripts/build_native_runtime.py` is a standard-library Python builder, intended
only for a disposable official Tumbleweed builder stage. It installs builder RPMs.
It is **not** an application entrypoint or a host provisioning command.

## Reviewed identities and fail-closed behavior

* FFmpeg 8.1.3 full upstream archive, detached signature and exact release key.
* One ordered, byte-pinned upstream RTSP redirect-limit patch, with exact signed
  8.1.3 preimage and patched postimage witnesses. The original patch header and
  notices are retained. No exploit or adversarial network test is part of this
  builder. Fuzz is disabled; exact whole-file witnesses bind accepted offsets.
* Gosu 1.19 at commit `6456aaa0f3c854d199d0f037f068eb97515b7513`, with
  `moby/sys/user v0.4.1` and `x/sys v0.44.0`, using fixed Go **1.26.10** and
  `GOTOOLCHAIN=local`. Compiler version, modules, vendor/source and Go BSD/std
  notices are verified/retained, rather than substituting a vulnerable compiler.
* Architecture-specific Packman x264/x265 devel/runtime and HDR10+ inputs plus
  their exact matching public source RPMs. Binary hashes, isolated-key RPM
  signatures, architecture and SOURCERPM headers are checked before installation.
  No Packman repository is enabled; all other RPMs come from GPG-enforced OSS.
* Fixed configure arguments retain the required software inputs, Sonic's existing
  default experimental strictness and Hap/snappy. No nonfree flags, physical
  ALSA/Pulse/sphinx device integrations or application flag changes.

The CLI accepts **locations and target architecture only**, not source URLs,
checksums, module versions, keys, patches, compiler flags or configure overrides.
A cache is merely content-addressed storage: each cached object is rehashed and
all signatures/identities are checked normally. Invalid cached bytes fail; they
do not silently authorize fetching/executing another input.

## Stage interface for the parent-owned Dockerfile

Bootstrap Python 3.12 from the official, GPG-checked architecture-specific OSS
repository in the pinned base. Copy this directory plus the helper, then run:

```sh
python3.12 /opt/native/build_native_runtime.py \
  --manifest /opt/native/manifest.json --arch amd64 \
  --work-dir /opt/native-work --output-dir /opt/native-output
```

Use `arm64` only on a matching native CI runner. Its approved, pinned official Go
SDK is builder-only; native ARM execution still requires CI verification.
No source-identity build ARG/environment variables
are needed or accepted. The optional `--cache-dir` contains files named by their
reviewed SHA256; it changes storage, not input identity. Work/output directories
must be new, distinct and non-nested. Compilation is bounded to four jobs.

Successful output:

| Path | Contract |
|---|---|
| `root/` | Only owned products: FFmpeg/ffprobe/gosu, seven FFmpeg shared-library families, notices and loader config; never a copy of system `/usr` |
| `runtime-packages.txt` | Exact installed epoch/version/release/architecture of actual recursive ELF RPM providers |
| `runtime-rpms/` | Only selected local Packman RPMs required by that actual graph; HDR10+ is not copied merely because it was a builder dependency |
| `build-output.json` | PASS only after version/config/architecture checks and final owned-file/symlink hash binding; includes helper/manifest/source/patch/compiler/module/RPM/ELF provenance |
| `sources/` | Original FFmpeg/archive/signature, patch/key/config/helper recipe, matching codec SRPMs, full resolved gosu source/vendor/go.mod/go.sum and Go notices |
| `source-files.json` | Retained regular source-file hashes; source-closure policy still belongs to the separate collector/gates |

The clean runtime stage installs the checked local `runtime-rpms` first, then
exact packages from `runtime-packages.txt` through the same official OSS feed.
The parent separately supplies Python/application packages, shadow/GNU coreutils,
bash/timezone/CA and resdet. Copy `root/` onto `/`, run `ldconfig`, and retain
`build-output.json` and source evidence for the parent's canonical provenance
binding. Keep compiler/build dependencies out of the runtime. This README is an
interface, not production-Dockerfile or CI integration.

The source-built FFmpeg result's `ffmpeg.patches` records the applied patch,
actual postimage and apply log; `ffmpeg.patched_source_files_sha256` binds the
full pre-configuration patched-source file index. Final owned products are in
`owned_artifacts`, relative to `root/`. The parent's final-image verifier must
compare the copied executable/library hashes; build-stage PASS is not that gate.

## Current architecture readiness — do not infer equivalence

Both architectures' five Packman binaries and two matching SRPMs were actually
downloaded, hashed and signature-checked. Their revisions differ:

| Architecture | x264 revision | x265/HDR revision | Fixed Go availability |
|---|---|---|---|
| amd64 | `1699.1.pm.18` | `1699.4.pm.27` | OSS Go 1.26.10-1.1, pinned |
| arm64 | `1699.1.pm.9` | `1699.4.pm.10` | Official Go 1.26.10 Linux ARM64 SDK, URL/hash/size/platform pinned |

ARM does not install the available Go 1.26.9 RPM. The exact official SDK is checked
against published SHA256/size and platform metadata, then its `VERSION` and ELF64
little-endian machine **183** are checked before native execution. Actual
`go version` must still be `go1.26.10 linux/arm64`, with `GOTOOLCHAIN=local`.
Only official go.dev/dl and its exact dl.google.com/go download redirect are
accepted. The SDK archive and extracted compiler remain in the builder work
directory, not `root/` or retained runtime products. Matching Go source archive
`sources/go1.26.10.src.tar.gz`, BSD/std notices, and `gosu.toolchain_origin`
provenance are retained. Existing CLI, output paths and record schema stay the
same. The input plan is valid; ARM build/execution approval still belongs to CI.

ARM binary/source feeds are distinct from x86, including the documented
`https://download.opensuse.org/ports/aarch64/source/tumbleweed/repo/oss/` source
feed. Matching filenames do not prove identical cross-architecture SRPM hashes.
The source collector resolves complete exact RPM provenance separately.

## Maintenance and limits

Update both architecture records from actual signed repository metadata; retain
exact binaries/source RPMs or refresh pins together when the rolling feed drops
old builds. Review source/patch/config pins and tests together. This manifest
does not make a moving repository immutable or promise indefinite availability.
Do not add unsigned/trust-on-first-use fallbacks, alternate privilege-drop tools,
format skips, global experimental strictness or vulnerability suppressions.

Only targeted offline unit tests and an owned scratch interface build are in this
slice. ARM execution, production Docker/CI integration, wheel/application gates,
full original-advisory disposition, and corresponding-source/license closure
remain with the parent and separate lanes. No release approval is implied.
