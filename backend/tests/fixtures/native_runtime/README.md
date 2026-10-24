# Recorded native runtime inputs

`rpm-headers.tsv`: actual `rpm -qp` name/version-release/architecture/SOURCERPM/
SOURCEPACKAGE output for the ten checksum- and isolated-key-signature-verified
x86_64/aarch64 Packman binaries plus their four matching public source RPMs.
`package-primary-records.json`: actual x265-devel primary XML records from the
signature-verified documented Packman Essentials repository, including exact
HDR10+ split dependencies. No architecture-equivalent revision is inferred.
`source-identities.json`: actual verified upstream FFmpeg/Gosu archive identities.
`go-catalogue.json`: the official go.dev JSON catalogue's real Go 1.26.10 ARM64 SDK
and matching source records, preserving filename/version/OS/architecture/kind,
published SHA256 and byte size. The runtime SDK is not executed by unit tests.
`ffmpeg-signature.txt`: actual exact-fingerprint GPG status from the proven image.
`ffmpeg-readelf.txt`: actual readelf output for that image's owned FFmpeg binary.
`ffmpeg-version-with-warning.txt`: actual merged stdout/stderr from the first
patched-helper proof attempt. A NUMA permission warning precedes the correct
version. Tests require exactly one matching version line, not silent acceptance
of a different version or a privilege/seccomp relaxation to remove the warning.
`ffmpeg-buildconf.txt`: actual configuration framing from the second focused
build, including the normal exit footer. Tests compare every argument exactly
and reject missing, changed, reordered or extra flags, not that unrelated footer.
`ffmpeg-buildconf-parent.txt`: byte-for-byte parent integration capture from
`ecm-suse-integration-native-gate-final.json`,
`runtime.metadata.ffmpeg_build_configuration`. The parser's regression test uses
this actual 8.1.3 response, including `Exiting with exit code 0`, and rejects
unknown text, wrong/duplicate framing, and missing/changed/extra arguments.

Origin/retrieval/signature logs and complete original primary metadata are under
the owned `suse-prototype/production-builder/` evidence directory. These minimal
fixtures do not certify corresponding-source completeness or ARM execution.
