# M3U Changes

M3U Changes is ECM's read-only log of what each provider added or removed on their end since ECM last refreshed that account: groups and streams appearing or disappearing. It answers "is this change safe," not "let me fix it here." Acting on a change always happens back in M3U Manager.

## Common tasks

### See what a provider changed recently

1. Open **M3U Changes**.
2. Pick a **Time Range** (Last 24 hours through Last 90 days) and, optionally, narrow to one **Account**, one change **Type** (Groups Added, Groups Removed, Streams Added, Streams Removed), or **Status** (Enabled Only / Disabled Only).
3. Read the four summary tiles for a scoped count before scrolling the table: **Groups Added**, **Groups Removed**, **Streams Added**, **Streams Removed**.

![M3U Changes with the Time Range, Account, Type, and Status filters, the four change-count summary tiles, and the change list below them](../../images/user_guide/m3u-changes/1-m3u-changes-overview.png)

**Result:** The summary tiles use your Time Range and Account selection. The table and its footer are unbounded by time and respect the Account/Type/Status filters. Saved group exclusions apply to both the table and summary. It is normal for the footer's "N total changes" to differ from the summary above it.

### Hide noisy groups without losing their history

1. On a change row, open **⋮ → Exclude this group**.
2. For several groups, open **Excluded groups (N)** in the toolbar. Search for a name such as "Sports" and choose **Select All Visible**, or check individual groups. The account appears beside each name.
3. Uncheck a group to show it again. **Clear exclusions** restores every hidden group.

Selections save on the server and survive reloads. They are shared by this ECM instance, not private to one browser or login. When authentication is enabled, administrators can change them; other viewers see the filtered history.

Each exclusion matches the **exact group name within one M3U account**. The same name on another provider stays visible. The checklist includes recorded groups and saved exclusions, including groups whose history has since expired. Search narrows the checklist, not the table. Save at most 1000 additions or removals at once; narrow a larger selection with search.

Exclusions filter the table, totals, pagination and summary. They **do not** stop collection, delete history, disable provider groups, or change digest notifications. Normal history-retention settings still apply. After changing exclusions through MCP or another browser, use **Refresh** to reload the saved list and results.

#### Use MCP

- `list_m3u_change_groups`: discover exact account/name identities; supports account, literal search and pagination.
- `get_m3u_change_exclusions`: inspect the same saved list as the tab.
- `update_m3u_change_exclusions`: add/remove identities, or pass `clear: true` alone.

Example arguments for excluding one group:

```json
{"add": [{"m3u_account_id": 1, "group_name": "Sports"}]}
```

Use `remove` with the same identity to restore it. These are reversible display preferences, not the regex rules in M3U Digest settings.

### Decide whether a change needs your attention

1. Look at the **Enabled** column on each row. **Yes** means that group (or the streams in it) is already syncing into ECM's stream list. **No** means the provider added something ECM has recorded, but that isn't flowing through to your streams yet. Most commonly, that's a brand-new group.
2. To bring in a **No** you want, go to **M3U Manager** → the account's **Manage Groups** icon, toggle that group **Enabled**, then **Save & Refresh**. This is the same flow covered in [Set Up Your First Channels](../getting-started/your-first-channels.md#3-choose-which-stream-groups-to-sync).
3. M3U Changes itself has no accept/reject control. It is a log, not a review queue. Confirming a change is "safe" means recognizing what it is (a seasonal group coming back, an expected provider reshuffle) and, if you want it live, making that selection in M3U Manager.

**Result:** For every row, you know whether a provider's change is already affecting your lineup or is sitting inert until you opt the group in from M3U Manager.

### Inspect exactly what changed in one group

1. Click a row to expand it.
2. Read **Change Details** (account, group, time, stream count) and the **Stream Names** list below it, which names every stream involved in that change (the first 20 inline; **+N more** expands the rest).

![An expanded M3U Changes row for a "Streams Added" change, showing the Change Details panel and the list of individual stream names added](../../images/user_guide/m3u-changes/2-m3u-changes-expanded-detail.png)

**Result:** You can confirm, by name, exactly which streams were added or removed. This is the fastest way to tell "this is the seasonal channel coming back" from "this is provider noise I don't want."

## Going deeper

- [Set Up Your First Channels](../getting-started/your-first-channels.md#3-choose-which-stream-groups-to-sync): where you actually act on a change, enabling or disabling a stream group in M3U Manager.
- [Notifications & Alert Methods](../notifications/index.md): the M3U Digest is an email/Discord summary of the same change data, on a schedule, for operators who don't want to check this tab manually.
- [`docs/api.md#m3u-digest`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/api.md#m3u-digest): the API reference for M3U Changes and the digest settings, useful if you want to query this data programmatically instead of through the UI.
