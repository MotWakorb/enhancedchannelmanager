# Selective system distrust

`emsign-ecc-root-ca-g3.pem` is the **public certificate to distrust**, not a
trust anchor or private key. Its DER SHA-256 is:

`86a1ecba089c4a8d3bbe2734c612ba341d813e043cf9e8a862cd5c57a36bbe6b`

The bytes were recovered from the retained pre-remediation certifi bundle
(bundle SHA-256 `9cc2a774b5198dcff14d9be1e66091f538975d867ce029a96bce15a55dfd730f`)
and independently matched by DER fingerprint, not by subject-name matching.
See the [upstream advisory](https://github.com/certifi/python-certifi/security/advisories/GHSA-6q48-6c7w-9gw2).

The accompanying Dockerfile installs only this certificate into SUSE's
`/etc/pki/trust/blocklist`, validates its fingerprint, then regenerates the
compatibility stores with `update-ca-certificates`. Other distribution roots
and `/etc/pki/trust/anchors` administrator configuration are not replaced.
The runtime gate still requires unaffected emSign G1 and rejects G3.
Both architecture images carry these editable PEM/configuration bytes and this
README at `/usr/share/ecm/sources/trust/`, with the exact build/regeneration
instructions in `/usr/share/ecm/sources/trust/Dockerfile`. The source-manifest
SBOM fingerprints every file in `docker/trust/` as well as the Dockerfile.

References:
- https://manpages.opensuse.org/Tumbleweed/ca-certificates/update-ca-certificates.8.en.html
- https://p11-glue.github.io/p11-glue/p11-kit/manual/trust-module.html
