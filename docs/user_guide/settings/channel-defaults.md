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
   candidates below 60%. That floor is fixed and isn't adjustable
   from this slider.
3. Optionally check **Suppress "pending merges queued" toast after M3U
   refresh** if you find the toast noisy. Pending merges are still queued
   and visible on the Pending Merges page either way; this only silences
   the toast.
4. Save.

**Result:** The next M3U refresh or stream-matching pass uses the new
threshold when deciding what counts as a likely duplicate.

### Choose how Smart Sort ranks streams

Smart Sort has two strategies:

- **Priority** compares enabled criteria from first to last. Use it when one
  criterion must win before the next criterion is considered, such as
  resolution first and bitrate only as a tie-breaker.
- **Points** adds every matching rule's signed points. Use it when several
  qualities should trade off against each other, such as rewarding resolution
  and bitrate while subtracting points for probe-health problems.

Both configurations are retained when you switch strategies. Switching to
Points does not erase the Priority order, and switching back to Priority does
not erase the point rules.

1. Go to **Settings → Channel Defaults**.
2. Under **Smart Sort**, choose **Priority** or **Points**.
3. Configure the selected strategy as described below.
4. Save.

Smart Sort is used by manual **Smart Sort**, probe-completion reordering when
**Automatically reorder streams in channels after probe completes** is on,
scheduled probes, Channel Pipeline rules that select **Smart Sort**, and Event
Sync rules configured to use **Smart Sort**.

#### Configure Priority

Check the criteria to use, then click **Reorder** to place them from most to
least important. Resolution, Bitrate, and Framerate are enabled by default.
The first enabled criterion is compared first; the next enabled criterion is
consulted only when the preceding values tie. Higher values sort first for all
eight criteria.

The health controls are evaluated before the enabled criteria:

- **Deprioritize Failed Streams** puts failed, timed-out, pending, and
  not-yet-probed streams below working streams.
- **Deprioritize Black Screen Streams** and **Deprioritize Low FPS Streams**
  put those detected conditions into their own lower health categories. They
  apply only while **Deprioritize Failed Streams** is on.
- **Failed Stream Ordering** controls which lower health category sits closer
  to working streams. The enabled criteria still order streams within the same
  health category.

Turn a category off when you want its streams ranked by the enabled criteria
instead of being put into that health category.

#### Configure Points

Click **Add rule** for each condition. A matching rule adds its signed integer
to the stream's score: positive points raise the stream and negative points
lower it. Every matching rule is included, the highest total score sorts first,
and a stream with no matching rules scores 0.

Displayed rule order is organizational only. It does not create precedence or
change the result. The Priority health controls and health buckets do not apply
in Points mode; add explicit Failed Streams, Black Screen, or Low FPS rules if
health should affect the score.

Numeric and Video Codec conditions support every operator shown below. Boolean
conditions support **Equals (=)** only.

| UI operator | Saved operator | Meaning |
| --- | --- | --- |
| Equals (=) | `eq` | actual value equals the rule value |
| Does not equal (!=) | `ne` | actual value does not equal the rule value |
| Greater than (>) | `gt` | actual value is greater than the rule value |
| At least (>=) | `gte` | actual value is greater than or equal to the rule value |
| Less than (<) | `lt` | actual value is less than the rule value |
| At most (<=) | `lte` | actual value is less than or equal to the rule value |

#### Criteria, values, and units

| Condition | Rule value and units | Implemented meaning |
| --- | --- | --- |
| Resolution | Vertical pixels, for example `1080` or `2160` | Probed video height. |
| Bitrate | Kilobits per second (kbps) | Probed video bitrate when available; otherwise overall bitrate. The saved and displayed value remains in kbps. |
| Framerate | Frames per second (FPS) | Probed frame rate; decimals such as `59.94` are supported. |
| Video Codec | Codec name | Compared by this quality rank: AV1, HEVC/H.265, VP9, H.264/AVC, VP8, MPEG-2 Video. Aliases at the same rank compare as equal. |
| M3U Priority | Unitless priority value | The configured priority for the stream's M3U account; an account without a configured value uses 0. |
| Audio Channels | Channel count | Probed audio-channel count, for example 2 or 6. |
| Custom Streams | `True` or `False` | Whether Dispatcharr marks the stream as custom. |
| Catch-up | `True` or `False` | Whether Dispatcharr marks the stream as catch-up enabled. |
| Failed Streams | `True` or `False` | `True` for probe status failed, timeout, or pending. |
| Black Screen | `True` or `False` | The saved black-screen detection result. |
| Low FPS | `True` or `False` | The saved low-FPS detection result, based on the configured FPS threshold. |

If a stream has no usable value for a Points condition, that rule does not
match. Unknown is not treated as zero or `False`: for example, a stream with no
Custom Streams metadata does not match either `Custom Streams = True` or
`Custom Streams = False`. Missing or malformed probe metrics and unknown video
codecs behave the same way. In Priority mode, missing criterion values compare
as 0, subject to the health-category behavior described above.

After the selected strategy has finished, equal streams always sort by numeric
stream ID in ascending order. This final tie-break makes the result
deterministic; displayed rule order and the incoming stream order do not break
ties.

#### Worked example: additive scoring

Rules:

| Rule | Points |
| --- | ---: |
| Resolution at least 1080 vertical pixels | +20 |
| Bitrate at least 6000 kbps | +25 |
| Failed Streams equals `True` | -40 |

- Stream 101 is healthy, 1080 pixels high, and 6500 kbps. It matches the first
  two rules: `20 + 25 = 45` points.
- Stream 202 is failed, 2160 pixels high, and 4500 kbps. It matches Resolution
  and Failed Streams: `20 - 40 = -20` points. Its bitrate is below 6000 kbps,
  so the positive bitrate rule does not match.

The resulting order is `[101, 202]` because 45 is greater than -20.

#### Worked example: a quality rule overrides health penalties

Rules:

| Rule | Points |
| --- | ---: |
| Resolution at least 2160 vertical pixels | +50 |
| Failed Streams equals `True` | -10 |
| Black Screen equals `True` | -10 |
| Low FPS equals `True` | -10 |

- Stream 101 is healthy at 720 pixels. It matches no rule and scores 0.
- Stream 202 is 2160 pixels high and is failed, black-screen, and low-FPS. It
  matches all four rules: `50 - 10 - 10 - 10 = 20` points.

The resulting order is `[202, 101]`. This is intentional Points behavior: the
quality reward outweighs the explicit health penalties, and there is no hidden
Priority health bucket to reverse the result.

## Going deeper

- [Stream deduplication](../channels-streams/stream-dedup.md): the merge workflow these thresholds feed into.
- [Channel Pipeline](../channel-pipeline/index.md): rule-based automation that runs ahead of these defaults.
- [`docs/api.md` Smart Sort settings](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/api.md#smart-sort-settings): API reference for reading or writing these settings programmatically.
- [v0.18.2 Smart Sort release evidence](https://github.com/MotWakorb/enhancedchannelmanager/blob/main/docs/validation/smart-sort-v0.18.2-readiness.md): automated consumer and release-gate evidence for the tested candidate.
