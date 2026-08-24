# Software Bill of Materials

`sbom/vX.Y.Z/` is the dependency inventory of record for release `X.Y.Z`. It is
at the repository root, not under `docs/`, because it is an artifact you reach
for while holding a CVE and a deadline — not documentation.

Each directory holds three files:

| File | What it is |
| --- | --- |
| `ecm.spdx.json` | SPDX 2.3 document for the ECM image's dependencies |
| `mcp.spdx.json` | SPDX 2.3 document for the MCP image's dependencies |
| `index.json` | The version, the SHA-256 of every source manifest the documents were derived from, and the SHA-256 of each document |

## Read this before you trust a document

These are **source-manifest** SBOMs. They inventory:

- every Python distribution pinned in the image's `requirements.txt`, with its version;
- every npm package resolved in `frontend/package-lock.json`, with its version (ECM only);
- every base and build image the Dockerfile pulls, **by digest**.

They do **not** inventory:

- **the operating-system packages inside the base images.** This is the important
  gap. A large share of container CVEs land in Debian/Alpine packages, and these
  documents do not list them. They name the base image digest, so a Debian or
  Alpine advisory can still be correlated to the base image you shipped — but you
  cannot answer "which version of `openssl` is in it?" from this file. Both
  Dockerfiles also run an OS upgrade (`apt-get upgrade` / `apk upgrade`) at build
  time, so the installed OS package set is not derivable from the source tree at
  all. For that question, scan the image: the published image digests are in the
  GitHub Container Registry, and the Trivy jobs in `build.yml` scan every
  candidate before it can be published.
- native libraries vendored inside Python wheels;
- **any image digest.** No document here asserts one, and none should be added by
  hand.

## Why there is no image digest

An image SBOM has to be generated from an image, and the images a release
publishes do not exist when the release is cut. `build.yml` triggers on pushes to
`main` and `dev`; the release images are built by the push to `main` that the
release PR's *merge commit* creates. That merge commit necessarily has a
different SHA from the release branch tip, and `Dockerfile` bakes `GIT_COMMIT`
into the ECM image as an `ENV`, so an image built from the release branch is
guaranteed to have a different digest from the one that ships — even though its
contents are otherwise identical. (Image config also carries a wall-clock
`created` field, so two builds of the same tree do not agree on a digest either.)

Building on `release/**` and inventorying that image would therefore commit a
document naming a digest that is not, and never will be, in the registry. A
committed SBOM that is not provably bound to its artifact is worse than none,
because it will be believed — so this one binds to what it can actually be
checked against, and says plainly what it does not cover.

Getting to a digest-bound image SBOM requires the release images to be *promoted*
rather than rebuilt when the release PR merges. That is a change to the
publication design, not to this generator; it is tracked separately.

## Binding, and what enforces it

`index.json` records the SHA-256 of every manifest the documents were derived
from, and `scripts/generate_sbom.py verify` regenerates both documents from the
working tree and byte-compares them against what is committed. The Release Cut
Gate runs that as **G8** on every release PR, so a release cannot merge with a
stale, hand-edited, deleted, or wrong-version SBOM. Every one of those mutations
is red-proven in `backend/tests/unit/test_generate_sbom.py`.

The one field `verify` does not police is `creationInfo.created`: a timestamp
cannot be re-derived from the tree, so it is read back out of the committed
`index.json` and reused. Every other byte is re-derived.

`scripts/generate_sbom.py audit` checks a directory against its own index
without needing that release's source tree, so past releases stay checkable.
It runs over every committed directory as a test.

## Commands

```bash
python scripts/generate_sbom.py generate            # for the current package.json version
python scripts/generate_sbom.py generate --version 0.19.0
python scripts/generate_sbom.py verify --version 0.19.0    # what the Release Cut Gate runs
python scripts/generate_sbom.py audit --out sbom/v0.18.0   # internal consistency only
```

## Directories that are not releases

`dev` moves its build counter on nearly every PR, and regenerating an SBOM on
each one is a tax nobody asked for — so a directory named for a
build-counter version (`v0.18.1-0144`) is a snapshot taken deliberately, not a
per-commit guarantee. It is held to the same standard as a release directory
while the tree still carries that version: `verify` must pass.
