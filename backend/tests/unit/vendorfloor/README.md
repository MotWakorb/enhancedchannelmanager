# Recorded vendor controls

`suse-rpm-query-20261025.json` contains actual seven-column `rpm -qp` rows for
the four relevant RPMs fetched separately from each architecture's official
Tumbleweed repository on 2026-10-25. RPM signatures and the exact corresponding
SRPMs were verified with the existing `scripts/collect_native_sources.py` pinned
key/header/complete-payload verifier. AMD snapshot was 20261023; ARM was 20261020.
These are vendor package projections, **not a rebuilt image inventory**. ARM
fixed packages and exact source URLs returned 404 at survey time. Unit-positive
ARM fixed-version controls are explicitly synthetic; old recorded image fixtures
and their ARM inventory hash remain unchanged.

`root-comodo-aaa.pem` is the public AAA Certificate Services certificate decoded
from Mozilla's official `NSS_3_131_RTM` certdata during the prior CA advisory
review, retained byte-for-byte. Source:
<https://hg.mozilla.org/projects/nss/raw-file/NSS_3_131_RTM/lib/ckfw/builtins/certdata.txt>.
DER SHA-256: `d7a7a0fb5d7e2731d771e9484ebcdef71d5f0c3e0a2948782bc83ee0ea699ef4`.
This is a negative trust-policy test fixture only, never an installed trust root.
It contains no private key.
