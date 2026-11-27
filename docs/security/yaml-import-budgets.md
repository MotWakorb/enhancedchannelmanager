# YAML import and restore structure budgets

Every production YAML load on an import, validate, restore or backup path goes
through the pure-Python `yaml_safety.BoundedSafeLoader` (`safe_load_bounded`).
No native parser or new dependency is introduced. These bounds limit parse work
on a single document. They are not a general process-memory limit.

## Structure budgets

Three refusals happen before any Python object is constructed, and before
SafeLoader's merge-key flattening:

| Budget | Value | Applies to |
|-|-|-|
| Alias amplification (expanded graph weight minus composed nodes) | 1,000,000 | Every caller, identically |
| Nesting depth, measured on the expanded graph | 64 levels | Every caller |
| Composed-node ceiling | Chosen per caller, see below | Each document |

The graph check memoizes nodes while counting each alias edge's expanded
weight, so an exponential alias DAG is refused without walking its expansion.
Recursive aliases are refused. Ordinary acyclic aliases and merge keys keep
SafeLoader behavior. Budgets are per document, not aggregate per archive.

Composed-node ceilings:

| Caller | Ceiling | Why |
|-|-|-|
| DBAS category members and DBAS backup creation | `BACKUP_MAX_NODES` = 10,000,000 | A DBAS backup carries a whole Dispatcharr catalog. A channel with three embedded streams costs about 42 to 105 nodes, so this admits roughly 95,000 to 240,000 channels per category. |
| Legacy YAML: `/api/backup/validate`, `/api/backup/restore-yaml`, `GET /api/backup/export` and the scheduled YAML backup | `LEGACY_MAX_NODES` = 2,000,000 | A legacy export never carries channels or logos. The legacy sections of real DBAS artifacts measured 28,262 nodes in total, and a real production instance's M3U group settings, the largest legacy-side structure seen, 46,547 nodes. This leaves more than 40 times headroom over both. |
| Tag, normalization, dummy EPG and channel rule imports, and `rules.yaml` in analyze/from-bundle | `IMPORT_MAX_NODES` = 100,000 | Hand-maintained configuration. A real debug bundle's `rules.yaml` was 206 nodes and its normalization export 455 nodes. This matches the rule editor's scale. |
| Rule editor | 100,000 nodes, 40 levels, no aliases | Unchanged. The editor keeps its duplicate-key and scalar-validation behavior. |

### Cost at the backup ceiling

The pure-Python parser holds the whole composed graph and then the constructed
objects. Measured on one development host (Python 3.12, PyYAML 6.0.3, one core)
with a channel-shaped document just under the ceiling: 185,116 channels with
three embedded streams each, 9,996,269 nodes, 104 MiB of YAML.

| Operation | Wall time | Peak RSS |
|-|-|-|
| `safe_load_bounded` (compose, check, construct) | 170 s | 7.6 GiB, about 75 times the YAML bytes |
| `measure_bounded` (compose and check only) | 162 s | 6.9 GiB |

In short, one parse at the ceiling costs about 170 s and 7.6 GiB. Most of that
is composition, so a document near the ceiling is expensive even when it is
accepted. Creating a backup with a category near the ceiling costs about twice
one parse: the category is dumped and parsed once so it can be redacted, then
dumped again for writing. Restoring it costs one more parse. The creation
preflight itself (below) walks the object instead of parsing, and costs well
under a second at a million nodes.

These parses run synchronously on the asyncio event loop of the request or task
that performs them, so while one runs, the ECM process serves nothing else.
Moving YAML parsing to a worker thread is follow-up bead
`enhancedchannelmanager-yx2pn`. The ceiling makes this cost bounded and
predictable. It does not make it small.

## Byte limits

| Path | Limit | On excess |
|-|-|-|
| `yaml_content` of the four import routes | `IMPORT_MAX_CHARS` = 4 MiB of text | 422 before parsing |
| `/api/backup/validate`, `/api/backup/restore-yaml` upload | 128 MiB, read in 1 MiB chunks | 413 before parsing |
| analyze/from-bundle upload | 128 MiB compressed, read in chunks | 413 |
| analyze/from-bundle members | 256 members, 512 MiB of declared member sizes in total | 413 before the member that crosses it is skipped or read |
| analyze/from-bundle inflation | Every decompressed byte counted, 512 MiB plus an 8 MiB allowance for headers and padding | 413 as soon as the count passes it, whatever the tar contains |
| analyze/from-bundle tar extended headers (GNU long name and long link, PAX local and global) | 64 KiB each, at most 64 per archive | 413 before the header body is read |
| analyze/from-bundle sparse entries (GNU sparse, PAX GNU.sparse) | Not accepted | 400 |
| analyze/from-bundle `rules.yaml` | 4 MiB | 413 |
| analyze/from-bundle `channel_groups_diagnostic.json` | 16 MiB | 413 |
| DBAS artifact upload and members | Existing 2 GiB upload, 1 GiB per member and in total, 100x ratio, 10,000 entries | Unchanged |

Real exports are a few KiB, so the 4 MiB import cap is far above them. It also
stays above `IMPORT_MAX_NODES` at about 40 characters per node, so the node
ceiling, not the byte cap, binds first for plausible documents. Measured
exports run about 10 to 13 bytes per node, so the 128 MiB legacy upload cap
stays far above `LEGACY_MAX_NODES` the same way. The bundle caps admit the
generator's 50 MiB log cap plus a large catalog. A scalar's own length is bound
only by these byte limits.

