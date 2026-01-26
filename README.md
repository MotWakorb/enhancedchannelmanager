# Enhanced Channel Manager

A professional-grade web interface for managing IPTV configurations with Dispatcharr. Features a tab-based interface for channel management, EPG data, logos, and more. Built with React + TypeScript frontend and Python FastAPI backend.

## Application Tabs

- **M3U Manager** - Full M3U account management with linked accounts and group synchronization
- **EPG Manager** - Manage EPG sources with drag-and-drop priority ordering
- **Channel Manager** - Full-featured channel and stream management with split-pane layout
- **Guide** - TV Guide with EPG grid view showing program schedules
- **Logo Manager** - Logo management with search, upload, and URL import
- **Journal** - Activity log tracking all changes to channels, EPG, and M3U accounts
- **Stats** - Live streaming statistics, M3U connection counts, bandwidth tracking, and charts
- **Settings** - Configure Dispatcharr connection, channel defaults, stream probing, scheduled tasks, alert methods, and appearance

## Features

### M3U Manager

- **Full CRUD Operations** - Create, edit, and delete M3U accounts
- **Account Types** - Support for Standard M3U, XtreamCodes, and HD Homerun
- **HD Homerun Simplified Setup** - Just enter the IP address; the lineup URL is auto-constructed
- **Linked M3U Accounts** - Link multiple accounts (e.g., same provider with different regions) so group enable/disable changes cascade automatically
- **Sync Groups** - One-click sync of enabled groups across all linked accounts using Union (OR) logic
- **Manage Groups Modal** - Enable/disable groups per account, configure auto-sync settings
- **M3U Priority** - Set priority order for each M3U account (used by smart sort to order streams)
- **Natural Sorting** - Accounts sorted with Standard M3U first, XtreamCodes second, then natural sort by name
- **Hide Disabled Filter** - Optionally hide disabled groups in the manage groups modal
- **Auto-Refresh** - New accounts automatically refresh after creation (no "Pending Setup" state)
- **Server Group Filtering** - Filter M3U accounts by server group

### Channel Profiles

- **Profile Management** - View and manage stream transcoding profiles
- **Default Profile** - Set a default channel profile in Settings
- **Profile Selection** - Choose profiles when creating channels (single or bulk)
- **Profile Assignment** - Assign profiles to existing channels

### Channel Management

