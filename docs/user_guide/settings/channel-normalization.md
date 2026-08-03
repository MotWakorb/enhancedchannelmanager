# Channel Normalization

Channel Normalization, under **Channel Processing** in the Settings
navigation, holds two Settings-level toggles plus the full normalization
rules engine. This article covers only the two toggles. Rule authoring,
condition/action types, testing, and the developer reference all live in
the dedicated normalization guide linked below. That guide is the source of
truth; this page exists so the two Settings-only toggles have a documented
home in the Settings navigation.

## Common tasks

### Apply normalization rules by default when creating channels

1. Go to **Settings → Channel Normalization**.
2. Under **Default Behavior**, check **Apply normalization by default when
   creating channels**.
3. Save.

**Result:** The "Apply normalization rules" checkbox is now pre-checked
whenever you create channels from streams. You can still toggle it off for
an individual operation.

### Keep country prefixes instead of stripping them

By default, the built-in Country tag group strips country prefixes from
channel names entirely.

1. Go to **Settings → Channel Normalization**.
2. Under **Country Prefix Format**, check **Normalize country prefix
   format**.
3. Save.

**Result:** Instead of removing country prefixes, normalization now keeps
them with a consistent separator format.

## Going deeper

- [`docs/normalization.md`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/normalization.md): the full normalization guide: rule groups and ordering, condition/action types, testing a rule before committing, re-normalizing existing channels, troubleshooting, and the developer reference.
- [`docs/normalization.md#quick-start`](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/normalization.md#quick-start): write your first rule.
- [Tags](tags.md): the tag vocabularies these rules match against.