These caps bound what ECM holds in memory after the request is received. They
do not bound what the server accepts: the JSON body is read whole before its
`yaml_content` length is checked, and a multipart upload is spooled by the
framework before the route reads it in chunks. Request-size limits at receipt
are tracked separately.

### Debug-bundle archive reading

analyze/from-bundle has no admin dependency, and the MCP tool posts to it, so
its archive reader is bounded independently of the tar's own fields. tarfile
reads a GNU long-name, long-link or PAX header's whole body before it returns
the following member, and that body is not counted in any member's size. A
305 KB `.tar.gz` holding a 300 MB long-name header raised RSS by about 600 MB
inside `tarfile.open()` alone, and a PAX header about 1.2 GB, before any member
check could run.

The route therefore reads the archive as a stream (`mode="r|"`) through a
reader that counts every byte the gzip layer inflates, and refuses once the
count passes the cap. A `TarInfo` subclass checks each extended header's
declared size in `_proc_member`, the dispatch point tarfile provides for
subclasses, before the body is read, and limits how many there can be. Sparse
entries are refused: bundles never use them, and the GNU sparse readers grow
unbounded lists from attacker-chosen counts. Those readers are private
methods, so a test asserts they still exist, and sparse bundles are refused,
which fails loudly if a Python upgrade renames them. The same 300 MB
constructions are now refused before the header body is read, with no
measurable RSS growth.

## Refusal behavior

- Import routes return 400 with "<route> exceeds the import size limit", naming
  the limit, before any database session opens. Malformed YAML still returns
  "Invalid YAML". A document or section of the wrong shape (a scalar where a
  mapping or list is required) is a 400, not a server error.
- `/validate` and `/restore-yaml` return 400 with "YAML export exceeds the
  backup size limit" before any section is restored.
- A list of groups, profiles or rules whose items are not mappings is a 400,
  checked before any database session or upstream client call.
- DBAS decode raises `ArtifactCategoryRefused`, naming the category. The restore
  task fails in preflight, before `get_client()`, any destination read or any
  orchestrator call. It is never treated as a silently empty category. A
  malformed category member still decodes as empty, as before.
- Backup creation is a restorability preflight. Before each DBAS category is
  dumped, `measure_object` walks the redacted object and applies the restore
  side's exact budgets. It counts one node per mapping, sequence and scalar
  (mapping keys included), and one per alias for a container repeated by
  identity, which is exactly what restore composes from the dump; tests pin
  the equality against `measure_bounded`. A category ECM would refuse to
  restore fails the backup with `BackupNotRestorableError`, naming the category
  and the limit, and no artifact is written. The legacy YAML export (`GET
  /api/backup/export`, which returns 413, and the scheduled YAML backup task)
  applies the same walk with `LEGACY_MAX_NODES`, so ECM never writes an export
  it would refuse.
- Creation also parses each category's own dump once, to redact it. On the
  credential-carrying path that dump is unredacted and can alias a stream
  shared by many channels, while the redacted bytes ECM writes carry no aliases
  (redaction copies every container). That self-generated parse raises its
  alias budget to the node ceiling, so refusing it implies the expanded final
  document is over the ceiling too. A backup is never refused for aliases its
  written bytes lack.

## Tests

- `backend/tests/unit/test_yaml_safety.py`: budget constants, per-caller
  ceilings, the shared alias budget with positive and negative controls,
  depth, recursion, measure-without-construct, the object walk's count and
  refusals equal to `measure_bounded` on several fixtures, DBAS readers, and
  the editor contract.
- `backend/tests/routers/test_yaml_import_limits.py`: HTTP node and byte limits
  on the four import routes with real-size positive controls, wrong-shape
  documents and list items, `/validate`, `/restore-yaml`, the separate legacy
  ceiling, analyze/from-bundle member, total, count and upload caps, and the
  export preflight.
- `backend/tests/routers/test_bundle_tar_bombs.py`: GNU long-name, long-link,
  PAX local and PAX global header bombs refused with a 413 while the counting
  reader and `tracemalloc` show inflation stayed bounded, small extended
  headers and PAX-format bundles still accepted, extended-header chains, sparse
  entries, the counting reader itself, and the private tarfile hooks the guard
  relies on.
- `backend/tests/dbas/test_yaml_budget_roundtrip.py`: a 600-channel,
  3-streams-per-channel backup built through the real producer and decoded
  through `decode_artifact_to_plan`, with the exact per-channel node cost of
  the producer's output (52 nodes) measured to prove the ceiling admits at
  least 150,000 such channels and that a 25,000-channel catalog (over 1,000,000
  composed nodes) exceeds the original single 1M budget but fits; the
  producer refusing one node under a catalog's measured count and admitting it
  at that count; creation-side refusal through the backup
  task, a credential backup whose dump aliases shared streams, and DBAS alias
  and node refusals failing the restore task before any destination call.
- `backend/tests/integration/test_pipeline_yaml_editor.py` preserves the
  stricter editor contract.

A valid backup exceeding these limits needs an explicitly reviewed limit
increase and a regression fixture. Removing a limit is not a recovery path.
