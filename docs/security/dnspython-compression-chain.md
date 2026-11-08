# dnspython compression-chain bound

Tracks `enhancedchannelmanager-8falz`, CVE-2026-114605 /
GHSA-45c3-73f7-pv4m, candidate build 0.18.2-0064.

The maintainer rates the uncontrolled DNS compression-pointer traversal **High,
CVSS 3.1 7.5** (`AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H`). A malicious DNS wire
message can consume excessive CPU in affected parsers. dnspython 2.9.0 limits
decompression to 16 pointers; no workaround is claimed.

ECM's backend has no direct dnspython resolver/wire imports in the inspected
source. Its dependency comes through email-validator. The installed Pydantic
email path explicitly sets `check_deliverability=False`; the regression checks
that validating a normal address does not invoke the resolver. Native socket
name resolution is not evidence of use of this Python wire parser. Effective
risk is **Low in this inspected syntax-only application path**, because no route
to attacker-controlled dnspython wire decoding was demonstrated. This is not a
blanket non-exploitability statement for plugins, future consumers or deployment
overrides, and does not suppress the raw High advisory or audit gate.

The lock refresh changes only dnspython 2.8.0 to 2.9.0 among 74 runtime pins.
It does not enable DNS-over-HTTPS extras, change SSRF resolution, add network
queries to email validation, or alter authentication. Python 3.12 satisfies the
new package's Python >=3.11 floor.

## Regression and artifact evidence

`backend/tests/security/test_dns_compression_chain.py` uses the maintainer's tiny
wire fixture, not a resource-exhaustion payload: 16 pointers remain valid and
17 are refused with a DNS format error. Before the upgrade the latter assertion
fails because the old parser accepts the chain. The email syntax-only control
passes both versions. All tests are offline.

The public `py3-none-any` wheel is hash-verified against PyPI metadata; its parser
bytes match pinned release commit `e790c8f20a67ad439c7aafef2321e161c6028eee`.
The published wheel and sdist both omit LICENSE. Rather than invent an archive
member or silently drop notices, this candidate delivers the ISC notice from
that exact upstream commit, verified against its Git blob and SHA-256. It is
byte-identical to the retained earlier notice. Artifact details are recorded in
`docker/python-third-party/artifact-index/dnspython-2.9.0.json`.

Historical native/wheel evidence is not relabeled as new. The shared pure-Python
index and current lock/component manifest identify the targeted replacement;
the older x86 snapshot remains a historical record for its original packages.
The native architecture indexes explicitly rebind the new lock hash while
retaining every native distribution record unchanged; their proof-layer limits
still apply. The runtime verifier pins those exact index hashes and the sorted
557-entry notice manifest. Its fixture copies the shipped manifest unchanged,
so tests cannot silently repair a broken notice bundle before exercising it.
Final image selection, native checks and unsuppressed scans still need actual
execution after the independent vendor/base delivery blockers are resolved.

## Primary references

- [Maintainer advisory](https://github.com/rthalley/dnspython/security/advisories/GHSA-45c3-73f7-pv4m)
- [Fix and upstream regression](https://github.com/rthalley/dnspython/commit/b485e460c266540a1a09ca6d8d1d7b685b1d3023)
- [Fixed release](https://github.com/rthalley/dnspython/releases/tag/v2.9.0)

No new ignore rule, VEX waiver, source substitution or delivery clearance is
introduced by this repair. The existing CA/Brotli and base-image holds remain.
