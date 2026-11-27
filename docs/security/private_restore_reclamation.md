# Private saved-restore snapshot reclamation

Applies to the private snapshots that saved DBAS restores (`POST /api/backup/restore-dbas-saved`) copy into.

The existing six-hour restore-temp sweep now also considers the known private
`ecm-saved-restore-<8-character-token>` layout. Upload and saved-restore requests
trigger the sweep; there is no new background timer or production cleanup job.

`restore_snapshot_storage.PrivateRestoreSnapshot` holds an exclusive BSD `flock`
lease on the snapshot directory through copying and the complete background wrapper lifetime.
Normal wrapper cleanup and the pre-start-cancellation callback remain idempotent.
The kernel releases the lease on process death; a later trigger can reclaim an
old orphan. An active lease wins over the age floor, including across workers.

The reclaimer opens the parent and candidate directories with no-follow directory
descriptors. It requires an owned parent without group/world write access, an
owned 0700 directory holding at most one snapshot ZIP (`ecm-restore-<8>.zip`)
and at most one decrypted plaintext temp (`ecm-restore-dec-<8>.zip`), where
`<8>` is `tempfile.mkstemp`'s eight-character `a-z0-9_` name. Every entry must be
an owned 0600 regular, singly linked file, and the directory and every entry
must be old. The decrypt gate creates its plaintext with `mkstemp` (owner-only
0600) beside the snapshot ZIP, so a process killed mid-restore can leave both;
task cleanup unlinks the ZIP before the plaintext, so a kill between the two can
leave the plaintext alone, which is also reclaimed rather than left on disk. Two
ZIPs, two plaintexts or any other name leave the directory untouched. Unknown or
fresh contents, symlinks, other directories and leased candidates are left alone.
Only those known entries are unlinked relative to the retained descriptor; the
candidate directory name is identity-checked before `rmdir`. The sweep does not
use recursive deletion or traverse the operator's saved-backup directory.

This requires working BSD `flock` locks; lease acquisition fails rather than
silently dropping active-run protection (the request is logged and fails with
HTTP 500, and the partial snapshot is removed). On an NFS-hosted `/config`,
Linux emulates `flock` with POSIX record locks, which do not conflict within one
process, so the lease is not guaranteed there. It assumes the private config parent is
trusted against malicious same-UID writers. Before each saved-restore snapshot,
`prepare_private_parent` opens the snapshot parent with no-follow, refuses a
symlinked or foreign-owned parent (the restore fails with HTTP 500 and nothing
is queued), and tightens an owned parent with any group/world bits to 0700,
because `mkdir(exist_ok=True)` never changes an existing directory's mode and a
writable parent would let another writer rename a snapshot directory and plant
a replacement before the task reopens it. The reclaimer itself still refuses a
group/world-writable parent, which only an upload-only trigger can now meet.
The legacy flat-upload age policy is unchanged; this is not a claim that every
historical upload path now holds a lease.

Each snapshot copies up to the 2 GiB restore cap, and the MCP service principal
may request previews, so the saved route refuses before copying when it cannot
keep the copy bounded: a backup over the cap (HTTP 413, "Saved backup is too
large"), a DBAS restore already active in the task engine (HTTP 409, the
engine's "Task is already running"), another snapshot still being copied in this
process (HTTP 409, a non-blocking `asyncio.Lock` held from snapshot creation
until the task is scheduled), or too little free space for the copy plus a
64 MiB margin (HTTP 507). Both the active-task check and the lock are per
process, not cross-worker. A request arriving after one snapshot's task is
scheduled but before the engine marks it active can still copy once; the
engine then refuses it and its wrapper removes that snapshot.
An `OSError` while creating or filling the snapshot (an unsupported lease, a full
disk) is logged and returned as HTTP 500 with the snapshot removed.

Verification: `backend/tests/routers/test_private_restore_reclamation.py` covers
old/fresh/unknown layouts, directory/file symlinks, hard links, empty directories,
snapshot-plus-plaintext and plaintext-only orphans, real saved-route triggering
and original retention. A separate interpreter holds
an aged snapshot lease, is deliberately killed (no Python finalizer), and the
same sweep then reclaims its orphan. `test_saved_restore_binding.py` covers
tightening of loose (0777/0775/0755) parents and refusal of symlinked or
foreign-owned parents, and the early 409/413/507 refusals and the logged 500 on
lease or copy failure. These are private test files/processes, not
production cleanup. Existing saved binding/auth/plain/encrypted tests are retained.
