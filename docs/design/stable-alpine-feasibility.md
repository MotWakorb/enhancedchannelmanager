# Stable Alpine feasibility: preserve the complete contract

Bead `enhancedchannelmanager-2ixgf`; effective tier **small-team** (ECM/MCP are
home-lab, published-image CI is small-team). This is a targeted feasibility
result, not a passing migration or permission to reduce functionality.

## Answer

**Python feasibility is demonstrated; an official-package-only native replacement
is not compatible or security-equivalent today.** Supported stable Alpine is
v3.24 / 3.24.4. Its two architecture feeds supply 33 of the 36 required direct
native selections. Three are missing and three available security providers
retain reviewed pre-fix source patterns. A stable-Alpine-native source-build
path remains possible, but has not yet been built or proven equivalent.

The PO's requirements remain **Alpine-only, no rolling/edge, identical behavior**.
Do not treat fewer formats, older vulnerable libraries, scanner silence or a
successful dependency solve as an acceptable compromise.

## Completed evidence

Two bounded, independent read-only investigations used source baseline
`06cc4bf5a1b9eb1f6ef29f617513d237cda2f531`. Sources, exact HTTP responses, package
identities, signature checks and commands are retained outside the checkout:

- `/tmp/opencode/ecm-alpine-python-spike-20261108/REPORT.md`
- `/tmp/opencode/ecm-alpine-native-spike-20261108/HANDOFF.md`
- `/tmp/opencode/ecm-alpine-native-spike-20261108/NATIVE-DELTA.md`

| Question | Actual result | Limit |
|---|---|---|
| Existing Python pins | All **74** resolve on x86_64 and aarch64: 52 pure + 22 musllinux distributions each; 96 distinct wheels downloaded/hash-verified and pip resolution independently checked | ARM wheel selection/ELF inspection is not native ARM execution |
| Native Python operations | All unchanged pins installed on actual Alpine x86_64, `pip check` passes, 37 non-test extensions load and bounded operations/trust checks pass | Not complete backend/media/bootstrap or whole-image security proof |
| Stable Python base | Official `python:3.12-alpine3.24@sha256:e064671ca452259bab50e8e2c7bd896e39e9a343bb06ca25234adf6fe18d0fc7`, observed Python 3.12.16 / Alpine 3.24.4 | Distro `apk add python3` selects 3.14.10 and would violate the minor-version contract |
| Native package availability | Four actual stable indexes verified with Alpine's shipped architecture keys; 33/36 direct selections on each architecture; full solves explicitly fail on missing libraries | A 223-package build-dependency simulation is not a runtime inventory or behavior pass |
| CA bundle parity | All **118 root DER identities** match the actual same-source baseline and each signed Alpine bundle: zero added/removed | All application consumer paths, administrator overrides and TLS behavior still require final-image tests |
| Source/security review | 20 selected APK signatures/header/index joins and 23 source checksum inputs verified; exact source witnesses retained | Not complete final-image corresponding-source closure or a candidate scan |

## Concrete native blockers

| Requirement | Stable v3.24 result, both architectures | Required treatment |
|---|---|---|
| Codec2 input support | No Codec2 package/development provider, including aliases | Preserve the baseline **1.2.0** capability through a reviewed pinned source build or actual stable vendor addition; no disabled integration |
| TwoLAME encoder integration | No TwoLAME provider | Preserve baseline **0.4.0** capability through a reviewed pinned source build or stable addition |
| Shine encoder integration | No Shine provider | Preserve baseline **3.1.1** capability through a reviewed pinned source build or stable addition |
| libxml2 corrections | **2.13.9-r2** retains source patterns for six reviewed 2.15.4 corrections | Corrected source/verified vendor backports required; do not clear from version labels or absent scan findings |
| librsvg correction | **2.62.3-r0** retains the entity replacement/UAF mechanism | Same-series fix exists at **2.62.4**; baseline is **2.63.4**. Security and feature equivalence must both be reviewed, not inferred from the lower fixed version |
| Speex correction | **1.2.1-r2** retains the non-owned-buffer truncation defect, no relevant packaging patch | Preserve the reviewed correction through an authenticated fixed source/patch path; unrelated APK revision numbers do not prove a fix |

