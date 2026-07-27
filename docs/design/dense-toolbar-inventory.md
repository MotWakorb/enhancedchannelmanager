# Dense toolbar inventory

Bead `enhancedchannelmanager-2896r.6` standardizes dense-route controls in this
DOM and keyboard order:

1. Search
2. Filters
3. Sort or view
4. Selection count
5. Bulk actions
6. Secondary/lifecycle actions

`DenseToolbar` renders only supported groups; it does not invent controls or
move route-primary creation actions out of the page header.

| Route | Supported groups | Query and pagination contract |
| --- | --- | --- |
| M3U Manager | server-group filter; priority control; refresh/setup secondary actions | Client-side account filtering; no paginated endpoint or route-query schema. |
| EPG Manager | reorder view control; refresh/migration secondary actions | Existing complete source collection; no route search/filter/page API. |
| Logo Manager | search; unused filter; list/grid view | Existing `getLogos` search/sort/filter/page request is unchanged; search/filter/sort/page-size changes reset page to 1. |
| M3U Changes | time/account/type/status filters; sortable columns | Existing request parameters and page reset remain unchanged. `hours` remains shareable in `#m3u-changes?hours=` because it is the only supported route query. |
| Journal | debounced search; category/action/source filters | Existing `getJournalEntries` parameters and page reset remain unchanged. The hash router has no Journal query schema, so these values remain session-local. |

## Route-state contract

| State | M3U / EPG / Logo | M3U Changes / Journal |
| --- | --- | --- |
| Initial loading | Keep a labelled toolbar region with a disabled loading action so the page does not reflow when controls arrive. | Keep the filter toolbar visible and show an inline loading status. |
| True empty | Show the route's setup-oriented empty state. | Show the route's no-history empty state only after a successful request. |
| Filtered zero | Explain that filters produced no matches and provide a clear-filter action where filters are active. | Preserve filters and show the existing filtered-empty result. |
| Recoverable error | Show the source error and a retry action; protected rows and counts are cleared. | Show an inline error and Retry; protected rows, summaries, and counts are cleared. |
| HTTP 403 | Suppress unsafe controls and show the permission state. | Suppress Retry and destructive actions, clear protected data, and show the permission state. |

## Deliberate deviations

- Primary create actions remain in the route header to preserve the approved
  page hierarchy.
- Refresh, migration, purge, and setup commands are secondary lifecycle
  actions, not bulk actions. Existing `OverflowMenu` use remains the recovery
  mechanism at constrained widths.
- None of these routes currently exposes row selection, so selection count and
  bulk-action groups are omitted rather than shown disabled. Disabled selection
  remains represented by the shared component contract and by existing
  route-specific disabled actions.
- Channel Manager is excluded. Its Edit Mode bottom selection bar, upward More
  menu, `2+` Merge rule, and Clear action are the approved dense-workspace
  exception tracked by `enhancedchannelmanager-2896r.13`.
