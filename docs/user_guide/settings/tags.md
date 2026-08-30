# Tags

Tags, under **Channel Processing** in the Settings navigation, manages the
tag vocabularies normalization rules use for pattern matching. It's a
single-section page. There's no "On this page" rail because there's only
one place to be.

The instance this was written against ships nine built-in tag groups:
Abbreviation, Country, League, Network, Provider, Quality, Small Word,
State/Province, and Timezone Tags, each expandable to show its individual
tag entries.

## Common tasks

### Add a tag to a built-in group

1. Go to **Settings → Tags**.
2. Expand the group you want (for example, **League Tags**).
3. Add the new tag entry.
4. Save.

**Result:** The group's tag count increases, and any normalization rule
that references this group can now match the new tag.

### Export your tag groups

1. Go to **Settings → Tags**.
2. Click **Export**.

**Result:** A file containing your tag groups downloads, suitable for
backing up or transferring to another instance.

### Import tag groups

1. Go to **Settings → Tags**.
2. Click **Import** and choose a previously exported file.

**Result:** The imported groups appear in the list, expandable like the
built-in ones.

### Create a custom tag group

1. Go to **Settings → Tags**.
2. Click **New Group**.
3. Name the group and add tags to it.
4. Save.

**Result:** The new group appears in the list and is available for
normalization rules to reference, the same as a built-in group.

## Going deeper

- [`docs/normalization.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/normalization.md): how rules use tag groups for pattern matching.
- [Channel Normalization](channel-normalization.md): the two Settings-level normalization toggles.
