# Software Bill of Materials

`sbom/vX.Y.Z/` is the dependency inventory of record for release `X.Y.Z`. It is
at the repository root, not under `docs/`, because it is an artifact you reach
for while holding a CVE and a deadline — not documentation.

## Two kinds of directory, and they have opposite lifecycles

Read this first; conflating them is the one mistake that makes this whole
directory misleading.

| Path | What it is | Lifecycle |
| --- | --- | --- |
| `sbom/vX.Y.Z/` | **Release record.** The inventory of record for a promoted release. | **Accumulates. Kept forever.** `v1.0.0/`, `v1.1.0/` and `v1.2.0/` all sit side by side. That is the entire point: when an advisory lands, the question is *which shipped versions contain the affected package*, and only the history can answer it. Never delete one. |
| `sbom/dev/` | **Transient snapshot** of whatever `dev` currently carries. Not an artifact of record; no released version is described by it. | **Superseded, never accumulated.** There is at most one, and regenerating it replaces it. |

The two are told apart by the **shape of the version string**, which decides the
path — not by a convention a reader has to know. [`docs/versioning.md`](../docs/versioning.md#format)
§Format: a release cut drops the `-BUILD` suffix, so `X.Y.Z` is a release and
`X.Y.Z-NNNN` is a dev build. `channel_for()` in the generator is the only place
that judgement is made and `directory_for()` is the only place a path comes out
of it, so `generate --version 0.18.1-0147` writes `sbom/dev/` no matter who
types it. `audit` fails any directory whose name and recorded channel disagree,
and a backend test asserts no committed directory is a dev snapshot wearing a
release name.

### Why the rule is written down this hard

`sbom/v0.18.1-0144/` was committed for a build number that was never released.
One build later a 60-package dependency sweep landed, and its contents described
a dependency set that no longer existed anywhere. A directory holding an
inventory matching nothing that ever shipped is worse than no directory: it is
the first thing somebody finds when they go looking during an incident, and its
name says "release record". It was removed rather than kept as a sibling.

Each directory holds three files:

| File | What it is |
| --- | --- |
| `ecm.spdx.json` | SPDX 2.3 document for the ECM image's dependencies |
| `mcp.spdx.json` | SPDX 2.3 document for the MCP image's dependencies |
| `index.json` | The version, channel, source and document hashes, package/relationship counts, and the expected native package/relationship graph for each document |

## Read this before you trust a document

These are **source-manifest** SBOMs. They inventory:

- every Python distribution pinned in the image's `requirements.txt`, with its version;
- every npm package resolved in `frontend/package-lock.json`, with its version (ECM only);
- every direct source-built native dependency declared in
  `native-dependencies.json`, with its version, license, source reference, and
  package relationships. Every ECM/MCP subject is explicit even when empty;
  resdet's source commit, archive checksum, and pixel ceiling are also the
  values consumed directly by its Docker build;
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

Each new index entry also records its exact native package IDs and native SPDX
relationships. `audit` compares those semantics as well as package and
relationship counts, so removing or reversing a required resdet/KISS FFT edge
and merely recomputing the document hash does not make a historical document
audit clean. Older index entries remain auditable under the format they recorded.

The one field `verify` does not police is `creationInfo.created`: a timestamp
cannot be re-derived from the tree, so it is read back out of the committed
`index.json` and reused. Every other byte is re-derived.

What `verify` holds the two kinds to is deliberately different, because the
questions are different:

- **A release record** must match its tree exactly, *including* the version:
  `frontend/package.json` and the directory must agree, or G8 fails.
- **The dev snapshot** is held to **manifest currency, not build-counter
  currency.** Change a dependency without regenerating and it goes red; move
  only the build counter and it does not. `dev` bumps its counter on nearly
  every PR, and re-cutting the inventory each time buys nothing — the binding
  that matters is the source-manifest hashes. The recorded version names the
  build the snapshot was taken at, and `index.json` says so in `channelNote`.
  A dependency sweep landing without a regeneration is exactly the drift that
  made the `0144` documents wrong, and that is the case this catches.

`scripts/generate_sbom.py audit` checks a directory against its own index
without needing that release's source tree, so past releases stay checkable. It
also checks that the directory's name, its recorded channel and its recorded
version agree — the check that stops a transient snapshot from being read as a
release record. It runs over every committed directory as a test.

## Commands

```bash
python scripts/generate_sbom.py generate                   # current package.json version; routes by channel
python scripts/generate_sbom.py generate --version 0.19.0  # release record -> sbom/v0.19.0/
python scripts/generate_sbom.py verify --version 0.19.0    # what the Release Cut Gate runs
python scripts/generate_sbom.py verify                     # on dev: checks sbom/dev/ against the manifests
python scripts/generate_sbom.py audit --all                # every committed directory, internal consistency only
python scripts/generate_sbom.py audit --out sbom/v0.18.0   # one directory
```

Refresh `sbom/dev/` whenever a PR changes `backend/requirements.txt`,
`mcp-server/requirements.txt`, `frontend/package-lock.json`,
`sbom/native-dependencies.json`, or a Dockerfile base image — the backend suite
will tell you if you forget.
