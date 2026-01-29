# Enhanced Channel Manager (ECM) User Guide

A comprehensive guide to using Enhanced Channel Manager for IPTV channel management with Dispatcharr.

> **Note**: This guide includes placeholder image references. To add screenshots, capture the described screens and save them to a `docs/images/` folder with the specified filenames.

---

## Table of Contents

1. [Getting Started](#getting-started)
2. [M3U Manager](#m3u-manager)
3. [Channel Manager](#channel-manager)
4. [EPG Manager](#epg-manager)
5. [TV Guide](#tv-guide)
6. [Logo Manager](#logo-manager)
7. [Journal](#journal)
8. [Stats Dashboard](#stats-dashboard)
9. [Settings](#settings)
10. [Keyboard Shortcuts](#keyboard-shortcuts)
11. [Tips & Best Practices](#tips--best-practices)
12. [Screenshot Checklist](#screenshot-checklist)

---

## Getting Started

Enhanced Channel Manager (ECM) is a web-based interface for managing IPTV channels, EPG data, and stream configurations with Dispatcharr.

### First-Time Setup

![Application Overview](docs/images/01-app-overview.png)
<!-- Screenshot: Full application window showing the tab navigation bar at top with all tabs visible (M3U Manager, EPG Manager, Channel Manager, Guide, Logo Manager, Journal, Stats, Settings) -->

**Step 1: Open Settings**

![Settings Navigation](docs/images/02-settings-nav.png)
<!-- Screenshot: Click on the Settings tab in the navigation bar, showing the settings sidebar -->

1. Click **Settings** in the top navigation bar
2. The settings sidebar will appear on the left

**Step 2: Configure Dispatcharr Connection**

![Dispatcharr Connection Settings](docs/images/03-dispatcharr-settings.png)
<!-- Screenshot: The General Settings section showing Server URL, Username, Password fields, and Test Connection button -->

1. Enter your **Server URL** (e.g., `http://192.168.1.100:5000`)
2. Enter your **Username**
3. Enter your **Password**
4. Click **Test Connection**

**Step 3: Verify Connection**

![Connection Success](docs/images/04-connection-success.png)
<!-- Screenshot: Show the green checkmark or "Connection verified" indicator after successful test -->

- A green checkmark indicates successful connection
- If connection fails, verify your URL and credentials

**Step 4: Save Settings**

1. Click **Save** to store your configuration
2. You're now ready to add M3U accounts

---

## M3U Manager

The M3U Manager is where you add and configure your IPTV provider playlists.

### Overview

![M3U Manager Tab](docs/images/05-m3u-manager-overview.png)
<!-- Screenshot: M3U Manager tab showing 2-3 accounts in different states (Ready, Disabled, etc.) with the Add Account button visible -->

The M3U Manager displays:
- List of all configured M3U accounts
- Account status indicators
- Quick action buttons (refresh, manage groups, filters, etc.)

### Adding an M3U Account

**Step 1: Click Add Account**

![Add Account Button](docs/images/06-add-account-button.png)
<!-- Screenshot: Highlight the "Add Account" button in the M3U Manager toolbar -->

1. Click the **Add Account** button in the top toolbar

**Step 2: Choose Account Type**

![Account Type Selection](docs/images/07-account-type-modal.png)
<!-- Screenshot: The Add Account modal showing the three account type options: Standard M3U, XtreamCodes, HD Homerun -->

Choose from three account types:

| Type | Use Case |
|------|----------|
| **Standard M3U** | Direct URL to M3U playlist |
| **XtreamCodes (XC)** | XtreamCodes portal with login |
| **HD Homerun** | Local HD Homerun device |

**Step 3: Configure Standard M3U Account**

![Standard M3U Form](docs/images/08-standard-m3u-form.png)
<!-- Screenshot: The form fields for Standard M3U: Name, URL, Max Streams, Refresh Interval -->

1. Enter a **Name** for the account
2. Paste the **M3U URL** from your provider
3. Set **Max Streams** (concurrent connection limit)
4. Set **Refresh Interval** (how often to update, default 24 hours; set to 0 for manual refresh only)
5. Click **Save**

**Step 3 (Alternative): Configure XtreamCodes Account**

![XtreamCodes Form](docs/images/09-xtreamcodes-form.png)
<!-- Screenshot: The form fields for XtreamCodes: Name, Server URL, Username, Password, Max Streams -->

1. Enter a **Name** for the account
2. Enter the **Server URL** (base URL without /get.php)
3. Enter your **Username** and **Password**
4. Set **Max Streams**
5. Click **Save**

**Step 4: Account Refreshes Automatically**

![Account Refreshing](docs/images/10-account-refreshing.png)
<!-- Screenshot: Account row showing "Downloading" or "Processing" status with spinner -->

After saving, the account automatically refreshes to load your channels.

### Understanding Account Status

![Account Status Indicators](docs/images/11-account-statuses.png)
<!-- Screenshot: Multiple accounts showing different statuses: Ready (green), Error (red), Downloading, Disabled -->

| Status | Icon | Meaning |
|--------|------|---------|
| Ready | Green check | Account loaded successfully |
| Error | Red X | Connection or parsing failed |
| Downloading | Spinner | Fetching playlist data |
| Processing | Spinner | Parsing M3U content |
| Disabled | Gray | Account turned off |

### Managing Channel Groups

**Step 1: Open Manage Groups**

![Manage Groups Button](docs/images/12-manage-groups-button.png)
<!-- Screenshot: Highlight the "Manage Groups" button on an M3U account row -->

1. Click **Manage Groups** on the account you want to configure

**Step 2: Enable/Disable Groups**

![Manage Groups Modal](docs/images/13-manage-groups-modal.png)
<!-- Screenshot: The Manage Groups modal showing a list of groups with toggle switches, some enabled, some disabled -->

1. Toggle groups **on** to make them available in Channel Manager
2. Toggle groups **off** to hide them
3. Use **Hide Disabled** to show only enabled groups

**Step 3: Configure Auto-Sync (Optional)**

![Auto-Sync Settings](docs/images/14-auto-sync-settings.png)
<!-- Screenshot: Expanded auto-sync settings for a group showing EPG override, name regex, channel profile options -->

For automatic channel creation from a group:

1. Click the **gear icon** next to a group
2. Configure auto-sync options:
   - **EPG Source Override**: Force specific EPG source
   - **Channel Group Override**: Place channels in different group
   - **Name Regex Pattern**: Transform channel names
   - **Channel Profile**: Assign default profile
3. Click **Save**

### Refreshing M3U Data

![Refresh Button](docs/images/15-refresh-button.png)
<!-- Screenshot: Highlight the refresh icon button on an account row -->

- Click the **refresh icon** on any account to update its playlist
- Use **Refresh All** in the toolbar to refresh all accounts

### M3U Filters

![Filters Modal](docs/images/16-m3u-filters.png)
<!-- Screenshot: The Filters modal showing a list of filters with Type (Group/Name/URL), Action (Include/Exclude), and Pattern columns -->

Filters let you include or exclude streams:

1. Click **Manage Filters** on an account
2. Click **Add Filter**
3. Choose **Type**: Group, Name, or URL
4. Choose **Action**: Include or Exclude
5. Enter a **Regex Pattern**
6. Drag filters to reorder (executed top to bottom)

---

## Channel Manager

The Channel Manager is where you create and organize your channel lineup.

### Interface Overview

![Channel Manager Overview](docs/images/17-channel-manager-overview.png)
<!-- Screenshot: Full Channel Manager view showing left pane (channels by group) and right pane (streams), with the divider visible -->

The screen is split into two panes:
- **Left Pane**: Your channel lineup organized by groups
- **Right Pane**: Available streams from M3U providers
- **Divider**: Drag to resize panes

### Creating Channels - Method 1: Drag and Drop

**Step 1: Find a Stream**

![Stream List](docs/images/18-stream-list.png)
<!-- Screenshot: Right pane showing stream list with provider filter dropdown and search box -->

1. Use the **Provider** dropdown to filter by M3U account
2. Use **Search** to find specific streams

**Step 2: Drag to Create Channel**

![Drag to Create](docs/images/19-drag-to-create.png)
<!-- Screenshot: Show a stream being dragged from right pane toward a channel group header, with drop indicator visible -->

1. Click and drag a stream from the right pane
2. Drop it on a **group header** in the left pane
3. A new channel is created with the stream's name and logo

### Creating Channels - Method 2: Bulk Creation

**Step 1: Select Stream Groups**

![Select Stream Groups](docs/images/20-select-stream-groups.png)
<!-- Screenshot: Right pane showing multiple stream groups selected (highlighted) with Ctrl+Click -->

1. Click a stream group header to select it
2. **Ctrl+Click** to select additional groups

**Step 2: Drag to Left Pane**

![Bulk Create Drag](docs/images/21-bulk-create-drag.png)
<!-- Screenshot: Multiple selected groups being dragged to left pane, showing drop zone indicator -->

1. Drag the selected groups to the left pane
2. The bulk creation modal appears

**Step 3: Configure Bulk Creation**

![Bulk Create Modal](docs/images/22-bulk-create-modal.png)
<!-- Screenshot: Bulk creation modal showing: Starting Number field, Group Selection dropdown, Channel Profile dropdown, and preview list of channels to be created -->

1. Set **Starting Channel Number**
2. Choose **Group Selection**:
   - Same-named group (creates matching group)
   - Select existing group
   - Create new group
3. Select **Channel Profile** (optional)
4. Review the **preview** of channels to be created

**Step 4: Review and Create**

![Bulk Create Preview](docs/images/23-bulk-create-preview.png)
<!-- Screenshot: The preview section showing list of channels with their assigned streams and quality indicators -->

1. Review the channel list
2. Note the **stream count** per channel (merged duplicates)
3. Click **Create Channels**

### Smart Stream Merging

![Merged Streams](docs/images/24-merged-streams.png)
<!-- Screenshot: An expanded channel showing multiple streams from different providers, ordered by quality -->

ECM automatically:
- **Merges duplicates**: Same channel from different providers
- **Orders by quality**: UHD → 4K → FHD → 1080p → HD → 720p → SD
- **Interleaves providers**: For failover redundancy

### Using Edit Mode

**Step 1: Enter Edit Mode**

![Edit Mode Button](docs/images/25-edit-mode-button.png)
<!-- Screenshot: Highlight the "Edit Mode" toggle button in the Channel Manager toolbar -->

1. Click **Edit Mode** to enable staged editing
2. All changes are now local until you commit

**Step 2: Make Changes**

![Edit Mode Active](docs/images/26-edit-mode-active.png)
<!-- Screenshot: Channel Manager in edit mode showing: modified indicator on channels, undo/redo buttons visible, pending changes count -->

In edit mode you can:
- Edit channel numbers (click to edit)
- Edit channel names (click to edit)
- Add/remove/reorder streams
- Delete channels (recoverable with undo)
- Move channels between groups

**Step 3: Use Undo/Redo**

![Undo Redo](docs/images/27-undo-redo.png)
<!-- Screenshot: Close-up of undo/redo buttons with tooltip showing "Undo: Delete channel ESPN" -->

- Press **Ctrl+Z** to undo
- Press **Ctrl+Shift+Z** to redo

**Step 4: Exit and Commit**

![Exit Edit Mode Dialog](docs/images/28-exit-edit-dialog.png)
<!-- Screenshot: The exit edit mode dialog showing summary of all changes: X channels modified, Y streams added, Z channels deleted, etc. -->

1. Click **Exit Edit Mode**
2. Review the summary of all changes
3. Click **Commit** to save or **Discard** to cancel

### Multi-Select Operations

**Step 1: Select Multiple Channels**

![Multi Select](docs/images/29-multi-select.png)
<!-- Screenshot: Multiple channels selected (checkboxes checked, rows highlighted) -->

- **Ctrl+Click**: Toggle individual selection
- **Shift+Click**: Select range
- **Ctrl+A**: Select all visible

**Step 2: Right-Click for Options**

![Context Menu](docs/images/30-context-menu.png)
<!-- Screenshot: Right-click context menu showing: Move to Group (with submenu of groups), Create New Group & Move -->

1. Right-click on selected channels
2. Choose **Move to Group** and select destination
3. Or choose **Create New Group & Move**

### Managing Streams Within Channels

![Channel Streams](docs/images/31-channel-streams.png)
<!-- Screenshot: Expanded channel showing its streams with: drag handles for reordering, X buttons for removal, quality badges -->

- **Add**: Drag streams from right pane onto channel
- **Remove**: Click the **X** on a stream
- **Reorder**: Drag streams up/down (higher = higher priority)

### Filtering Channels and Streams

![Filter Options](docs/images/32-filter-options.png)
<!-- Screenshot: Filter panel expanded showing: Group checkboxes, "Hide Empty Groups", "Hide Provider Groups", Search box -->

**Channel Filters (Left Pane)**:
- Search by name
- Show/hide specific groups
- Show/hide empty groups
- Show/hide provider groups

**Stream Filters (Right Pane)**:
- Provider dropdown
- Group dropdown
- Search by name
- Hide already-mapped streams

---

## EPG Manager

The EPG Manager configures your Electronic Program Guide data sources.

### Overview

![EPG Manager Overview](docs/images/33-epg-manager-overview.png)
<!-- Screenshot: EPG Manager tab showing list of EPG sources with status, channel count, last updated, and action buttons -->

### Adding an EPG Source

**Step 1: Click Add Source**

![Add EPG Source](docs/images/34-add-epg-source.png)
<!-- Screenshot: Add Source button highlighted -->

1. Click **Add Source** in the toolbar

**Step 2: Configure XMLTV Source**

![XMLTV Form](docs/images/35-xmltv-form.png)
<!-- Screenshot: Add EPG Source modal with Name field and URL field for XMLTV -->

1. Enter a **Name** for the source
2. Paste the **XMLTV URL**
3. Set **Refresh Interval** (hours between updates; set to 0 for manual refresh only)
4. Click **Save**

**Step 3: Source Refreshes**

![EPG Refreshing](docs/images/36-epg-refreshing.png)
<!-- Screenshot: EPG source showing "Fetching" or "Parsing" status with progress -->

The source automatically fetches and parses EPG data.

### Setting Source Priority

![EPG Priority](docs/images/37-epg-priority.png)
<!-- Screenshot: EPG sources with drag handles, showing one being dragged to reorder -->

1. Drag sources up/down to change priority
2. Higher sources take precedence for channel matching

### Creating Dummy EPG

![Dummy EPG Form](docs/images/38-dummy-epg-form.png)
<!-- Screenshot: Dummy EPG configuration form showing: Name source, Title template, Description template, Duration, Timezone fields -->

For channels without guide data:

1. Add a **Dummy EPG** source
2. Configure:
   - **Name Source**: Channel or stream name
   - **Title Template**: Use `{name}` placeholder
   - **Duration**: Program length in minutes
   - **Timezone**: Event timezone
3. Click **Save**

### Bulk EPG Assignment

**Step 1: Select Channels**

![Select for EPG](docs/images/39-select-for-epg.png)
<!-- Screenshot: Channel Manager with multiple channels selected -->

1. In Channel Manager, select channels needing EPG
2. Click **Assign EPG** in the toolbar

**Step 2: Review Matches**

![EPG Matching](docs/images/40-epg-matching.png)
<!-- Screenshot: Bulk EPG assignment modal showing: auto-matched channels (green), conflicts requiring review, unmatched channels -->

1. Auto-matched channels show in green
2. Conflicts show multiple options to choose from
3. Unmatched channels need manual assignment

**Step 3: Resolve Conflicts**

![EPG Conflict](docs/images/41-epg-conflict.png)
<!-- Screenshot: Conflict resolution card showing multiple EPG options for a channel, with radio buttons to select -->

1. For each conflict, select the correct EPG entry
2. Click **Apply** when done

---

## TV Guide

The Guide tab displays your EPG data in a grid format.

### Guide Overview

![Guide Overview](docs/images/42-guide-overview.png)
<!-- Screenshot: Full TV Guide showing: time header, channel list on left, program grid, red "now" line, currently playing highlighted -->

Features:
- Time header with current time indicator (red line)
- Channel list on left
- Program grid with 6-hour window
- Currently airing programs highlighted

### Navigation

![Guide Navigation](docs/images/43-guide-navigation.png)
<!-- Screenshot: Guide header showing: date picker, hour selector buttons, profile filter dropdown -->

- **Date Picker**: Browse different days
- **Hour Buttons**: Jump to specific times
- **Profile Filter**: Show specific channel profiles

### Viewing Program Details

![Program Hover](docs/images/44-program-hover.png)
<!-- Screenshot: Hovering over a program showing tooltip with: full title, subtitle, time range, description -->

- Hover over any program to see full details
- Click a channel to edit its settings

### Print Guide

![Print Guide](docs/images/45-print-guide.png)
<!-- Screenshot: Print Guide modal showing group selection checkboxes and display mode options -->

1. Click **Print Guide**
2. Select groups to include
3. Choose display mode
4. Use browser print function

---

## Logo Manager

### Logo Library

![Logo Manager](docs/images/46-logo-manager.png)
<!-- Screenshot: Logo Manager showing grid of logos with search box, view mode toggle, and usage counts -->

- Browse all logos with previews
- Toggle between list and grid view
- Search by name
- See usage count per logo

### Adding Logos

![Add Logo](docs/images/47-add-logo.png)
<!-- Screenshot: Add Logo modal with URL input field or file upload option -->

- **From URL**: Enter image URL
- **Upload**: Upload image file directly

---

## Journal

### Activity Log

![Journal Overview](docs/images/48-journal-overview.png)
<!-- Screenshot: Journal tab showing activity list with: color-coded entries, category icons, timestamps, expand buttons -->

The Journal tracks all changes:
- Channel operations (create, update, delete)
- EPG changes
- M3U operations
- Watch events

### Filtering

![Journal Filters](docs/images/49-journal-filters.png)
<!-- Screenshot: Journal filter bar showing: Category dropdown, Action Type dropdown, Time Range dropdown, Search box -->

- **Category**: Channel, EPG, M3U, Watch
- **Action Type**: Create, Update, Delete, etc.
- **Time Range**: Last hour to all time
- **Search**: Full-text search

### Entry Details

![Journal Entry](docs/images/50-journal-entry.png)
<!-- Screenshot: Expanded journal entry showing before/after values in JSON format -->

Click any entry to see full details including before/after values.

---

## Stats Dashboard

### Live Statistics

![Stats Overview](docs/images/51-stats-overview.png)
<!-- Screenshot: Stats tab showing: active channels list with metrics, connection counts, bandwidth chart -->

Monitor in real-time:
- Active streaming channels
- FFmpeg speed (color-coded)
- Bitrate and FPS
- Connection counts per M3U account

### Channel Metrics

![Channel Metrics](docs/images/52-channel-metrics.png)
<!-- Screenshot: Close-up of channel stats showing: speed indicator (green/yellow/red), FPS, bitrate, duration -->

| Metric | Meaning |
|--------|---------|
| Speed (green) | ≥0.98x - Excellent |
| Speed (yellow) | ≥0.90x - Acceptable |
| Speed (red) | <0.90x - Buffering likely |

### Historical Charts

![Stats Charts](docs/images/53-stats-charts.png)
<!-- Screenshot: Expanded channel showing speed and bandwidth charts over time -->

Click any channel to expand and see:
- Speed over time
- Bandwidth usage trends

### Auto-Refresh

![Refresh Settings](docs/images/54-refresh-settings.png)
<!-- Screenshot: Auto-refresh dropdown showing options: Manual, 10s, 30s, 1m, 5m -->

Set refresh interval:
- Manual
- 10 seconds
- 30 seconds
- 1 minute
- 5 minutes

---

## Settings

### Settings Navigation

![Settings Sidebar](docs/images/55-settings-sidebar.png)
<!-- Screenshot: Settings page showing sidebar navigation with all sections: General, Normalization, Probing, Sort Priority, Defaults, Appearance, Tasks, Alerts -->

Access different settings sections from the sidebar.

### Stream Probing

![Probe Settings](docs/images/56-probe-settings.png)
<!-- Screenshot: Stream Probing settings showing: Enable toggle, Schedule (start time, interval), Performance limits, Channel group filter -->

Configure automated stream health checking:

1. **Enable** stream probing
2. Set **Start Time** (e.g., 03:00 for off-peak)
3. Set **Interval** (hours between probes)
4. Configure **Batch Size** and **Timeout**
5. Select **Channel Groups** to probe

### Stream Sort Priority

![Sort Priority](docs/images/57-sort-priority.png)
<!-- Screenshot: Sort Priority settings showing draggable criteria: Resolution, Bitrate, Framerate with toggle switches -->

1. Drag criteria to set priority order
2. Toggle individual criteria on/off
3. Enable "Deprioritize Failed Streams"

### Normalization Engine

![Normalization Engine](docs/images/60-normalization-engine.png)
<!-- Screenshot: Normalization Engine settings showing rule list with conditions, actions, and test panel -->

Create custom rules to automatically transform stream names:

**Step 1: Create a Rule**

1. Click **Add Rule** to create a new normalization rule
2. Enter a descriptive **Rule Name**
3. Configure the **Condition** (when the rule applies)
4. Configure the **Action** (what transformation to apply)

**Step 2: Configure Conditions**

Available condition types:
- **Contains** - Matches if name contains the text
- **Starts With** - Matches if name starts with the text
- **Ends With** - Matches if name ends with the text
- **Equals** - Matches if name exactly equals the text
- **Regex** - Matches using a regular expression pattern

**Step 3: Configure Actions**

Available action types:
- **Remove Prefix** - Remove text from the beginning
- **Remove Suffix** - Remove text from the end
- **Replace** - Replace matched text with new text
- **Regex Replace** - Replace using regex pattern and replacement
- **Set Value** - Set the entire name to a specific value

**Step 4: Use Compound Conditions**

Build complex logic by combining conditions:
- **AND** - All conditions must match
- **OR** - Any condition can match
- **NOT** - Inverts the condition result

**Step 5: Test Your Rules**

1. Enter sample stream names in the **Test Panel**
2. See real-time preview of how names will be transformed
3. Adjust rules as needed before saving

**Step 6: Enable Auto-Normalization**

Toggle **Normalize on Channel Create** to automatically apply rules when creating new channels.

### Scheduled Tasks

![Scheduled Tasks](docs/images/58-scheduled-tasks.png)
<!-- Screenshot: Scheduled Tasks section showing task list with schedules, enable toggles, and Run Now buttons -->

Configure automated tasks:

1. Click **Add Schedule** on a task
2. Choose schedule type (Interval, Daily, Weekly, etc.)
3. Set the timing
4. Enable/disable as needed

### Alert Methods

![Alert Methods](docs/images/59-alert-methods.png)
<!-- Screenshot: Alert Methods section showing Discord, Telegram, Email options with configuration fields -->

1. Click **Add Method**
2. Choose type (Discord, Telegram, Email)
3. Enter configuration (webhook URL, bot token, SMTP settings)
4. Select notification types to receive
5. **Test** the configuration

---

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+Z` / `Cmd+Z` | Undo |
| `Ctrl+Shift+Z` / `Cmd+Shift+Z` | Redo |
| `Ctrl+A` / `Cmd+A` | Select all visible items |
| `Shift+Click` | Select range |
| `Ctrl+Click` / `Cmd+Click` | Toggle selection |
| `Escape` | Clear selection / Close menu |
| `Enter` | Save inline edit |

---

## Tips & Best Practices

### Organizing Channels
- Use descriptive group names ("Sports", "News", "Movies")
- Set logical channel number ranges (100-199 Sports, 200-299 News)
- Keep related channels together

### Managing Providers
- Link accounts from the same provider
- Use smart merging for duplicate channels
- Set stream priorities (best quality first)

### Maintaining EPG
- Prioritize most reliable EPG sources
- Use dummy EPG for channels without guide data
- Schedule EPG refreshes during off-peak hours

### Monitoring Health
- Enable stream probing
- Configure alerts for failures
- Check Stats dashboard regularly

---

## Screenshot Checklist

Use this checklist to capture all screenshots for the guide:

### Getting Started (4 screenshots)
- [ ] `01-app-overview.png` - Full app with all tabs visible
- [ ] `02-settings-nav.png` - Settings tab in navigation
- [ ] `03-dispatcharr-settings.png` - Connection settings form
- [ ] `04-connection-success.png` - Successful connection indicator

### M3U Manager (12 screenshots)
- [ ] `05-m3u-manager-overview.png` - M3U tab with multiple accounts
- [ ] `06-add-account-button.png` - Add Account button
- [ ] `07-account-type-modal.png` - Account type selection
- [ ] `08-standard-m3u-form.png` - Standard M3U form fields
- [ ] `09-xtreamcodes-form.png` - XtreamCodes form fields
- [ ] `10-account-refreshing.png` - Account in refreshing state
- [ ] `11-account-statuses.png` - Various account statuses
- [ ] `12-manage-groups-button.png` - Manage Groups button
- [ ] `13-manage-groups-modal.png` - Groups list with toggles
- [ ] `14-auto-sync-settings.png` - Auto-sync configuration
- [ ] `15-refresh-button.png` - Refresh button on account
- [ ] `16-m3u-filters.png` - Filters modal

### Channel Manager (16 screenshots)
- [ ] `17-channel-manager-overview.png` - Full split-pane view
- [ ] `18-stream-list.png` - Right pane stream list
- [ ] `19-drag-to-create.png` - Dragging stream to create channel
- [ ] `20-select-stream-groups.png` - Multiple groups selected
- [ ] `21-bulk-create-drag.png` - Dragging groups to left pane
- [ ] `22-bulk-create-modal.png` - Bulk creation options
- [ ] `23-bulk-create-preview.png` - Preview of channels to create
- [ ] `24-merged-streams.png` - Channel with merged streams
- [ ] `25-edit-mode-button.png` - Edit Mode toggle
- [ ] `26-edit-mode-active.png` - Active edit mode indicators
- [ ] `27-undo-redo.png` - Undo/redo buttons
- [ ] `28-exit-edit-dialog.png` - Exit edit mode summary
- [ ] `29-multi-select.png` - Multiple channels selected
- [ ] `30-context-menu.png` - Right-click menu
- [ ] `31-channel-streams.png` - Channel's stream list
- [ ] `32-filter-options.png` - Filter panel

### EPG Manager (9 screenshots)
- [ ] `33-epg-manager-overview.png` - EPG sources list
- [ ] `34-add-epg-source.png` - Add Source button
- [ ] `35-xmltv-form.png` - XMLTV source form
- [ ] `36-epg-refreshing.png` - Source refreshing
- [ ] `37-epg-priority.png` - Dragging to reorder sources
- [ ] `38-dummy-epg-form.png` - Dummy EPG configuration
- [ ] `39-select-for-epg.png` - Selecting channels for EPG
- [ ] `40-epg-matching.png` - Bulk EPG matching results
- [ ] `41-epg-conflict.png` - Conflict resolution

### TV Guide (4 screenshots)
- [ ] `42-guide-overview.png` - Full guide grid
- [ ] `43-guide-navigation.png` - Date/time navigation
- [ ] `44-program-hover.png` - Program details tooltip
- [ ] `45-print-guide.png` - Print guide modal

### Logo Manager (2 screenshots)
- [ ] `46-logo-manager.png` - Logo grid view
- [ ] `47-add-logo.png` - Add logo modal

### Journal (3 screenshots)
- [ ] `48-journal-overview.png` - Journal activity list
- [ ] `49-journal-filters.png` - Filter bar
- [ ] `50-journal-entry.png` - Expanded entry details

### Stats (4 screenshots)
- [ ] `51-stats-overview.png` - Stats dashboard
- [ ] `52-channel-metrics.png` - Channel metric details
- [ ] `53-stats-charts.png` - Historical charts
- [ ] `54-refresh-settings.png` - Auto-refresh dropdown

### Settings (6 screenshots)
- [ ] `55-settings-sidebar.png` - Settings navigation
- [ ] `56-probe-settings.png` - Stream probing config
- [ ] `57-sort-priority.png` - Sort priority settings
- [ ] `58-scheduled-tasks.png` - Task scheduler
- [ ] `59-alert-methods.png` - Alert configuration
- [ ] `60-normalization-engine.png` - Normalization rules and test panel

**Total: 60 screenshots**

---

*Enhanced Channel Manager - Professional IPTV Channel Management*
