# Private saved-restore snapshot reclamation

Bead `sumq7`; stacked on the saved-file binding candidate in PR #1045.

The existing six-hour restore-temp sweep now also considers the known private
`ecm-saved-restore-<8-character-token>` layout. Upload and saved-restore requests
trigger the sweep; there is no new background timer or production cleanup job.

`restore_snapshot_storage.PrivateRestoreSnapshot` holds an exclusive advisory
directory lock through copying and the complete background wrapper lifetime.
Normal wrapper cleanup and the pre-start-cancellation callback remain idempotent.
The kernel releases the lease on process death; a later trigger can reclaim an
old orphan. An active lease wins over the age floor, including across workers.

The reclaimer opens the parent and candidate directories with no-follow directory
descriptors. It requires an owned parent without group/world write access, an
owned 0700 directory, and at most one owned 0600 regular, singly linked ZIP with
the exact generated name. Both directory and artifact must be old. Unknown or
fresh contents, symlinks, other directories and leased candidates are left alone.
Only the known artifact is unlinked relative to the retained descriptor; the
candidate directory name is identity-checked before `rmdir`. The sweep does not
use recursive deletion or traverse the operator's saved-backup directory.

This requires working POSIX advisory locks; lease acquisition fails rather than
silently dropping active-run protection. It assumes the private config parent is
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

Verification: `backend/tests/routers/test_private_restore_reclamation.py` covers
old/fresh/unknown layouts, directory/file symlinks, hard links, empty directories,
real saved-route triggering and original retention. A separate interpreter holds
an aged snapshot lease, is deliberately killed (no Python finalizer), and the
same sweep then reclaims its orphan. `test_saved_restore_binding.py` covers
tightening of loose (0777/0775/0755) parents and refusal of symlinked or
foreign-owned parents. These are private test files/processes, not
production cleanup. Existing saved binding/auth/plain/encrypted tests are retained.
