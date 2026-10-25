# YAML import and restore structure budgets

Bead: `enhancedchannelmanager-10tln`. These bounds complement existing request,
upload and ZIP-member byte limits; they are not a general process-memory limit.

Import/restore uses the pure-Python `yaml_safety.BoundedSafeLoader`, with at most
**1,000,000 composed node visits**, **64 levels**, and **1,000,000 expanded graph
node visits**. Its graph check runs before constructors and merge-key flattening.
It memoizes graph nodes while counting each alias edge's expanded weight, so an
exponential alias DAG is refused without traversing its exponential expansion.
Recursive aliases are refused. Ordinary acyclic aliases and merge keys retain
SafeLoader behavior. These are per-document budgets, not aggregate ZIP budgets.

The rule editor reuses the loader with its previous **100,000 nodes /40 levels /
no aliases** policy and keeps its duplicate-key and scalar-validation behavior.
No new native parser or dependency is introduced.

Synchronous YAML import/validation routes return HTTP 400 before import/restore
mutations. DBAS decode refusals fail the background task's preflight before any
destination client or orchestrator call, rather than becoming a silently empty
category; the initiating task request need not return HTTP 400. Authentication
reads and task bookkeeping can precede parsing. Ordinary malformed-category
compatibility remains unchanged. Existing decrypt, archive-integrity,
authorization and dry-run/apply controls still apply.

Tests: `backend/tests/unit/test_yaml_safety.py` covers representative HTTP import
boundaries, backup validation, all three DBAS category readers, node/depth limits,
recursive/exponential aliases, ordinary merge aliases and 20,000 valid stream
records. `test_pipeline_yaml_editor.py` preserves the stricter editor contract.
The production call-site inventory includes tags, normalization, dummy EPG,
pipeline YAML/bundle imports, backup production/validation and DBAS readers.

These structure bounds do not cap a single scalar's bytes or all downstream
business-logic work. Existing byte/ZIP limits are not silently lowered. A valid
backup exceeding these documented structure limits needs an explicitly reviewed
limit increase and regression fixture; removing limits is not a recovery path.