The libxml2 source witnesses concern CVE-2026-86138, -86139, -86140, -86142,
-86143 and -86144. Do not reintroduce a blanket Critical claim for
CVE-2026-6653: its current CNA affected range excludes 2.13.9. The separately
packaged Python SAX binding must remain absent and independently checked.
Librsvg CVE-2026-96889 and Speex CVE-2026-116741 retain their existing primary
risk assessments/owners; missing vendor advisory records do not waive them.

This means **at least six additional library workstreams**, not a claim that all
other version differences are harmless. The complete native delta records all
36 direct selections and every one of the 211 baseline RPM/key rows. Every
functional dependency requires a disposition and actual comparison; OS-specific
branding/key metadata is accounted for as a platform change, not copied as a
foreign runtime dependency.

## Positive security results are scoped

- Stable musl/musl-utils **1.2.6-r3** carries the vendor backport for
  CVE-2026-107891. Requiring upstream 1.2.7 would incorrectly reject the backport.
- Stable OpenSSL **3.5.9-r0** exceeds the reviewed Alpine 3.5.8-r0 floor. It does
  not document or patch wheel-bundled OpenSSL: cryptography 50.0.2 must still load
  its expected separate OpenSSL 4.0.3 copy.
- Brotli **1.2.1-r0** has an explicit stable vendor fix for CVE-2026-111497.
  Its primary Critical score is not erased by a scanner's Medium label.
- Existing GNU coreutils/timeout, shadow/account tools and bash must remain.
  BusyBox timeout does not provide the existing required long-option contract.
- Preserve owned gosu **1.19**, its module pins and Go **1.26.10**. Stable's
  functional gosu package was built with older Go 1.26.9. A proposed Alpine-only
  builder is official
  `golang:1.26.10-alpine3.24@sha256:87c65a688975c0b1d4585d204874c45ca78354670e3a5f11368c5a492303d696`;
  its manifest/architecture discovery is recorded, but execution/source/privilege
  proofs are still outstanding.

## Scanner/source gaps must be solved, not hidden

Official Trivy Alpine coverage documents **no unfixed OS vulnerability support**.
All-severity output and `ignore-unfixed: false` do not make absent vendor advisory
records appear. The native source inspection above demonstrates why this matters.

Keep complete raw findings and exact candidate identity/inventory binding, and add
an independent known-unfixed/source-advisory denial path. A required affected
provider must fail even when vendor scan JSON lists no vulnerability. Owned
FFmpeg/libraries need their own exact-source advisory checks. The uncovered
control gap has estimated **Medium 6.5 process risk**, not a newly invented CVSS
vector, and is a pre-promotion blocker. Do not use a clean Alpine vendor report
as evidence of complete coverage.

The musl wheels also contain **nine additional bundled GCC runtime ELF copies
per architecture**. Retain/rebind their real notices/SBOM/source attribution;
current manylinux/RPM indexes are not musl provenance. Existing license text
matches do not by themselves establish complete corresponding-source delivery.

## Stable feeds still need deliberate maintenance

Main's published support end is **2028-06-01**; community support lasts only to
the next stable release. The complete media graph cannot inherit main's full
support period. Exact old APK retention varies by architecture; preserve selected
authenticated artifacts/source inputs where rebuilds require them. No new hosted
artifact service or move to edge is authorized by this observation.

## Next implementation boundary

1. Build the unchanged signed FFmpeg **8.1.3 + one RTSP patch**, with all **49**
   reviewed arguments and all required integrations, using only stable Alpine
   and explicitly reviewed owned sources. Stock Alpine FFmpeg is now 8.1.3, but
   does not prove this configuration/patch contract and is not selected by default.
2. Close the six minimum library gaps, all remaining functional version/config
   differences and GNU iconv/charset behavior; no library downgrade is assumed safe.
3. Complete APK and actual musllinux source/notices plus unfixed-aware checks.
4. Execute the [paired parity matrix](stable-alpine-parity.md), full application
   gates, real bootstrap/isolation and complete scans on **both native platforms**.

The unchanged backend selection is being exercised separately in a disposable
Alpine test-only image. Its first collection attempt failed because the harness
required `jq`; that dependency was added to the **test image**, not bypassed or
skipped. The corrected run remains pending. This test image lacks the complete
media/gosu/source runtime and must never be presented as the production candidate.

The target remains Alpine. Stock-package insufficiency is not a distribution
reversal or a feature-reduction decision. Until the gaps and complete parity
evidence are resolved, the migration is not ready for cutover/publication.
