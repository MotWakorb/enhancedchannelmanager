# Troubleshoot EPG issues

| Symptom | Likely cause | Fix |
|---|---|---|
| Channel shows **no guide at all** | The channel has never been matched to any EPG source, or its dummy EPG source was never added to Dispatcharr. | Run [channel-to-EPG matching](channel-to-epg-matching.md); for dummy EPG, confirm you clicked **Add to Dispatcharr as EPG source** (or added the profile's XMLTV URL manually). A profile that exists in ECM but was never wired into Dispatcharr produces nothing downstream. |
| Guide shows the **wrong programme** (times look off, or it's clearly a different feed) | The channel is linked to the wrong EPG row, commonly a West feed sharing an East feed's guide, or vice versa. | Run the [duplicate-EPG-link audit](finding-mislinked-channels.md) to find channels sharing one EPG row, then re-match or manually re-link the affected channel. Read [timezone/region awareness](channel-to-epg-matching.md#timezone-and-region-awareness) to understand why this happens. |
| A **bulk match accepted a bad suggestion** | A low-confidence candidate (well under 100%) was accepted via **Accept Best Guesses** without review. | Re-open [Bulk EPG Assignment](channel-to-epg-matching.md#why-review-matters-read-the-confidence-score) for the affected channels and manually pick (or clear) the correct entry; don't trust a low score just because it was the top-ranked option. |
| EPG source stuck on **"Downloading… (see Dispatcharr for progress)"** | The pull is still in progress, or Dispatcharr itself is stuck fetching the feed. | Give it a few minutes for a first pull on a large feed (some public XMLTV feeds carry 10,000+ channel entries). If it never clears, check Dispatcharr's own logs. ECM surfaces Dispatcharr's fetch status; it doesn't perform the fetch itself. |
| EPG source refresh is **slow** | Large feeds take longer to parse; Schedules Direct additionally rate-limits. | For XMLTV, this scales with feed size (nothing to configure). For Schedules Direct, refreshes are capped to a 2-hour minimum interval by design; see [Schedules Direct](schedules-direct.md#refresh-and-match). |
| **Bulk EPG Assignment** matching feels slow on a large channel selection | Matching runs a full scan against every selected EPG source; on a very large guide (e.g. a full US OTA feed with tens of thousands of entries) this can take a while. | Narrow the **EPG Sources** checklist in the configure step to only the sources that could plausibly carry the channels you selected, instead of matching against every active source. |
| Schedules Direct source added but **still shows 0 channels** | An SD source with no lineups added pulls no data. This is expected, not a bug. | Add at least one lineup (see [Add lineups](schedules-direct.md#add-lineups)), then refresh. |
| Schedules Direct **"lineup changes" or refresh rejected** | You've hit SD's daily lineup-change limit, or tried a refresh interval under the 2-hour minimum. | Wait for the daily limit to reset (the Lineups panel shows changes remaining), or raise the refresh interval to at least 2 hours. |
| Dummy EPG template shows **literal `{placeholder}` text** in the generated guide instead of a real value | A template references an unknown pipe/transform (commonly the retired `lookup:` pipe), or a group name that doesn't exist in the pattern. | See [Author dummy EPG templates](dummy-epg-templates.md#write-the-output-templates). The engine falls back to raw template text rather than failing the whole feed, so check the profile's live preview for the same issue before it reaches an end user. If the template uses `lookup:`, see [Lookup Tables retired](lookup-tables-retired.md). |

## Going deeper

- [Finding and fixing mis-linked channels](finding-mislinked-channels.md):
  the read-only whole-fleet audit for shared EPG links.
- [Matching channels to EPG data](channel-to-epg-matching.md): the full
  bulk-match walkthrough, including confidence-score guidance.
- [Add and refresh EPG sources](epg-sources.md): reading a source's health
  status.
- [`docs/runbooks/`](https://github.com/MotWakorb/enhancedchannelmanager/tree/main/docs/runbooks): if an EPG problem has escalated into
  an on-call/incident situation rather than routine troubleshooting.
