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
