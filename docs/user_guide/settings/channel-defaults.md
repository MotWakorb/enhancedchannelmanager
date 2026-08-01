# Channel Defaults

Channel Defaults, under **Channel Processing** in the Settings navigation,
configures the defaults applied every time you create channels in bulk from
streams. Nothing here is retroactive. Changes apply to channels created
*after* you save.

## Common tasks

### Keep channel names in sync when a channel number changes

1. Go to **Settings → Channel Defaults**.
2. Under **Channel Naming**, check **Auto-rename channel when number
   changes**.
3. Optionally check **Include channel number in name** to prefix new
   channel names with their number (e.g. "101 - Sports Channel").
4. Save.

**Result:** Renumbering a channel whose name embeds the old number now
updates the name automatically; new bulk-created channels are prefixed with
their number if you enabled that option.

### Choose how regional stream variants are handled

1. Go to **Settings → Channel Defaults**.
2. Under **Timezone Preference**, open **Default timezone for regional
   channel variants** and choose how East/West variant streams are
   resolved by default (for example, keep both as separate channels, or
   prefer one region).
3. Save.

**Result:** Future bulk channel creation from streams with East/West
variants follows the selected default. You can still override this per
operation.

### Add new channels to specific Channel Profiles automatically

1. Go to **Settings → Channel Defaults**.
2. Under **Channel Profiles**, check every profile that newly created
   channels should belong to.
3. Save.

**Result:** Channels created afterward are automatically added to the
checked profiles, without a separate assignment step.

### Tune EPG auto-match confidence

1. Go to **Settings → Channel Defaults**.
2. Under **EPG Matching**, set **Auto-match confidence threshold** (0–100%).
   Matches at or above this score are assigned automatically; lower scores
   are left for manual review. Set to 0 to require manual review for every
   match.
3. Save.

**Result:** Subsequent EPG matching runs use the new threshold. Lowering it
matches more channels automatically but increases the chance of a wrong
match; raising it means more channels wait for manual review.

### Tune stream dedup sensitivity

1. Go to **Settings → Channel Defaults**.
2. Under **Stream Deduplication**, set **Dedup confidence threshold**
   (60–100%, default 80). Streams scoring at or above this against an
   existing channel are offered as merge candidates. ECM will not surface
   candidates below 60%. That floor is fixed (ADR-008) and isn't adjustable
   from this slider.
3. Optionally check **Suppress "pending merges queued" toast after M3U
   refresh** if you find the toast noisy. Pending merges are still queued
   and visible on the Pending Merges page either way; this only silences
   the toast.
4. Save.

**Result:** The next M3U refresh or stream-matching pass uses the new
threshold when deciding what counts as a likely duplicate.

### Set which criteria Smart Sort uses, and in what order

1. Go to **Settings → Channel Defaults**.
2. Under **Smart Sort Priority**, check the criteria you want Smart Sort to
   use (Resolution and Bitrate are on by default; M3U Priority, Audio
   Channels, Video Codec, Custom Streams, and Catch-up are available but
   off by default).
3. Click **Reorder** to drag criteria into priority order. The first
   enabled criterion is compared first.
4. Save.

**Result:** Enabled criteria, in the order you set, appear in the Smart
Sort dropdown and govern how streams within a channel are auto-sorted.

### Control how failed, black-screen, and low-FPS streams sort

1. Go to **Settings → Channel Defaults**.
2. Below Smart Sort Priority, check or uncheck **Deprioritize Failed
   Streams**, **Deprioritize Black Screen Streams**, and **Deprioritize
   Low FPS Streams** as needed. Each is on by default.
3. If more than one deprioritization category is enabled, click **Reorder**
   under **Failed Stream Ordering** to set which category sorts closer to
   the working streams.
4. Save.

**Result:** Smart Sort now pushes streams matching the enabled
deprioritization categories toward the bottom, in the order you set,
instead of ranking them purely by quality stats.

## Going deeper

- [Stream deduplication](../channels-streams/stream-dedup.md): the merge workflow these thresholds feed into.
- [Channel Pipeline](../channel-pipeline/index.md): rule-based automation that runs ahead of these defaults.
- [`docs/api.md`](../../api.md): API reference for reading or writing these settings programmatically.