- **Create, Edit, Delete Channels** - Full CRUD operations for channel management
- **Channel Numbering** - Assign channel numbers (including decimal numbers like 4.1)
- **Auto-Rename** - Optionally update channel names when numbers change (e.g., "101 | Sports Channel" becomes "102 | Sports Channel")
- **Channel Groups** - Organize channels into groups for better organization
- **Group Channel Range** - Each group header displays the channel number range (e.g., #100–150)
- **Delete Groups** - Delete channel groups with option to also delete contained channels
- **Delete Orphaned Groups** - Automatically detect and remove channel groups that have no streams, no channels, and no M3U association (groups left behind after M3U account deletions)
- **Bulk Delete** - Select multiple channels and delete them at once
- **Search & Filter** - Search channels by name, filter by one or more groups
- **Inline Stream Display** - View assigned streams directly in the channel list
- **Copy Channel URL** - Click copy button on any channel to copy its Dispatcharr proxy stream URL

### Stream Management

- **View Available Streams** - Browse all streams from your M3U providers
- **Assign Streams to Channels** - Drag-and-drop or click to add streams
- **Stream Priority** - Reorder streams within a channel (order determines playback priority)
- **Multi-Select** - Select multiple streams and bulk-add to channels
- **Filter by Provider** - Filter streams by M3U account
- **Filter by Group** - Filter streams by their source group
- **Copy Stream URL** - Click copy button on any stream to copy its direct URL

### EPG Management

- **Add EPG Sources** - Configure XMLTV URLs or Schedules Direct accounts
- **Dummy EPG Sources** - Create custom EPG entries for channels without guide data
  - Configure title patterns, descriptions, and colors
  - Set custom time slots and durations
  - Preview how dummy EPG will appear before saving
- **Drag-and-Drop Priority** - Reorder EPG sources to set matching priority (higher priority sources take precedence)
- **Source Status** - View status of each source (success, error, fetching, parsing)
- **Refresh Sources** - Manually refresh individual sources or all at once
- **Enable/Disable** - Toggle sources active/inactive without deleting
- **Channel Count** - See how many EPG channels each source provides
- **Last Updated** - Track when each source was last refreshed

### TV Guide

- **EPG Grid View** - Full program guide showing all channels with their schedules
- **Now-Playing Highlight** - Currently airing programs are highlighted with accent color
- **Current Time Indicator** - Red vertical line shows the current time position
- **Channel Profile Filter** - Filter the guide by channel profile to see only specific channels
- **Date Navigation** - Browse programs from past days through upcoming week
- **Time Navigation** - Jump to any hour of the day
- **Click-to-Edit** - Click any channel in the guide to edit its metadata (logo, EPG, name, etc.)
- **Auto-Scroll** - Guide automatically centers on current time when viewing today
- **Program Details** - Hover over programs to see full title, subtitle, and time range

### Bulk Channel Creation

- **Channel Profile Selection** - Choose which stream profile to apply to all created channels
- **Create from Stream Group** - Create multiple channels from an entire M3U stream group at once
- **Multi-Group Creation** - Select and drag multiple stream groups at once to create channels from all of them
  - **Separate Groups Mode** - Create a separate channel group for each stream group with independent naming and numbering
  - **Combined Mode** - Merge all streams from selected groups into a single channel group
  - **Per-Group Settings** - Customize channel group name and starting channel number for each group
  - **Group Preview** - See channel previews organized by group with calculated channel numbers
  - **Drag Multiple Groups** - Select multiple groups in the streams pane and drag them together onto the channels pane
- **Auto-Assign Streams** - Each created channel automatically gets its source stream assigned
- **Auto-Assign Logos** - Channels inherit logos from their source streams automatically
- **Merge Duplicate Names** - Streams with identical names from different M3U providers are merged into a single channel with all streams assigned (provides multi-provider redundancy)
- **Quality Variant Normalization** - Streams with quality suffixes (FHD, UHD, HD, SD, 4K, 1080P, etc.) are automatically merged into one channel
- **Smart Stream Ordering** - Streams are automatically ordered by quality (UHD/4K → FHD/1080p → HD/720p → SD) and interleaved by provider for failover redundancy (e.g., Provider1-FHD, Provider2-FHD, Provider1-HD, Provider2-HD)
- **Tag-Based Normalization** - Integrated Quick Tag Manager lets you customize which tags to strip:
  - Toggle built-in tags across 5 groups (Country, League, Network, Quality, Timezone)
  - Add custom tags with prefix/suffix/both mode on the fly
  - Settings default to your configured preferences, adjustable per-operation
- **Network Prefix Stripping** - Strip network prefixes (e.g., "CHAMP | Queens Park Rangers" → "Queens Park Rangers") to merge streams from different networks
- **Network Suffix Stripping** - Strip network suffixes (e.g., "ESPN (ENGLISH)" → "ESPN", "HBO BACKUP" → "HBO") to clean up channel names
- **East/West Timezone Preference** - When streams have regional variants (e.g., "Movies Channel" and "Movies Channel West"), choose to create East feeds only, West feeds only, or keep both as separate channels
- **Country Prefix Options** - Choose to remove country prefixes (e.g., "US: Sports Channel" → "Sports Channel") or keep them with normalized formatting (e.g., "US: Sports Channel" → "US | Sports Channel") with configurable separator
- **Channel Number Prefix** - Option to prepend channel numbers to names with configurable separator (-, :, or |), e.g., "100 | Sports Channel"
- **Prefix Order Choice** - When both country and channel number are enabled, choose the order: "Number first" (100 | US | Sports Channel) or "Country first" (US | 100 | Sports Channel)
- **Custom Starting Number** - Choose the starting channel number for sequential assignment
- **Flexible Group Options** - Create in same-named group, select existing group, or create new group
- **Staged Group Creation** - New channel groups are staged with channels and only created on commit (prevents orphaned groups)
- **Preview Before Creating** - See a preview of channels that will be created with merged stream counts
- **Edit Mode Integration** - Requires edit mode, button visible on stream group headers on hover

### Edit Mode (Staged Editing)

A unique workflow that lets you stage changes locally before committing to the server:

- **Enter Edit Mode** - Start a new editing session
- **Stage Changes** - All edits are queued locally (including deletes)
- **Preview Changes** - See pending operations count
- **Local Undo/Redo** - Undo/redo within your edit session (Ctrl+Z / Ctrl+Shift+Z)
- **Undoable Deletes** - Channel and group deletions can be undone before committing
- **Bulk Delete with Groups** - When deleting all channels in a group, option to delete the empty group too
- **Batch Operations** - Group related changes together
- **Commit or Discard** - Apply all changes at once or discard everything
- **Exit Dialog** - Review a detailed summary of all changes before committing (channel number changes, name changes, streams added/removed, new channels, deleted channels, new groups, deleted groups)
- **Modified Indicators** - Visual highlighting of channels with pending changes

### Drag and Drop

- **Add Streams** - Drag streams onto channels to add them
- **Bulk Add** - Drag multiple selected streams at once
- **Create from Stream** - Drag a stream onto a group header (not a channel) to create a new channel with the stream name pre-populated
- **Reorder Channels** - Drag channels to reorder within a group (edit mode)
- **Move Between Groups** - Drag channels to different groups with confirmation modal
- **Ungroup Channels** - Drag channels to the "Uncategorized" group at the top to remove them from their group
- **Reorder Streams** - Drag to reorder streams within a channel
- **Visual Feedback** - Drop indicators and highlighting

### Context Menu (Right-Click)

- **Move channels to...** - Right-click selected channels to show a submenu of groups to move them to
- **Create new group and move** - Right-click selected channels to create a new group and move them there
- Works in edit mode when one or more channels are selected

### Multi-Select Channels

- **Ctrl/Cmd + Click** - Toggle individual channel selection
- **Shift + Click** - Select range of channels
- **Visual Indicators** - Selection checkboxes and highlighting
- **Bulk Operations** - Move or modify multiple channels at once
- **Normalize Names** - Standardize selected channel names with title case and league prefix formatting (e.g., "NFL ARIZONA CARDINALS" → "NFL: Arizona Cardinals")

### Sort & Renumber

- **Alphabetical Sorting** - Sort channels within a group A-Z
- **Sequential Renumbering** - Assign sequential numbers starting from any value
- **Smart Name Sorting** - Option to ignore channel numbers in names when sorting (e.g., "101 | Sports Channel" sorts as "Sports Channel")
- **Preview** - See the result before applying
- **Batch Undo** - Entire sort/renumber operation undoes as one action

### Logo Management

- **View Logos** - Browse available logos with previews
- **Assign Logos** - Assign logos to channels
- **Search Logos** - Search by name
- **Add from URL** - Create logos from external URLs
- **Upload Files** - Upload logo files directly
- **Usage Tracking** - See how many channels use each logo

### EPG Integration

- **Multiple EPG Sources** - Support for multiple EPG data sources
- **Search EPG Data** - Search for programs by name
- **Assign EPG** - Map EPG data to channels
- **Bulk EPG Assignment** - Assign EPG to multiple selected channels at once with intelligent matching:
  - **Country-Aware Matching** - Detects country from stream names (e.g., "US: Sports Channel") and matches to appropriate EPG entries (e.g., `SportsChannel.us` not `SportsChannel.mx`)
  - **Smart Call Sign Matching** - Prefers EPG entries where the call sign matches the channel name (e.g., `NOSEY` over `VIZNOSE` for "Nosey" channel)
  - **Call Sign Scoring** - Rates call sign matches: exact match > starts with channel name > common prefix > no match
  - **Balanced HD Preference** - Prefers HD EPG entries but not when a non-HD variant has a significantly better call sign match
  - **Regional Variant Handling** - Intelligent matching for regional variants including East, West, Pacific, Central, Mountain, and compound variants like WestCoastFeed
  - **Special Character Support** - Properly handles channels with special characters like "E!" or "MGM+"
  - **Auto-Match** - Channels with a single matching EPG entry are automatically assigned
  - **Conflict Resolution** - When multiple EPG entries match, review and select the correct one with card-based navigation
  - **Search Filter** - Filter EPG options within conflict resolution
  - **Unmatched List** - See which channels couldn't be matched for manual assignment later
  - **Batch Undo** - All assignments from a bulk operation undo as a single action
- **TVG ID Support** - Set TVG IDs for Kodi/XMLTV compatibility
- **TVC Guide Station ID** - Support for TVC guide integration

### Stream Profiles

- **View Profiles** - See available stream transcoding profiles
- **Assign to Channels** - Select which profile each channel uses
- **Profile Metadata** - View profile name, command, and parameters

### Stats (Live Dashboard)

Real-time monitoring of your streaming infrastructure:

- **Active Channels** - See all currently streaming channels with live data
- **M3U Connection Counts** - Monitor connections per M3U account (current/max)
- **Connected Clients** - Track total connected client count
- **Per-Channel Metrics** - FFmpeg speed, FPS, bitrate, duration, and total bytes
- **Speed Indicators** - Color-coded speed display (green ≥0.98x, yellow ≥0.90x, red <0.90x)
- **Historical Charts** - Expandable per-channel charts showing speed and bandwidth over time
- **Bandwidth Summary** - Daily bandwidth tracking with 7-day history chart
- **Top Watched Channels** - Leaderboard of most-watched channels by watch time or view count
- **System Events** - Live event feed (channel starts/stops, client connections)
- **Auto-Refresh** - Configurable refresh interval (10s, 30s, 1m, 5m) or manual refresh
- **Visibility-Aware** - Polling pauses when tab is hidden to save resources

### Journal (Activity Log)

Track all system activity with filtering and search:

- **Activity Categories** - Filter by Channel, EPG, M3U, or Watch events
- **Action Types** - Create, Update, Delete, Refresh, Start, Stop actions
- **Time Range Filter** - Filter entries by time period (1h, 6h, 24h, 7d, 30d, all)
- **Search** - Full-text search across all journal entries
- **Entry Details** - View detailed metadata for each entry
- **Statistics** - Summary counts by category and action type
- **Pagination** - Efficient loading of large activity logs
- **Color-Coded Actions** - Visual distinction between creates, updates, and deletes

### Global History (Undo/Redo)

Separate from edit mode, provides session-wide history:

- **Undo/Redo** - Revert or reapply changes (Ctrl+Z / Ctrl+Shift+Z)
- **Save Points** - Create named checkpoints to revert to later
- **Change Tracking** - See descriptions of recent changes

### Settings

The Settings tab features sidebar navigation with multiple sections:

#### General Settings
- **Dispatcharr Connection** - Configure server URL, username, and password
- **Test Connection** - Verify connectivity before saving

#### Stream Name Normalization (Tag-Based)
Powerful tag-based system for cleaning and normalizing stream names during bulk channel creation:

- **5 Built-in Tag Groups** - Country (US, UK, CA, etc.), League (NFL, NBA, NHL, etc.), Network (CHAMP, PPV, etc.), Quality (FHD, UHD, HD, SD, etc.), Timezone (EST, PST, EAST, WEST, etc.)
- **Toggle Individual Tags** - Enable or disable specific built-in tags within each group
- **Custom Tags** - Add your own tags with mode selection:
  - **Prefix only** - Strip when tag appears at start of name
  - **Suffix only** - Strip when tag appears at end of name
  - **Any position** - Strip tag wherever it appears
- **Expandable Groups** - Click to expand each tag group and see/manage individual tags
- **Summary Stats** - See counts of active, disabled, and custom tags at a glance
- **Reset to Defaults** - One-click restore of default tag configuration
- **Quick Tag Manager** - Compact tag editor also available in bulk create modal for per-operation customization

#### Stream Probing
Automated stream health checking:

- **Enable/Disable** - Toggle automatic stream probing
- **Schedule Time** - Set the daily probe start time (e.g., 03:00 for off-peak)
- **Interval** - Hours between probe cycles (default 24h)
- **Batch Size** - Number of concurrent probes (default 10)
- **Timeout** - Seconds to wait for each stream response (default 30s)
- **Channel Group Filter** - Select which channel groups to include in probing
- **Manual Probe** - Trigger an immediate probe of all streams
- **Probe Progress** - Real-time progress bar with success/failed/skipped counts
- **Probe History** - View past probe results with timestamps and statistics
- **Auto-Reorder After Probe** - Automatically reorder streams by quality and status after scheduled probes complete
- **Parallel Probing** - Streams from different M3U accounts probe concurrently
- **Max Concurrent Probes** - Configure simultaneous probe limit (1-16) with guidance based on provider limits
- **Rate Limit Detection** - Automatic backoff when providers return 429 errors, with UI notification
- **M3U Connection Awareness** - Respects M3U max connection limits during probing
- **Persistent History** - Probe results saved to `/config/probe_history.json` and persist across container restarts
- **Failed Stream Indicators** - Visual error icons on channels and groups that contain failed/timeout streams

#### Stream Sort Priority
Configure how streams are automatically sorted within channels:

- **Drag-and-Drop Reordering** - Arrange sort criteria in priority order
- **Enable/Disable Criteria** - Toggle individual sort criteria on/off
- **Available Criteria**:
  - Resolution - Supports any resolution (2160p → 1440p → 1080p → 720p → 576p → 540p → 480p → etc.)
  - Bitrate (higher bitrate first)
  - Framerate (higher FPS first)
  - M3U Priority - Sort by M3U account priority (configurable per-account in M3U Manager)
  - Audio Channels - Sort by audio channel count (7.1 → 5.1 → stereo → mono)
- **Arbitrary Resolution Support** - Any resolution ending in 'p' or 'i' is automatically parsed and sorted (e.g., 476p, 544p, 1440p)
- **Deprioritize Failed Streams** - Option to automatically sort failed/dead streams to the bottom
- **Visual Rank Badges** - See sort priority numbers at a glance

#### Channel Defaults
Default options applied when using bulk channel creation:

- **Default Channel Profile** - Set the default stream profile for new channels
- **Auto-Rename on Number Change** - Update channel names when numbers change
- **Include Channel Number in Name** - Add number prefix to channel names (e.g., "101 - Sports Channel")
- **Number Separator** - Choose hyphen (-), colon (:), or pipe (|) for number prefix
- **Remove Country Prefix** - Strip country codes (US, UK, CA, etc.) from names (the bulk create modal also offers a "Keep" option with normalized formatting)
- **Timezone Preference** - Default handling for East/West regional variants

These defaults are pre-loaded when opening the bulk create modal, with a "(from settings)" indicator shown.

#### Appearance
- **Theme** - Choose from three themes:
  - **Dark** (default) - Dark theme for low-light environments
  - **Light** - Bright theme for well-lit environments
  - **High Contrast** - Maximum contrast for accessibility
- **Show Stream URLs** - Toggle visibility of stream URLs in the UI (useful for screenshots or hiding sensitive information)
- **Hide Auto-Sync Groups** - Automatically hide channel groups managed by M3U auto-sync on app load
- **Hide EPG URLs** - Hide EPG source URLs in the EPG Manager tab to prevent accidental exposure in screenshots or screen shares
- **Hide M3U URLs** - Hide M3U server URLs in the M3U Manager tab to prevent accidental exposure in screenshots or screen shares
- **Gracenote ID Conflict Handling** - Control behavior when assigning Gracenote IDs to channels that already have different IDs:
  - **Ask** (default) - Show conflict dialog to review and choose which channels to overwrite
  - **Skip** - Automatically skip channels with existing IDs
  - **Overwrite** - Automatically replace all existing IDs with new ones
- **Frontend Log Level** - Set console logging verbosity (Error, Warn, Info, Debug) for troubleshooting

#### Scheduled Tasks
Automated background tasks with flexible scheduling:

- **Task Types** - EPG Refresh, M3U Refresh, Stream Probe, Database Cleanup
- **Multiple Schedules** - Each task can have multiple independent schedules
- **Schedule Types** - Interval, Daily, Weekly, Bi-weekly, or Monthly (no cron expressions needed)
- **Per-Schedule Parameters** - Each schedule can have its own configuration (batch size, timeout, channel groups, etc.)
- **Smart Defaults** - Stream Probe schedules default to values from Settings > Maintenance
- **Timezone-Aware** - All schedules respect your configured timezone
- **Real-Time Progress** - Live progress bars with success/failed/skipped counts during execution
- **Task History** - View execution history with detailed results
- **Manual Run** - Trigger any task immediately with one click
- **Enable/Disable** - Toggle individual schedules or entire tasks

#### Alert Methods
Send notifications via external services when tasks complete or errors occur:

- **Discord Webhooks** - Send alerts to Discord channels via webhook URL
- **Telegram Bots** - Send alerts via Telegram bot API
- **Email (SMTP)** - Send email alerts with configurable SMTP settings
- **Multiple Methods** - Configure multiple alert methods simultaneously
- **Source Filtering** - Control which notification types trigger each alert method
- **Digest/Batching** - Batch multiple notifications to reduce alert noise
- **Test Alerts** - Send test notifications to verify configuration
- **Failed Stream Details** - Task completion alerts include names of failed streams

#### Notification Center
In-app notification system accessible from the header:

- **Notification Bell** - Shows unread count badge
- **Notification History** - View past notifications with timestamps
- **Mark as Read** - Mark individual or all notifications as read
- **Delete Notifications** - Clear individual or all notifications
- **Notification Types** - Info, Success, Warning, Error with color coding

### Channel List Filters

Fine-tune which groups appear in the channel list:

- **Show/Hide Empty Groups** - Toggle visibility of groups with no channels
- **Show/Hide Newly Created Groups** - Toggle visibility of groups created this session
- **Show/Hide Provider Groups** - Toggle visibility of auto-populated provider groups
- **Show/Hide Manual Groups** - Toggle visibility of manually created groups
- **Show/Hide Auto-Sync Groups** - Toggle visibility of auto-channel sync groups
- **Persistent Settings** - Filter preferences saved to localStorage

### Provider Management

- **View Providers** - See all configured M3U accounts with status and stream counts
- **Provider Groups** - View and manage stream groups from each provider
- **Linked Accounts** - Link accounts together to sync group settings across providers
- **Auto-Channel Sync** - Configure automatic channel synchronization per group
- **Group Settings** - Per-group provider configuration with start channel numbers

## Roadmap

Here's what's coming to Enhanced Channel Manager:

### ~~v0.8.0 - Scheduled Tasks~~ ✅ Implemented
Automated background tasks with flexible multi-schedule support:
- **Multiple Schedules Per Task** - Each task can have multiple independent schedules
- **User-Friendly Schedule Types** - No cron expressions; choose from Interval, Daily, Weekly, Bi-weekly, or Monthly
- **Timezone-Aware** - All schedules respect your configured timezone
- **Task Types** - EPG refresh, M3U refresh, Stream probing, Database cleanup
- **Task History** - View execution history with success/failure details
- **Manual Run** - Trigger any task immediately with one click
- **Enable/Disable** - Toggle individual schedules or entire tasks on/off

### ~~v0.8.2 - Notifications~~ ✅ Implemented
In-app notifications and external alerts:
- **Toast Notifications** - Real-time feedback for actions and errors
- **Notification Center** - In-app notification history with mark read/delete
- **Email Alerts (SMTP)** - Configurable SMTP email notifications
- **Discord Webhooks** - Send alerts to Discord channels
- **Telegram Bots** - Send alerts via Telegram
- **Source Filtering** - Control which events trigger each alert method
- **Digest/Batching** - Reduce notification noise with batching
- **Failed Stream Details** - Task alerts include names of failed streams
- **Failed Stream Indicators** - Visual markers on channels/groups with probe failures

### ~~v0.8.4 - Tag-Based Normalization Engine~~ ✅ Implemented
Powerful tag-based system for stream name normalization:
- **5 Built-in Tag Groups** - Country, League, Network, Quality, Timezone with 100+ tags
- **Toggle Individual Tags** - Enable/disable specific tags within each group
- **Custom Tags** - Add your own with prefix/suffix/both mode selection
- **Expandable UI** - Collapsible tag groups with counts and bulk enable/disable
- **Quick Tag Manager** - Compact tag editor in bulk create modal for per-operation customization
- **Settings Integration** - Configure default normalization in Settings tab
- **Backward Compatible** - Migrates old custom prefix/suffix settings automatically

### ~~v0.8.6 - Stream Sorting & Probe Schedules~~ ✅ Implemented
Enhanced stream sorting options and granular probe scheduling:
- **M3U Account Priority Sort** - Sort streams by M3U account priority (configurable in M3U Manager)
- **Audio Channels Sort** - Sort streams by audio channel count (stereo, 5.1, 7.1, etc.)
- **Per-Schedule Parameters** - Each probe schedule can have its own batch_size, timeout, max_concurrent, and channel group settings
- **Multiple Probe Schedules** - Create multiple independent probe schedules with different configurations
- **Smart Defaults** - Schedule editor for Stream Probe defaults to values from Settings > Maintenance
- **Auto-Reorder After Probe** - Automatically reorder streams by quality and status after scheduled probes complete

### v0.8.7 - Granular Normalization Engine
Advanced rule-based stream name normalization:
- **Rule Creation UI** - Create custom normalization rules with conditions and actions
- **Rule Prioritization** - Drag-and-drop rule ordering to control execution priority
- **Regex Support** - Full regex pattern matching and replacement capabilities
- **Condition Types** - Contains, starts with, ends with, regex match
- **Action Types** - Remove prefix/suffix, replace, regex replace, set value
- **Rule Groups** - Organize rules into logical groups for better management
- **Rule Testing Panel** - Test rules against sample stream names before applying
- **Built-in Rules Migration** - Migrate existing tag-based normalization to new engine

### v0.9.0 - Mobile Interface
Full mobile support for managing channels on the go:
- Responsive layouts for phones and tablets
- Touch-optimized controls
- Mobile-friendly navigation
- Progressive Web App (PWA) support

### v0.10.0 - Preview Stream
Embedded video player for stream preview:
- **MPEG-TS Playback** - Native .ts stream playback using mpegts.js library
- **Preview Modal** - Click any stream to open a preview player
- **Stream Health Indicators** - Visual feedback on stream quality and stability
- **Quick Preview** - Preview streams before assigning to channels
- **Integration Points** - Preview from stream list, channel streams, and bulk create modal

### v0.11.0 - M3U Change Tracking
Track and report changes to M3U sources:
- **Snapshot System** - Store M3U state snapshots for comparison
- **Group Change Detection** - Track when groups are added or deleted
- **Stream Change Detection** - Track when streams are added, deleted, or modified
- **M3U Changes Tab** - New tab to view all detected changes with filtering
- **Email Digest Reports** - Configurable email digests when changes occur
- **Change History** - Historical view of all M3U changes over time

### v0.12.0 - Channel Auto-Creation Pipeline
Rules engine for automatic channel management:
- **Rule-Based Automation** - Create rules to automatically create/delete channels
- **Condition Builder** - Define conditions based on stream properties, groups, M3U sources
- **Action Executor** - Automatically assign logos, EPG, profiles, and groups
- **Pipeline-as-Code** - Define automation pipelines in YAML format
- **YAML Import/Export** - Share and version control your automation rules
- **Scheduled Execution** - Run pipelines on schedules or triggered by M3U refreshes
- **Dry Run Mode** - Preview what changes a pipeline would make before executing
- **Audit Trail** - Full logging of automated actions for troubleshooting

### v0.13.0 - Enhanced Stats
Advanced analytics and popularity-based channel management:
- **Channel Watch Tracking** - Track watch time and view counts per channel
- **Unique Client Tracking** - Count unique connecting IPs per channel
- **Upstream/Downstream Bandwidth** - Monitor bandwidth utilization in both directions
- **Popularity Scoring** - Calculate channel popularity based on multiple metrics
- **Auto-Add Popular Channels** - Automatically add channels that become popular
- **Auto-Remove Unpopular** - Optionally remove channels with low viewership
- **Popularity Dashboard** - Visual analytics for channel performance
- **Trend Analysis** - Track popularity changes over time

---

## Technical Stack

### Frontend
- React 18 with TypeScript
- Vite for bundling
- @dnd-kit for drag-and-drop
- CSS with custom styling (dark theme)

### Backend
- Python with FastAPI
- Proxy to Dispatcharr API
- Health check endpoint
- CORS support

### Deployment
- Docker containerized
- Single container with frontend served as static files
- Configurable via environment variables

## Getting Started

### Docker Compose

```yaml
services:
  ecm:
    image: ghcr.io/motwakorb/enhancedchannelmanager:latest
    ports:
      - "6100:6100"
    volumes:
      - ./config:/config
```

The Dispatcharr URL can be configured through the Settings modal in the UI, which persists to the config volume.

### Development

```bash
# Frontend
cd frontend
npm install
npm run dev

# Backend
cd backend
pip install -r requirements.txt
uvicorn main:app --reload
```

## API Endpoints

### Channels
- `GET /api/channels` - List channels (paginated, searchable, filterable)
- `POST /api/channels` - Create channel
- `GET /api/channels/{id}` - Get channel details
- `PATCH /api/channels/{id}` - Update channel
- `DELETE /api/channels/{id}` - Delete channel
- `POST /api/channels/{id}/add-stream` - Add stream to channel
- `POST /api/channels/{id}/remove-stream` - Remove stream from channel
- `POST /api/channels/{id}/reorder-streams` - Reorder channel streams
- `POST /api/channels/assign-numbers` - Bulk assign channel numbers

### Channel Groups
- `GET /api/channel-groups` - List all groups
- `GET /api/channel-groups/orphaned` - List orphaned groups (no streams, channels, or M3U association)
- `POST /api/channel-groups` - Create group
- `PATCH /api/channel-groups/{id}` - Update group
- `DELETE /api/channel-groups/{id}` - Delete group
- `DELETE /api/channel-groups/orphaned` - Delete orphaned groups (optionally specify group IDs)

### Streams
- `GET /api/streams` - List streams (paginated, searchable, filterable)
- `GET /api/stream-groups` - List stream groups

### Logos
- `GET /api/channels/logos` - List logos (paginated, searchable)
- `POST /api/channels/logos` - Create logo from URL
- `POST /api/channels/logos/upload` - Upload logo file
- `PATCH /api/channels/logos/{id}` - Update logo
- `DELETE /api/channels/logos/{id}` - Delete logo

### EPG
- `GET /api/epg/sources` - List EPG sources
- `POST /api/epg/sources` - Create EPG source (including dummy sources)
- `PATCH /api/epg/sources/{id}` - Update EPG source
- `DELETE /api/epg/sources/{id}` - Delete EPG source
- `POST /api/epg/sources/{id}/refresh` - Refresh EPG source
- `GET /api/epg/data` - Search EPG data (paginated)
- `GET /api/epg/grid` - Get EPG program grid for guide view

### Stream Profiles
- `GET /api/stream-profiles` - List available profiles

### Providers
- `GET /api/providers` - List M3U accounts
- `GET /api/providers/group-settings` - Get provider group settings

### Settings
- `GET /api/settings` - Get current settings
- `POST /api/settings` - Update settings
- `POST /api/settings/test` - Test connection

### Stream Stats
- `GET /api/stream-stats` - Get live channel statistics
- `GET /api/stream-stats/bandwidth` - Get bandwidth summary and history
- `GET /api/stream-stats/events` - Get system events
- `GET /api/stream-stats/top-watched` - Get top watched channels
- `POST /api/stream-stats/probe` - Start stream probe
- `GET /api/stream-stats/probe/progress` - Get probe progress
- `POST /api/stream-stats/probe/cancel` - Cancel running probe
- `GET /api/stream-stats/probe/history` - Get probe history

### Journal
- `GET /api/journal` - Get journal entries (paginated, filterable)
- `GET /api/journal/stats` - Get journal statistics

### Notifications
- `GET /api/notifications` - Get notifications (paginated, filterable by read status)
- `PATCH /api/notifications/{id}` - Update notification (mark as read)
- `DELETE /api/notifications/{id}` - Delete notification
- `POST /api/notifications/mark-all-read` - Mark all notifications as read
- `DELETE /api/notifications/clear` - Clear notifications (read only or all)

### Alert Methods
- `GET /api/alert-methods` - List all alert methods
- `POST /api/alert-methods` - Create alert method
- `GET /api/alert-methods/{id}` - Get alert method details
- `PATCH /api/alert-methods/{id}` - Update alert method
- `DELETE /api/alert-methods/{id}` - Delete alert method
- `POST /api/alert-methods/{id}/test` - Send test notification

### Scheduled Tasks
- `GET /api/tasks` - List all tasks with status
- `GET /api/tasks/{id}` - Get task details with schedules
- `PATCH /api/tasks/{id}` - Update task configuration
- `POST /api/tasks/{id}/run` - Run task immediately
- `POST /api/tasks/{id}/cancel` - Cancel running task
- `GET /api/tasks/{id}/history` - Get task execution history
- `GET /api/tasks/{id}/schedules` - Get task schedules
- `POST /api/tasks/{id}/schedules` - Add schedule to task
- `PATCH /api/tasks/{id}/schedules/{schedule_id}` - Update schedule
- `DELETE /api/tasks/{id}/schedules/{schedule_id}` - Delete schedule

### Health
- `GET /api/health` - Health check

## Utility Scripts

### Search Stream Script

The `scripts/search-stream.sh` utility allows you to search for streams in Dispatcharr via the command line:

```bash
# Make executable
chmod +x scripts/search-stream.sh

# Search for a stream
./scripts/search-stream.sh http://dispatcharr:9191 admin password "HBO Max HD"

# Search with special characters
./scripts/search-stream.sh http://dispatcharr:9191 admin password "[PPV EVENT 38]"

# Search partial name
./scripts/search-stream.sh http://dispatcharr:9191 admin password "ESPN"
```

**Features:**
- Handles authentication automatically
- URL-encodes special characters and spaces
- Returns pretty-printed JSON results
- Supports searching across all M3U accounts

**Extract specific fields:**
```bash
# Get just name and ID
./scripts/search-stream.sh http://dispatcharr:9191 admin pass "HBO" | jq '.results[] | {id, name}'

# Get count of results
./scripts/search-stream.sh http://dispatcharr:9191 admin pass "ESPN" | jq '.count'
```

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl/Cmd + Z` | Undo (edit mode or global) |
| `Ctrl/Cmd + Shift + Z` | Redo (edit mode or global) |
| `Enter` | Save when editing inline |
| `Escape` | Cancel when editing inline |
| `Ctrl/Cmd + Click` | Toggle channel selection |
| `Shift + Click` | Select range of channels |

## License

MIT
