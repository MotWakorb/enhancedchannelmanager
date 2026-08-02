# Next Steps

ECM can do a lot; you don't need most of it on day one. This is a short map
of where to go depending on what you're trying to do next.

## Common tasks

### Build your first real channels

1. Go to [Set up your first channels](your-first-channels.md).

**Result:** by the end of that walkthrough you'll have a small set of real,
working channels (an M3U account, an EPG source, a few channels and
channel groups, and streams attached to them) and understand where the
power features (Channel Pipeline, Normalization, EPG matching) pick up from
there.

### Protect your work before you need to

Set up backups now, while nothing is on fire. A restore is only as good as
the backup behind it, and the best time to configure one is before you have
data you'd regret losing.

1. Select **Settings** in the sidebar, then **Backup & Restore** (the
   **Upkeep** group).
2. See [Backup & Restore](../backup-restore/index.md) for the full section.
   Start with [Take a backup](../backup-restore/take-a-backup.md).

**Result:** you have a backup you could restore from if this container were
lost or misconfigured tomorrow.

### Automate channel creation instead of building by hand

Once you're comfortable creating channels manually, the Channel Pipeline
rules engine can do it for you as new streams appear.

1. See [Channel Pipeline](../channel-pipeline/index.md) for rule concepts
   and the standard-rules tutorials.

**Result:** new streams matching your rules become channels automatically on
the next M3U refresh, instead of requiring manual work each time.

## Going deeper

- [Getting Started](index.md): back to the section landing page.
- [Channels & Streams](../channels-streams/index.md): day-to-day channel and stream management once your first batch exists.
- [Troubleshooting](../troubleshooting/index.md): if something stops working later, this is the first place to look.
