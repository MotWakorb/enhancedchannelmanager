# Recover profile assignments after creating channels

Channel creation and profile visibility updates are separate operations. A channel
can be created successfully while its requested profile assignments fail or cannot
be verified.

## Resolve the notice

If **Profile assignments unresolved** appears after **Done → Apply All**:

1. Do not create the channels again: they already exist.
2. Open **Review requested profile assignments** to see the committed channel IDs
   and their requested profiles. **No profiles** means the channel should be
   hidden from every profile.
3. Finish any new Edit Mode session, then select **Retry profile assignments**.
   This reads current membership, updates only mismatched profile visibility, and
   checks the result again. It does not resubmit channel creation.

The notice disappears when a fresh membership read confirms the requested
visibility. A rejected request, a successful response with no matching change,
or an unreadable final result leaves the original intent available for retry.
An update whose reply was lost needs no repeat write if readback confirms it.
If a requested profile is missing, the notice identifies the channel and
profile ID. Retry still processes other channels. If the original profile can
be restored with the same ID, retry then; otherwise set visibility manually for
that channel. A new profile with the same name does not have the same ID.

Retry uses the original resolved profile selection, not newly configured defaults.
Visibility in unselected profiles is also checked, because Dispatcharr may enable
new channels automatically. This is best-effort reconciliation, not a lock against
another operator changing membership afterward.

## Reload, account changes, and discard

Retry details are saved in this tab's session storage, separately for each ECM
operator and Dispatcharr destination. Reloading restores the notice but does not
automatically replay profile writes. Closing the tab ends this recovery path;
it is not a server-side queue or a backup. If storage is unavailable, the notice
asks you to keep the page open.

Creating more channels only starts checks for the new creates. Use **Retry profile
assignments** for older saved requests. Recovery requests are checked against the
ECM sign-in and Dispatcharr connection that created the channels, even if this
tab still shows old settings. If the original sign-in or connection is no longer
available, or there is no saved connection binding, review the requested
assignments and set visibility manually.

This notice covers only channels whose committed IDs reached ECM's browser.
If Apply reports that the channel list could not refresh, reload to see what
already committed. If the create result itself was lost, check the lineup
before attempting another create; profile recovery does not infer which channel
was created.

**Discard saved profile intent** asks for confirmation, then forgets the retry
plan. It does not delete channels, reverse memberships already applied, or discard
unrelated staged edits. Use it if you intentionally resolve visibility another way
and no longer want ECM to apply the original selection.
