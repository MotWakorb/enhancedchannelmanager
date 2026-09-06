# Channel Pipeline YAML Editor

Open **Channel Pipeline > YAML** to edit the complete local rule collection.
The normal list's search and enabled/disabled filter do not limit this document.
Both standard and Event Sync rules are included.

## Workflow

1. Keep a rule's `id` when editing or renaming it. Names are not identity.
2. To copy a rule, copy its block and remove the copied block's `id` line.
   The copy receives a fresh ID and no runtime statistics, managed-channel
   ownership, or execution history. Leaving the same ID in two blocks is an error.
3. Edit configuration, including `priority` to change execution order. Moving
   blocks alone does not change priorities. `kind` must be `standard` or
   `event_sync` and agree with the presence of `event_sync_config`.
4. Use **Find (literal)**, **Find next**, and **Replace all** for plain text
   changes. Neither search patterns nor replacement values execute regex.
5. Select **Save YAML**. Syntax, configuration, scalar, regex, reference, and
   stale-revision errors retain the draft and reject the save.

Removing a rule block proposes deletion. Save first shows the omitted rules'
names and reference warnings. **Cancel** keeps the draft and saved collection;
**Confirm deletions and save** retries the same revision with explicit consent.
An empty `rules: []` document uses the same confirmation for delete-all.

Switching between **Rule list** and **YAML** preserves the draft in the mounted
Channel Pipeline view. **Reload YAML** asks before replacing a dirty draft;
**Keep editing** cancels that replacement. Browser reload/close uses the native
unsaved-changes warning. This is not persistent draft storage or a backup system.

## Identity and Deletion

Edits keep rule IDs, runtime statistics, managed ownership, and history.
The editor excludes runtime fields and timestamps and rejects them if submitted.
Unknown supplied IDs are rejected, not inserted or matched by name.

Deleting a rule does not delete Dispatcharr channels or run any pipeline action.
Existing local foreign-key behavior still applies: Event Sync review/decision
and exclusion rows for that rule are removed; execution rows remain, with nullable
rule references cleared. Selected schedules can retain stale IDs. The editor
warns about that possibility but does not repair schedules or prepared decisions.

## Conflicts and Limits

The editor fingerprints configuration and membership, not runtime statistics or
timestamps. Another editor or a committed CRUD/import/reorder change invalidates
the loaded revision. There is no force-overwrite option. Retain needed draft text,
reload the current rules, and reapply the intended changes.

Catalog availability and revisions are valid reasons to reject a save. Catalog
reads occur before the local SQLite transaction, so an external catalog can
change after validation; Save is not a distributed transaction with Dispatcharr
and does not perform execution preflight. Unchanged stored catalog references
are preserved on unrelated edits. Changed references are checked against the
catalog snapshot. Existing Event Sync schema validation also checks its local
dummy-EPG profile reference.

YAML input is limited to 2,000,000 UTF-8 bytes, 100,000 composed nodes and 40
nesting levels. Duplicate mapping keys, aliases (including recursive aliases),
unsafe tags and non-JSON configuration values are rejected. Copy blocks rather
than using YAML anchors/aliases. These limits belong to this editor, not portable
import or other API endpoints.

## API and Evidence

GET `/api/channel-pipeline/rules/yaml` returns `yaml_content` and `revision`.
PUT accepts those fields plus `confirm_deletions` (default `false`) and uses the
existing admin-when-auth-enabled authority boundary. The document has exactly
`version: 1` and `rules`. The editable field set comes from the existing create
request, plus local `id` and `kind`.

The private SQLite connection reserves the writer before comparing a fresh
revision. Validation precedes mutation; creates are flushed before removals so
the same save cannot reuse a deleted maximum ID. Updates, creates, deletions and
their local FK effects commit together. Save does not add out-of-transaction
journal entries or claim journal atomicity.

The file-SQLite tests in
`backend/tests/integration/test_pipeline_yaml_editor.py` exercise identity,
rollback (including after FK cascades), stale revisions, simultaneous editors,
generic StaticPool reader rollback isolation, and actual worker runtime-completion
methods. Runtime writes update only runtime columns on an existing ID and its
creation timestamp, rather than merging detached configuration snapshots.
This does not redesign pipeline execution or cancel an already-running pipeline.

The GH971 browser case in `e2e/smart-sort-points.spec.ts` uses the real mounted
rule API and a private file database, sharing the isolated harness with GH980.
Its upstream catalog stub has no channel-write methods. Component tests cover
literal replacement, draft preservation and confirmation cancellation.

Portable **Import** and **Export** remain separate. Export still omits local
rule identity for sharing; import still has its existing name-based semantics.
No new dependency, migration, backup format, undo mechanism, or scheduler repair
is introduced by this editor. The frozen delivery boundary is recorded in
[the GH971 plan](gh971-yaml-editor-plan.md).
