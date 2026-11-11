# Cairo and GNU tar: bounded remediation evidence

Assessment: **2026-11-11**, isolated from `origin/dev`
`95fc8e4e88c22744327de0ed75767ce5e8b28af9` (0066).
Tracks `eeh2i`, `4yxbm`, coordination `uv80n`.

## Outcome

Neither P1 is closed. There is **no installed remediation or advisory clearance**.

- **Cairo:** the exact font assertion is observable with an ordinary font when
  a caller violates Cairo's documented debug-cleanup lifetime contract. Correct
  cleanup order succeeds. This narrows the investigation, but does **not** prove
  the actual Poppler input trigger or repair it. MR 621 and MR 632 remain
  open/unmerged and concern a different tessellation fault.
- **GNU tar:** a maintained vendor repair exists. A bounded sequence of the
  original vendor patches, retaining their code and tests, applies to the
  exact Alpine source with fuzz zero. The installed GNU tar's top-level boundary
  failure and repeated-extraction hazard are confirmed in private disposable
  directories. **The patched source has not been compiled or executed.**
- Fresh official Alpine package pages still offer Cairo **1.18.4-r1** and tar
  **1.35-r5** on **both x86_64 and aarch64**. The current official aports GitHub
  mirror recipes equal the pinned package-commit recipes. Neither includes the
  relevant corrections. A vendor-maintained stable update is preferred; none is
  present in the inspected package pages/recipes. This worker did not independently
  verify fresh signed APK indexes and does not claim ARM execution.

## Evidence and scope

The [native receipt](evidence/cairo_tar_native_contract_20261111.json) binds the
probe, exact library/executable hashes, installed APK records, image ID, registry
digest, ordinary font hash, isolation settings, and complete observed outputs.
The image is cached AMD64 **0.18.2-0066**,
`sha256:ca2fb0b9c99667df56641eaf0427535079f2b8dd66bfbde2e951603a4d4958a8`.
It is not a newly rebuilt/pulled image, nor proof of a patched final image.

The [assessment index](evidence/cairo_tar_remediation_20261111.json) identifies
the hash-bound receipt capsule. It retains complete primary HTTP bodies and
per-response URL/status/hash/date, original patch bytes, failed acquisitions,
source replay transcripts, all 44 changed-file pre/post hashes, and test logs.
The GNU source archive and ordinary font are hash-bound but not embedded.
Scratch retention: `/tmp/opencode/ecm-p1-cairo-tar-proof-20261111`.

Only private scratch was writable in the native probes: non-root 3000:3000,
read-only image/source, no network, all capabilities dropped,
`no-new-privileges`, 2 CPUs, 3 GiB memory, 64 PIDs, 32 MiB private `/tmp`.
No exploit attachments were downloaded or run. No untrusted payload was compiled.
No operator data, live container, network target, shared test state, or shared
recipe/gate was mutated. A first nested read-only mount attempt failed before
starting a process; the final run mounts an ordinary font copy read-only.

## Cairo — CVE-2025-50422

### Primary evidence, not a substitute MR

