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
| DBAS category members, DBAS backup creation, legacy YAML export, `/api/backup/validate`, `/api/backup/restore-yaml` | `BACKUP_MAX_NODES` = 10,000,000 | A backup carries a whole Dispatcharr catalog. A channel with three embedded streams costs about 42 to 105 nodes, so this admits roughly 95,000 to 240,000 channels per category. |
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

Most of the cost is composition, so a document near the ceiling is expensive
even when it is accepted. Backup creation parses each category once to redact
it and then measures the final bytes, so a category near the ceiling costs
roughly twice the figures above, and restore costs them once more. Parsing runs
on the request or task's event loop; moving it to a worker thread is tracked
separately. The ceiling exists to make cost bounded and predictable, not small.

## Byte limits

| Path | Limit | On excess |
|-|-|-|
| `yaml_content` of the four import routes | `IMPORT_MAX_CHARS` = 4 MiB of text | 422 before parsing |
| `/api/backup/validate`, `/api/backup/restore-yaml` upload | 128 MiB, read in 1 MiB chunks | 413 before parsing |
| analyze/from-bundle upload | 128 MiB compressed, read in chunks | 413 |
| analyze/from-bundle archive | 256 members, 512 MiB declared uncompressed in total | 413 before the oversized member is inflated |
| analyze/from-bundle `rules.yaml` | 4 MiB | 413 |
| analyze/from-bundle `channel_groups_diagnostic.json` | 16 MiB | 413 |
| DBAS artifact upload and members | Existing 2 GiB upload, 1 GiB per member and in total, 100x ratio, 10,000 entries | Unchanged |

Real exports are a few KiB, so the 4 MiB import cap is far above them. It also
stays above `IMPORT_MAX_NODES` at about 40 characters per node, so the node
ceiling, not the byte cap, binds first for plausible documents. Measured
exports run about 10 to 13 bytes per node, so the 128 MiB legacy upload cap
stays above `BACKUP_MAX_NODES` the same way. The bundle caps admit the
generator's 50 MiB log cap plus a large catalog. A scalar's own length is bound
only by these byte limits.

## Refusal behavior

- Import routes return 400 with "<route> exceeds the import size limit", naming
  the limit, before any database session opens. Malformed YAML still returns
  "Invalid YAML".
- `/validate` and `/restore-yaml` return 400 with "YAML export exceeds the
  backup size limit" before any section is restored.
- DBAS decode raises `ArtifactCategoryRefused`, naming the category. The restore
  task fails in preflight, before `get_client()`, any destination read or any
  orchestrator call. It is never treated as a silently empty category. A
  malformed category member still decodes as empty, as before.
- Backup creation is a restorability preflight. Each DBAS category's final
  bytes are checked with the restore side's exact budgets (`measure_bounded`,
  which composes without constructing). A category ECM would refuse to restore
  fails the backup with `BackupNotRestorableError`, naming the category and the
  limit, and no artifact is written. The legacy YAML export (`GET
  /api/backup/export`, which returns 413, and the scheduled YAML backup task)
  applies the same check, so ECM never writes an export it would refuse.

## Tests

- `backend/tests/unit/test_yaml_safety.py`: budget constants, per-caller
  ceilings, the shared alias budget with positive and negative controls,
  depth, recursion, measure-without-construct, DBAS readers, and the editor
  contract.
- `backend/tests/routers/test_yaml_import_limits.py`: HTTP node and byte limits
  on the four import routes with real-size positive controls, `/validate`,
  `/restore-yaml`, analyze/from-bundle (member, total, count, upload and gzip
  bomb), and the export preflight.
- `backend/tests/dbas/test_yaml_budget_roundtrip.py`: a 50,000-channel,
  3-streams-per-channel backup built through the real producer and decoded
  through `decode_artifact_to_plan`, creation-side refusal through the backup
  task, and DBAS alias and node refusals failing the restore task before any
  destination call.
- `backend/tests/integration/test_pipeline_yaml_editor.py` preserves the
  stricter editor contract.

A valid backup exceeding these limits needs an explicitly reviewed limit
increase and a regression fixture. Removing a limit is not a recovery path.