- [CNA record](https://cveawg.mitre.org/api/cve/CVE-2025-50422): the reported
  `_cairo_ft_unscaled_font_fini` assertion, affected through 1.18.4, CWE-617.
- [Poppler 1591](https://gitlab.freedesktop.org/poppler/poppler/-/issues/1591):
  description names `cairo_debug_reset_static_data`. Closed by the original
  reporter on 2025-07-24; the API has `merge_requests_count: 0`. Closure is not
  a matching fix. The notes API returned **401**; no credentials or attachment
  access was attempted.
- [MR 621](https://gitlab.freedesktop.org/cairo/cairo/-/merge_requests/621) and
  [MR 632](https://gitlab.freedesktop.org/cairo/cairo/-/merge_requests/632): both
  currently `state: opened`, `merged_at: null`. MR 632 includes 621 and adds
  rectangular-tessellation crash tests. Their patches do not change
  `cairo-ft-font.c`. Do not substitute 632 for 621 as a font-assertion remedy.
- Cairo 1.18.4 `src/cairo-debug.c:54–58` explicitly warns that debug reset is
  unsafe while Cairo objects remain. In `cairo-ft-font.c:347–359`, map cleanup
  skips releasing externally supplied FT faces, then calls fini; fini asserts
  `unscaled->face == NULL` at line 516.
- Poppler 25.08.0 `utils/pdftocairo.cc:1289–1294` calls this debug reset under
  `#ifndef NDEBUG`, explicitly describing a remaining reference as a leak.
  This is caller source context, not a demonstration of which Poppler object
  retains the reported reference or whether another path is reachable.
- The inspected 30-entry upstream `cairo-ft-font.c` history reaches 2023. Its
  only post-1.18.4 entry is `842bd70c` (2026-09-16), a buffer-size overflow
  correction, not a font-lifetime remedy. This bounded history survey is not
  proof that no correction exists anywhere.

### Controlled native result

Using the trusted packaged DejaVuSans font and public FT/Cairo APIs:

| Caller order | Native result |
|---|---|
| Live Cairo FT font face → debug reset | SIGABRT; exact `unscaled->face == NULL`, `cairo-ft-font.c:516` |
| Destroy Cairo face → release FT face → debug reset → release FT library | Exit 0 |

This proves the instrument can distinguish the documented lifetime contract on
the installed bytes, and that assertions are enabled in the tested Cairo build.
It does **not** reproduce the CVE's PDF trigger, establish confidentiality loss,
or prove ECM-wide non-reachability. No malformed font/PDF was needed.

**Do not remove the assertion, compile with assertions disabled, clear a field
without fixing ownership, or apply the tessellation MR as a font fix.** A faithful
repair requires the actual leaking/misordered caller ownership path and a
maintainer-reviewed correction. The destroy-first witness is a lifecycle
prototype, not a proposed global change to Cairo or ECM.

### Conflicting interpretations and quantified risk

The fresh CNA record supplies **2.9**, vector
`CVSS:3.1/AV:L/AC:H/PR:N/UI:N/S:U/C:N/I:N/A:L`. This is new primary metadata,
**not conversion of Alpine's missing vector/zero score into low risk**.
[SUSE's advisory](https://www.suse.com/support/update/announcement/2025/suse-su-202503450-1/)
labels a rectangular `active_edges_to_traps` NULL dereference as this CVE and
quotes SUSE 3.3 plus differing NVD 5.5 and 2.9 scores. Debian still lists its
Cairo source as unfixed. Those conflicting diagnoses cannot establish that the
reported FT assertion is fixed.

Observed impact is process abort at invalid debug cleanup; the original report's
"information leak" title is not established by this probe. User interaction,
runtime call reachability, reference retention, and operational likelihood in ECM
remain unproven. Poppler's absence and lack of an ECM debug-reset call are **not
package exemptions**. Effective ECM risk remains undetermined; retain the **P1
hold**, rather than transferring another vendor's score or declaring safety.

**Concrete blocker:** no matching reviewed correction/root cause has been
established from accessible primary evidence. Obtaining upstream diagnosis or an
equivalent legitimate-input ownership reproducer is required before source
patching. A new owned Cairo provider alone would not resolve this uncertainty.

## GNU tar — three separate contracts

| Record | Primary risk metadata | Evidence and context |
|---|---|---|
| CVE-2025-45582 | Alpine historical 4.1; fresh Red Hat **5.6**, `AV:N/AC:H/PR:N/UI:N/S:U/C:L/I:L/A:L` | [GNU maintainer](https://lists.gnu.org/archive/html/bug-tar/2025-08/msg00012.html) calls repeated untrusted extraction documented misuse. Our ordinary, locally generated two-archive witness modifies a sibling scratch sentinel when the destination is reused; an independent second destination contains the second file as intended. Neither interpretation is an automatic waiver. |
| CVE-2026-18477 | Fresh Red Hat **4.4**, `AV:L/AC:H/PR:L/UI:R/S:U/C:N/I:H/A:N` | Incremental dumpdir `X` rename/temporary-directory handling. Attacker can write backed-up content and access the restore system; no archive modification is required. Fresh-directory-only advice does not mitigate this race. No race reproduction or patched binary test is claimed here. |
| CVE-2026-18508 | Fresh Red Hat **4.4**, `AV:L/AC:L/PR:N/UI:R/S:U/C:L/I:L/A:N` | With `--one-top-level`, a hardlink under `top/` resolves to a working-directory sentinel outside `top/`. Our first witness confirms inode identity without changing the sentinel; the second uses `--overwrite` and changes only that scratch sentinel. Both commands exit 0. |

Primary vendor records:
[45582](https://access.redhat.com/hydra/rest/securitydata/cve/CVE-2025-45582.json),
[18477](https://access.redhat.com/hydra/rest/securitydata/cve/CVE-2026-18477.json),
[18508](https://access.redhat.com/hydra/rest/securitydata/cve/CVE-2026-18508.json).

Red Hat's text still says "assessing" for 18477, **but its structured
`affected_release` already lists fixed RHEL packages**, including
`tar-2:1.35-13.el10_2`. Preserve both fields; do not infer no fix from the prose,
or an Alpine fix from a foreign vendor's NEVRA. `package_state` also includes
unrelated packages and heterogeneous dispositions. These are not Alpine enums.

The installed binary supports both ordinary relative and absolute
`--one-top-level` extraction in the compatibility controls. Losing absolute-path
support to the intermediate vendor patch is therefore a regression, not an
acceptable way to make the boundary test green.

### Source prototype: the complete dependency chain matters

Fresh [AlmaLinux tar source](https://git.almalinux.org/rpms/tar/src/branch/c10s)
provides a maintained backport, not merely the historical draft/revert titles.
The captured source spec is 1.35 release 13. Most patch bodies are pinned to
`f31e585904ba4128f8da22de178145e6bbfd5ed6`; the incremental follow-up is separately
bound by its captured Git blob and SHA256.

The source archive matches the **exact SHA512 in Alpine's pinned recipe**:
`8b84ed661e6c878fa33eb5c1808d20351e6f40551ac63f96014fb0d0b9c72d5d94d8865d39e36bcb184fd250f84778a3b271bbd8bd2ceb69eece0c3568577510`.
The bounded replay uses the existing Alpine checksum patch, then these **11
original vendor files**, in order:

1. Vendor Patch3: wildcard/name handling prerequisite.
2. Patch4: read-only-filesystem atime handling prerequisite.
3. Patch11: padding/error plumbing prerequisite.
4. Patch12: disk-read-error plumbing and test registration prerequisite.
5. Patch13: directory extraction correction and original regressions.
6. Patch22: `--no-overwrite-dir` correction and regressions.
7. Patch23: complete **87,640-byte** confinement/gnulib openat2 backport.
8. Patch24: intermediate relative-only top-level behavior.
9. Patch26: CVE-2026-5704 chain on which later source context depends.
10. Patch27: **25,233-byte** final absolute-top-level correction, also correcting
    hardlink confinement; do not stop at Patch24 or the earlier draft.
11. Patch28: **11,055-byte** incremental correction, including descriptor-relative
    `mkdtempat`, name handling, and original `rename08`/`rename09` regressions.

Every complete patch applies with **`patch --batch --fuzz=0 -p1`**. Source and test
hunks/registrations are retained, with **44 changed regular files** hash-bound.
The [source prototype receipt](evidence/cairo_tar_source_prototype_20261111.json)
also records a separate offline consumer replay from the final sealed capsule:
all twelve patch applications and all 44 resulting full-file hashes agree.
This is a **source-application result**, not security review of every fallback
or a native test pass. Earlier incomplete attempts and their actual code/test
conflicts are retained; they were not silently ignored or reported successful.
The full vendor sequence also replays, but importing its unrelated distro patches
is not proposed. The bounded sequence avoids those unnecessary additions.

The openat2 implementation attempts the kernel syscall and emulates only after
**ENOSYS**, not after arbitrary errors such as EPERM. The tar path resolver sets
`RESOLVE_BENEATH` unless `--absolute-names` explicitly opts out. Kernel support,
Docker seccomp behavior, emulation/race guarantees, and old-kernel feature parity
remain **untested** for this proposed build. Neither one syscall name nor a
successful patch application is blanket confinement proof.

### Maintenance choice and exact remaining work

| Choice | Disposition |
|---|---|
| Maintained official Alpine stable update/backport of the complete corrections | **Preferred.** Current inspected pages/recipes do not provide it. Recheck actual signed feeds before adoption. |
| Approve one bounded owned GNU tar provider using the retained complete source prototype | Explicit maintenance decision required. Preserve GNU features, ACLs/xattrs, normal/incremental restore behavior, the original Alpine checksum behavior, and absolute top-level support. Do not import glibc RPM binaries or mix edge feeds into musl stable. |
| Rely solely on fresh directories or `--one-top-level` | **Insufficient.** They do not settle all three contracts, especially incremental rename races. |
| Suppress the CVEs, replace GNU tar with BusyBox, or infer non-applicability from Python `tarfile` | **Not proposed.** These do not fulfill package/operator/transitive acceptance. |

**Concrete execution blocker:** the faithful vendor procedure needs
`autoreconf -v`; the available host returns **127, command not found**, and the
approved cached test-tooling image has no cc/make/Autoconf/Meson. No new toolchain,
image pull, full Docker build, provider package, or global cohort was introduced.
Approving a maintenance route must also provide an appropriate isolated native
build environment. Do not bypass regeneration by silently omitting new gnulib
objects or the original Autotest registrations.

Native acceptance still needs both musl architectures; kernel and ENOSYS/EPERM
paths; deterministic incremental rename/race coverage in an inaccessible restore
tree; original upstream regressions; ordinary/internal links, ACL/xattr and
incremental round trips; relative/absolute top-level behavior; explicit `-P`
semantics; final installed-byte/source/scanner binding. No feature may be removed
to satisfy a probe.

Until repaired, follow the primary vendor's contextual mitigations: isolate
untrusted extracts as an unprivileged user in independent empty destinations;
do not treat top-level wrapping as a sandbox; do not perform untrusted incremental
restores or restore on systems accessible to untrusted users. These reduce
exposure but are not a source fix or a quantified ECM-wide likelihood reduction.
Python `tarfile` application paths distinguish components only. Operator and
indirect GNU tar workflows remain in scope. Effective risk remains unresolved
**P1/pre-promotion blocking**, not dismissed because no direct application call
was found.

## Verification and handoff

- Eleven instrument/source-receipt unit tests pass on the approved native Python 3.12 tooling
  image, using `scripts/backend-gate.sh --subset -p no:cacheprovider` and a
  private scratch/config root. **Subset, not the backend gate.**
- The exact dangerous mutant that ignores outside inode aliasing fails a unit
  assertion (**1 failed, 8 passed**); restored code plus the two source-receipt
  tests passes. Recorded native output and real upstream MR responses are parsed.
- The native probe's **exit 1 is intentional RED**: `--require-tar-confinement`
  detects the installed tar boundary failure. Cairo's destroy-first control and
  both ordinary top-level extraction controls succeed. This is not a green
  vulnerability gate or evidence of a patched binary.
- Ruff check and format check pass for the two new Python files.
- No existing security-library recipe/helper/source plan, version/changelog,
  SBOM, application source, workflow, test skip, or suppression was changed.

Re-run the native witness only in the documented isolation, with a trusted
ordinary font mounted read-only:

```sh
PROOF=/tmp/opencode/ecm-p1-cairo-tar-proof-20261111
docker run --rm --network none --read-only --user 3000:3000 \
  --cap-drop ALL --security-opt no-new-privileges \
  --cpus 2 --memory 3g --pids-limit 64 \
  --tmpfs /tmp:rw,nosuid,nodev,size=32m,uid=3000,gid=3000 \
  --mount type=bind,src="$PWD",dst=/source,readonly \
  --mount type=bind,src="$PROOF",dst=/proof,readonly \
  --entrypoint /opt/venv/bin/python \
  sha256:ca2fb0b9c99667df56641eaf0427535079f2b8dd66bfbde2e951603a4d4958a8 \
  /source/scripts/proofs/cairo_tar_contract_probe_20261111.py \
  --font /proof/font.ttf --tar /bin/tar --require-tar-confinement
```

This standalone command requires a non-root read-only root filesystem. The guard
is accidental-execution protection, not the sandbox; Docker supplies isolation.
The flag checks the two narrow top-level witnesses, **not global safety**, and
the report explicitly leaves the Poppler input and incremental race untested.

Parent integration may take these independent proof/documentation files. Recipe
integration remains with its owner after the maintenance choice. Keep both P1s
open/blocked pending actual source/runtime acceptance; do not count this as
completion of all nine P1s or all nineteen advisory records.
