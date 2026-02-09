# Enhanced Channel Manager

A professional-grade web interface for managing IPTV configurations with Dispatcharr. Features a tab-based interface for channel management, EPG data, logos, and more. Built with React + TypeScript frontend and Python FastAPI backend.

## Application Tabs

- **M3U Manager** - Full M3U account management with linked accounts and group synchronization
- **M3U Changes** - Track and review all changes detected in M3U playlists (groups added/removed, streams added/removed)
- **EPG Manager** - Manage EPG sources with drag-and-drop priority ordering
- **Channel Manager** - Full-featured channel and stream management with split-pane layout
- **Guide** - TV Guide with EPG grid view showing program schedules
- **Logo Manager** - Logo management with search, upload, and URL import
- **Auto-Creation** - Rules-based automation pipeline for automatic channel creation, stream merging, and orphan management
- **Journal** - Activity log tracking all changes to channels, EPG, and M3U accounts
- **Stats** - Live streaming statistics, M3U connection counts, bandwidth tracking, and charts
- **Settings** - Configure Dispatcharr connection, channel defaults, stream probing, scheduled tasks, alert methods, authentication, and appearance

## Features

### Authentication (v0.11.5)

ECM includes a comprehensive authentication system to secure access to your channel management:

- **First-Run Setup** - On first launch, create an administrator account through a setup wizard
- **Local Authentication** - Username/password authentication with secure bcrypt password hashing
- **Dispatcharr SSO** - Single sign-on using your existing Dispatcharr credentials
- **Account Linking** - Link multiple authentication methods to a single user account (e.g., local + Dispatcharr)
- **Password Reset** - Email-based password reset with secure time-limited tokens (requires SMTP configuration)
- **CLI Password Reset** - Reset passwords from the command line when locked out (see [Utility Scripts](#utility-scripts))
- **User Management** - Admin panel for managing user accounts, roles, and access
- **Session Management** - JWT-based sessions with automatic token refresh

#### Authentication Providers

- **Local** - Create and manage local user accounts with password authentication
- **Dispatcharr** - Authenticate using your Dispatcharr server credentials (SSO)

#### User Management (Admin)

Administrators can manage users through Settings → Users:
- View all user accounts with their roles and status
- Edit user email, display name, admin status, and active status
- Activate/deactivate user accounts
- Delete user accounts (soft delete - deactivates the account)

#### Password Reset

When SMTP is configured in Settings → Email Settings:
- Users see "Forgot password?" link on the login page
- Password reset emails contain a secure link valid for 1 hour
- Professional HTML email template with fallback plain text
- Security: Always returns success to prevent email enumeration

#### CLI Password Reset

When locked out or SMTP is not configured, reset passwords from the command line:
```bash
# Interactive mode (lists users, prompts for password)
docker exec -it enhancedchannelmanager python /app/reset_password.py

# Non-interactive (for scripting)
docker exec enhancedchannelmanager python /app/reset_password.py -u admin -p 'NewPass123'

# Skip password strength validation
docker exec enhancedchannelmanager python /app/reset_password.py -u admin -p 'simple' --force
```

### Auto-Creation Pipeline (v0.12.0)

A rules-based automation engine for automatic channel creation, stream merging, and lifecycle management:

#### Rule Builder
- **Create Rules** - Define automation rules with conditions, actions, and priority ordering
- **Rule Priority** - Drag-and-drop reordering to control execution order
- **Enable/Disable** - Toggle individual rules without deleting
- **Duplicate Rules** - Clone rules as a starting point for new rules
- **Run on M3U Refresh** - Optionally auto-execute rules when M3U accounts refresh
- **Stop on First Match** - Stop evaluating further rules when a stream matches
- **Normalize Names** - Apply name normalization during processing
- **YAML Import/Export** - Share and version control rule definitions

#### Conditions
Build complex matching logic with AND/OR connectors:
- **Stream Name** - Contains, matches (regex), starts/ends with
- **Stream Group** - Contains, matches (regex)
- **TVG ID** - Exists/not exists, matches pattern
- **Logo** - Exists/not exists
- **Provider** - Filter by specific M3U account
- **Quality** - Minimum/maximum resolution (2160p, 1080p, 720p, 480p, 360p)
- **Codec** - Filter by video codec (H.264, HEVC, etc.)
- **Channel Exists** - Check if channel already exists (by name, regex, or group)

#### Actions
Define what happens when conditions match:
- **Create Channel** - Template-based naming with variables (`{stream_name}`, `{stream_group}`, `{quality}`, `{provider}`, etc.)
- **Create Group** - Automatically create channel groups with template naming
- **Merge Streams** - Combine multiple streams into a single channel with quality preference ordering
- **Assign Logo/EPG/Profile** - Automatically assign channel metadata
- **Set Channel Number** - Auto-assign or specify channel numbering (including ranges)
- **Set Variable** - Define reusable variables with regex extraction for use in templates
- **Name Transform** - Apply regex find/replace to channel names
- **If Exists Behavior** - Skip, merge, update, or use existing when channels already exist

#### Execution
- **Dry Run Mode** - Preview all changes before applying
- **Execute Mode** - Apply changes with full audit trail
- **Run Single Rule** - Execute or dry-run a specific rule in isolation
- **Rollback** - Undo a completed execution to restore previous state
- **Execution History** - View past runs with duration, match counts, and created/updated/merged counts
- **Execution Log** - Per-stream granular log showing condition evaluation, matched rules, and action results

#### Smart Stream Handling
- **Quality-Based Sorting** - Sort streams within channels by resolution (highest first)
- **Probe on Sort** - Optionally probe unprobed streams for resolution data before sorting
- **Multi-Criteria Sort** - Sort by stream name, natural name, group, or quality
- **User Settings Integration** - Honors channel numbering, default profile, timezone preference, and auto-rename settings

#### Orphan Reconciliation
Automatically manage channels that no longer match any rule:
- **Delete** - Remove orphaned channels entirely
- **Move to Uncategorized** - Preserve channels by moving them out of managed groups
- **Delete and Cleanup Groups** - Delete channels and remove now-empty groups
- **None** - Skip reconciliation (preserve all channels)

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

### M3U Change Tracking

- **Automatic Change Detection** - Detects changes every time an M3U account is refreshed
- **Group Changes** - Track when groups are added or removed from M3U playlists
- **Stream Changes** - Monitor streams added or removed within existing groups
- **Change History** - Full searchable history of all detected changes
- **Filtering** - Filter changes by M3U account, change type (group/stream add/remove), and enabled status
- **Sorting** - Sort by time, account, type, group name, count, or enabled status
- **Summary Statistics** - Dashboard cards showing total groups/streams added and removed
- **Time Range Filter** - View changes from last 24 hours, 3 days, 7 days, 30 days, or 90 days
- **Expandable Details** - Click any change row to see full details including stream names
- **Enabled/Disabled Tracking** - See whether affected groups were enabled or disabled in the M3U
- **M3U Change Monitor Task** - Background task polls for external changes made in Dispatcharr
- **M3U Digest Emails** - Configure email notifications for M3U changes (immediate, hourly, daily, weekly)

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
- **Stream Preview** - Preview streams directly in the browser before assigning to channels
  - MPEG-TS playback using mpegts.js library
  - Three preview modes: Passthrough (direct), Transcode (AAC audio), Video Only (no audio)
  - Mode indicator shows current preview mode in the modal
  - Alternative options: Open in VLC, Download M3U, Copy URL

### Channel Preview

- **Channel Output Preview** - Preview channels as they would appear to clients through Dispatcharr
  - Tests the actual Dispatcharr proxy stream output
  - Verifies channel configuration is working correctly
  - Same preview modes as stream preview (Passthrough, Transcode, Video Only)
  - JWT authentication handled automatically

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
- **Search Logos** - Search logos by name with client-side filtering
- **Add from URL** - Create logos from external URLs
- **Upload Files** - Upload logo files directly to Dispatcharr
- **Usage Tracking** - See how many channels use each logo
- **Pagination** - Configurable page sizes (25, 50, 100, 250) with page navigation
- **Full Logo Loading** - Automatically paginates through Dispatcharr's API to load all logos

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

### Enhanced Stats (v0.11.0)

Advanced analytics and channel popularity tracking:

- **Unique Viewer Tracking** - Count unique connecting IPs per channel over configurable time periods
- **Per-Channel Bandwidth** - Track bandwidth consumption by channel with breakdown by connections and watch time
- **Popularity Scoring** - Weighted algorithm calculates channel popularity based on:
  - Watch count (default 30% weight)
  - Watch time (default 30% weight)
  - Unique viewers (default 25% weight)
  - Bandwidth usage (default 15% weight)
- **Popularity Rankings** - View channels ranked by popularity score with pagination
- **Trend Analysis** - Track popularity changes over time:
  - **Trending Up** - Channels gaining popularity (>10% increase)
  - **Trending Down** - Channels losing popularity (>10% decrease)
  - **Stable** - Channels with consistent viewership
- **Trend Indicators** - Visual arrows and percentage changes on channel rankings
- **Period Selector** - View stats for 7, 14, or 30 day periods
- **On-Demand Calculation** - Trigger popularity score recalculation manually
- **Watch History Log** - Detailed log of all channel viewing sessions with IP addresses and durations

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

#### Normalization Engine (Rule-Based)
Advanced rule-based normalization system for granular control over stream name transformations:

- **Custom Rules** - Create rules with flexible conditions and actions
- **Compound Conditions** - Build complex logic with AND/OR/NOT operators
- **Condition Types** - Contains, starts with, ends with, equals, regex match
- **Action Types** - Remove prefix/suffix, replace text, regex replace, set value
- **Rule Ordering** - Drag-and-drop to set execution priority
- **Enable/Disable Rules** - Toggle individual rules without deleting
- **Testing Panel** - Test rules against sample names with real-time preview
- **Normalize on Create** - Automatically apply normalization when creating channels

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

#### Stream Preview
Configure how streams and channels are previewed in the browser:

- **Preview Mode** - Choose how streams are processed for browser playback:
  - **Passthrough** - Direct proxy, fastest but may fail on AC-3/E-AC-3/DTS audio codecs
  - **Transcode** - FFmpeg transcodes audio to AAC for browser compatibility (recommended)
  - **Video Only** - Strip audio entirely for silent quick preview
- **Mode Switching** - Change mode anytime; takes effect on next preview
- **Requires FFmpeg** - Transcode and Video Only modes require FFmpeg installed in the container (included in official Docker image)

#### Channel Defaults
Default options applied when using bulk channel creation:

- **Default Channel Profile** - Set the default stream profile for new channels
- **Auto-Rename on Number Change** - Update channel names when numbers change
- **Include Channel Number in Name** - Add number prefix to channel names (e.g., "101 - Sports Channel")
- **Number Separator** - Choose hyphen (-), colon (:), or pipe (|) for number prefix
- **Remove Country Prefix** - Strip country codes (US, UK, CA, etc.) from names (the bulk create modal also offers a "Keep" option with normalized formatting)
- **Timezone Preference** - Default handling for East/West regional variants

These defaults are pre-loaded when opening the bulk create modal, with a "(from settings)" indicator shown.

#### Authentication
Configure how users authenticate to ECM:

- **Require Authentication** - Enable or disable authentication requirement
- **Primary Auth Mode** - Choose between Local or Dispatcharr as the primary authentication method
- **Local Authentication** - Enable/disable local username/password authentication
- **Dispatcharr Authentication** - Enable/disable Dispatcharr SSO authentication

#### User Management
Admin panel for managing user accounts (requires admin privileges):

- **User List** - View all users with username, email, provider, status, and role
- **Edit Users** - Modify user email, display name, admin status, and active status
- **Toggle Active Status** - Quickly activate or deactivate user accounts
- **Delete Users** - Remove user accounts (soft delete)

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

#### VLC Integration
Open streams directly in VLC media player from your browser:

- **Open in VLC Behavior** - Choose how "Open in VLC" buttons work:
  - **Try VLC Protocol** - Attempt vlc:// protocol, show helper if it fails
  - **Fallback to M3U** - Try vlc:// first, then download M3U file if it fails
  - **Always M3U** - Always download M3U file (most compatible)
- **Protocol Handler Scripts** - Downloadable scripts to register the vlc:// protocol handler:
  - **Windows** - PowerShell script with auto-elevation and registry setup
  - **Linux** - Shell script creating .desktop file for xdg-open
  - **macOS** - Shell script creating AppleScript handler app
- **Helper Modal** - When vlc:// fails, shows OS-specific setup instructions with download buttons
- **M3U Fallback** - Download M3U playlist files that open directly in VLC

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

Fine-tune which groups and channels appear in the channel list:

- **Show/Hide Empty Groups** - Toggle visibility of groups with no channels
- **Show/Hide Newly Created Groups** - Toggle visibility of groups created this session
- **Show/Hide Provider Groups** - Toggle visibility of auto-populated provider groups
- **Show/Hide Manual Groups** - Toggle visibility of manually created groups
- **Show/Hide Auto-Sync Groups** - Toggle visibility of auto-channel sync groups
- **Missing Data Filters** - Filter channels by missing metadata:
  - Missing Logo - Show channels without a logo assigned
  - Missing TVG-ID - Show channels without a TVG ID
  - Missing EPG Data - Show channels without EPG data
  - Missing Gracenote - Show channels without a Gracenote ID
  - Active filter indicator on the filter button
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

### ~~v0.8.7 - Granular Normalization Engine~~ ✅ Implemented
Advanced rule-based stream name normalization:
- **Rule Creation UI** - Create custom normalization rules with conditions and actions
- **Rule Prioritization** - Drag-and-drop rule ordering to control execution priority
- **Compound Conditions** - Build complex conditions with AND/OR/NOT logic
- **Regex Support** - Full regex pattern matching and replacement capabilities
- **Condition Types** - Contains, starts with, ends with, regex match, equals
- **Action Types** - Remove prefix/suffix, replace, regex replace, set value
- **Rule Testing Panel** - Test rules against sample stream names with real-time preview
- **Normalization on Channel Create** - Option to automatically normalize names when creating channels
- **EPG/M3U Manual Refresh** - Set refresh interval to 0 for manual-only refresh (no auto-refresh)

### ~~v0.9.0 - M3U Change Tracking~~ ✅ Implemented
Track and report changes to M3U sources:
- **Automatic Change Detection** - Detects changes every time an M3U account is refreshed
- **Snapshot System** - Store M3U state snapshots for comparison
- **Group Change Detection** - Track when groups are added or deleted
- **Stream Change Detection** - Track when streams are added, deleted, or modified
- **M3U Changes Tab** - New tab to view all detected changes with filtering
- **Email Digest Reports** - Configurable email digests when changes occur (immediate, hourly, daily, weekly)
- **Change History** - Full searchable history of all M3U changes over time
- **Summary Statistics** - Dashboard cards showing total groups/streams added and removed

### ~~v0.10.0 - Stream Preview~~ ✅ Implemented
Embedded video player for stream and channel preview:
- **MPEG-TS Playback** - Native .ts stream playback using mpegts.js library for browser compatibility
- **Preview Modal** - Click any stream or channel to open a preview player
- **Three Preview Modes** - Configurable in Settings:
  - **Passthrough** - Direct stream proxy (fastest, may fail on AC-3/E-AC-3 audio)
  - **Transcode** - FFmpeg transcodes audio to AAC for browser compatibility
  - **Video Only** - Strip audio for quick silent preview
- **Mode Indicator** - Shows current preview mode in the modal with icon and tooltip
- **Channel Preview** - Preview channel output through Dispatcharr's TS proxy with JWT authentication
- **Alternative Options** - Open in VLC, Download M3U, Copy URL (for streams with direct URLs)
- **Stream Metadata Display** - Shows stream name, TVG-ID, channel group, and M3U provider
- **VLC Protocol Handler Scripts** - One-click "Open in VLC" with scripts for Windows, Linux, and macOS
- **VLC Protocol Helper Modal** - Download scripts and view setup instructions in Settings → Advanced

### ~~v0.11.0 - Enhanced Stats~~ ✅ Implemented
Advanced analytics and channel popularity tracking:
- **Channel Watch Tracking** - Track watch time and view counts per channel
- **Unique Viewer Tracking** - Count unique connecting IPs per channel
- **Per-Channel Bandwidth** - Monitor bandwidth consumption per channel
- **Popularity Scoring** - Weighted algorithm based on watch count, watch time, unique viewers, and bandwidth
- **Popularity Rankings** - View channels ranked by popularity with pagination
- **Trend Analysis** - Track popularity changes (trending up, down, or stable)
- **Period Selection** - View stats for 7, 14, or 30 day periods
- **On-Demand Calculation** - Manually trigger popularity score recalculation
- **Watch History Log** - Detailed log of viewing sessions with IP addresses

### ~~v0.11.5 - Authentication System~~ ✅ Implemented
Comprehensive authentication and user management:
- **First-Run Setup Wizard** - Create initial admin account on first launch
- **Local Authentication** - Username/password auth with bcrypt password hashing
- **Dispatcharr SSO** - Single sign-on using Dispatcharr credentials
- **Password Reset** - Email-based password reset with secure time-limited tokens
- **User Management** - Admin panel for managing users, roles, and access
- **Session Management** - JWT-based sessions with automatic token refresh
- **SMTP Integration** - Password reset requires SMTP configuration; link hidden when not configured

### ~~v0.12.0 - Auto-Creation Pipeline~~ ✅ Implemented
Rules engine for automatic channel management:
- **Rule-Based Automation** - Create rules with conditions and actions to automatically create channels, merge streams, and assign metadata
- **Condition Builder** - Match streams by name, group, provider, quality, codec, TVG ID, logo, and more with AND/OR logic
- **Action Executor** - Create channels/groups, merge streams, assign logos/EPG/profiles, set channel numbers, define variables
- **Template Variables** - Dynamic naming with `{stream_name}`, `{stream_group}`, `{quality}`, `{provider}`, and custom variables
- **YAML Import/Export** - Share and version control your automation rules
- **Dry Run Mode** - Preview what changes a pipeline would make before executing
- **Execution Rollback** - Undo completed executions to restore previous state
- **Orphan Reconciliation** - Automatically manage channels that no longer match (delete, move, or preserve)
- **Quality-Based Sorting** - Sort streams within channels by resolution with optional probe-on-sort
- **User Settings Integration** - Honors channel numbering, default profile, timezone, and auto-rename preferences
- **Execution Log** - Per-stream granular log showing condition evaluation, rule matching, and action results
- **CLI Password Reset** - `reset_password.py` utility for command-line password recovery
- **Missing Data Filters** - Filter channels by missing logo, TVG-ID, EPG, or Gracenote ID
- **Account Linking** - Link multiple authentication methods to a single user account
- **Design Tokens & Theme Compliance** - CSS variable system for consistent theming across all components
- **Logo Manager Improvements** - File upload to Dispatcharr, pagination, full logo loading

### v0.13.0 - Mobile Interface
Full mobile support for managing channels on the go:
- Responsive layouts for phones and tablets
- Touch-optimized controls
- Mobile-friendly navigation
- Progressive Web App (PWA) support

### v0.14.0 - Auto-Channel Management
Popularity-based automatic channel management:
- **Auto-Add Popular Channels** - Automatically add channels that become popular
- **Auto-Remove Unpopular** - Optionally remove channels with low viewership
- **Popularity Thresholds** - Configurable thresholds for auto-add/remove actions
- **Notification on Changes** - Alert when channels are auto-added or removed

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
      - "6100:6100"   # HTTP (always available)
      - "6143:6143"   # HTTPS (when TLS is enabled)
    volumes:
      - ./config:/config
```

The Dispatcharr URL can be configured through the Settings modal in the UI, which persists to the config volume.

**Port Configuration:**
- **Port 6100** - HTTP interface (always available as fallback)
- **Port 6143** - HTTPS interface (when TLS is configured in Settings → TLS Certificates)

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

Interactive API documentation is available at `/api/docs` (Swagger UI) and `/api/redoc` (ReDoc).

### Channels

| Endpoint | Description |
|----------|-------------|
| `GET /api/channels` | List channels (paginated, searchable, filterable) |
| `POST /api/channels` | Create channel |
| `GET /api/channels/{id}` | Get channel details |
| `GET /api/channels/{id}/streams` | Get streams for a channel |
| `PATCH /api/channels/{id}` | Update channel |
| `DELETE /api/channels/{id}` | Delete channel |
| `POST /api/channels/{id}/add-stream` | Add stream to channel |
| `POST /api/channels/{id}/remove-stream` | Remove stream from channel |
| `POST /api/channels/{id}/reorder-streams` | Reorder channel streams |
| `POST /api/channels/assign-numbers` | Bulk assign channel numbers |
| `POST /api/channels/bulk-commit` | Batch multiple channel operations in one request |
| `POST /api/channels/clear-auto-created` | Clear auto-created flag from channels |
| `GET /api/channels/csv-template` | Download CSV template for channel import |
| `GET /api/channels/export-csv` | Export all channels to CSV |
| `POST /api/channels/import-csv` | Import channels from CSV file |
| `POST /api/channels/preview-csv` | Preview and validate CSV before import |

### Channel Groups

| Endpoint | Description |
|----------|-------------|
| `GET /api/channel-groups` | List all groups |
| `POST /api/channel-groups` | Create group |
| `PATCH /api/channel-groups/{id}` | Update group |
| `DELETE /api/channel-groups/{id}` | Delete group |
| `GET /api/channel-groups/orphaned` | List orphaned groups (no streams, channels, or M3U association) |
| `DELETE /api/channel-groups/orphaned` | Delete orphaned groups (optionally specify group IDs) |
| `GET /api/channel-groups/hidden` | List hidden channel groups |
| `POST /api/channel-groups/{id}/restore` | Restore a hidden channel group |
| `GET /api/channel-groups/auto-created` | List groups with auto-created channels |
| `GET /api/channel-groups/with-streams` | List groups that have channels with streams |

### Logos

| Endpoint | Description |
|----------|-------------|
| `GET /api/channels/logos` | List logos (paginated, searchable) |
| `GET /api/channels/logos/{id}` | Get a single logo |
| `POST /api/channels/logos` | Create logo from URL |
| `POST /api/channels/logos/upload` | Upload logo image file |
| `PATCH /api/channels/logos/{id}` | Update logo |
| `DELETE /api/channels/logos/{id}` | Delete logo |

### Streams

| Endpoint | Description |
|----------|-------------|
| `GET /api/streams` | List streams (paginated, searchable, filterable) |
| `GET /api/stream-groups` | List stream groups with stream counts |

### M3U

| Endpoint | Description |
|----------|-------------|
| `GET /api/m3u/accounts/{id}` | Get M3U account details |
| `GET /api/m3u/accounts/{id}/stream-metadata` | Get stream metadata (tvg-id mappings) |
| `POST /api/m3u/accounts` | Create M3U account |
| `PUT /api/m3u/accounts/{id}` | Update M3U account (full) |
| `PATCH /api/m3u/accounts/{id}` | Partially update M3U account |
| `DELETE /api/m3u/accounts/{id}` | Delete M3U account |
| `POST /api/m3u/upload` | Upload M3U file |
| `POST /api/m3u/refresh` | Refresh all active M3U accounts |
| `POST /api/m3u/refresh/{id}` | Refresh a single M3U account |
| `POST /api/m3u/accounts/{id}/refresh-vod` | Refresh VOD content (XtreamCodes) |
| `GET /api/m3u/accounts/{id}/filters` | List filters for an account |
| `POST /api/m3u/accounts/{id}/filters` | Create filter for an account |
| `PUT /api/m3u/accounts/{id}/filters/{fid}` | Update a filter |
| `DELETE /api/m3u/accounts/{id}/filters/{fid}` | Delete a filter |
| `GET /api/m3u/accounts/{id}/profiles/` | List profiles for an account |
| `POST /api/m3u/accounts/{id}/profiles/` | Create profile for an account |
| `GET /api/m3u/accounts/{id}/profiles/{pid}/` | Get a specific profile |
| `PATCH /api/m3u/accounts/{id}/profiles/{pid}/` | Update a profile |
| `DELETE /api/m3u/accounts/{id}/profiles/{pid}/` | Delete a profile |
| `PATCH /api/m3u/accounts/{id}/group-settings` | Update group settings for an account |
| `GET /api/m3u/accounts/{id}/changes` | Get change history for an account |
| `GET /api/m3u/snapshots` | List M3U snapshots |
| `GET /api/m3u/server-groups` | List server groups |
| `POST /api/m3u/server-groups` | Create server group |
| `PATCH /api/m3u/server-groups/{id}` | Update server group |
| `DELETE /api/m3u/server-groups/{id}` | Delete server group |

### M3U Digest

| Endpoint | Description |
|----------|-------------|
| `GET /api/m3u/changes` | Get M3U change history (paginated, filterable) |
| `GET /api/m3u/changes/summary` | Get change summary for a time period |
| `GET /api/m3u/digest/settings` | Get digest email settings |
| `PUT /api/m3u/digest/settings` | Update digest email settings |
| `POST /api/m3u/digest/test` | Send a test digest email |

### EPG

| Endpoint | Description |
|----------|-------------|
| `GET /api/epg/sources` | List EPG sources |
| `GET /api/epg/sources/{id}` | Get EPG source details |
| `POST /api/epg/sources` | Create EPG source (including dummy sources) |
| `PATCH /api/epg/sources/{id}` | Update EPG source |
| `DELETE /api/epg/sources/{id}` | Delete EPG source |
| `POST /api/epg/sources/{id}/refresh` | Refresh EPG source |
| `POST /api/epg/import` | Trigger EPG import |
| `GET /api/epg/data` | Search EPG data (paginated) |
| `GET /api/epg/data/{id}` | Get individual EPG data entry |
| `GET /api/epg/grid` | Get EPG program grid for guide view |
| `GET /api/epg/lcn` | Get LCN (Logical Channel Number) for a TVG-ID |
| `POST /api/epg/lcn/batch` | Batch LCN lookup for multiple TVG-IDs |

### Channel Profiles

| Endpoint | Description |
|----------|-------------|
| `GET /api/channel-profiles` | List all channel profiles |
| `POST /api/channel-profiles` | Create channel profile |
| `GET /api/channel-profiles/{id}` | Get channel profile |
| `PATCH /api/channel-profiles/{id}` | Update channel profile |
| `DELETE /api/channel-profiles/{id}` | Delete channel profile |
| `PATCH /api/channel-profiles/{id}/channels/bulk-update` | Bulk enable/disable channels for a profile |
| `PATCH /api/channel-profiles/{id}/channels/{cid}` | Enable/disable a single channel for a profile |

### Stream Profiles

| Endpoint | Description |
|----------|-------------|
| `GET /api/stream-profiles` | List available stream profiles |

### Providers

| Endpoint | Description |
|----------|-------------|
| `GET /api/providers` | List M3U accounts (legacy) |
| `GET /api/providers/group-settings` | Get provider group settings |

### Settings

| Endpoint | Description |
|----------|-------------|
| `GET /api/settings` | Get current settings |
| `POST /api/settings` | Update settings |
| `POST /api/settings/test` | Test Dispatcharr connection |
| `POST /api/settings/test-smtp` | Test SMTP connection |
| `POST /api/settings/test-discord` | Test Discord webhook |
| `POST /api/settings/test-telegram` | Test Telegram bot |
| `POST /api/settings/restart-services` | Restart background services |
| `POST /api/settings/reset-stats` | Reset all statistics |

### Stream Stats

| Endpoint | Description |
|----------|-------------|
| `GET /api/stream-stats` | Get all stream probe statistics |
| `GET /api/stream-stats/summary` | Get probe statistics summary |
| `GET /api/stream-stats/{id}` | Get probe stats for a specific stream |
| `POST /api/stream-stats/by-ids` | Get probe stats for multiple streams |
| `POST /api/stream-stats/probe/{id}` | Probe a single stream |
| `POST /api/stream-stats/probe/bulk` | Probe multiple streams |
| `POST /api/stream-stats/probe/all` | Probe all streams (background task) |
| `GET /api/stream-stats/probe/progress` | Get probe progress |
| `GET /api/stream-stats/probe/results` | Get results of last probe-all operation |
| `GET /api/stream-stats/probe/history` | Get probe run history |
| `POST /api/stream-stats/probe/cancel` | Cancel running probe |
| `POST /api/stream-stats/probe/reset` | Force reset stuck probe state |
| `POST /api/stream-stats/dismiss` | Dismiss probe failures for streams |
| `GET /api/stream-stats/dismissed` | Get list of dismissed stream IDs |
| `POST /api/stream-stats/clear` | Clear probe stats for specific streams |
| `POST /api/stream-stats/clear-all` | Clear all probe stats |

### Enhanced Stats

| Endpoint | Description |
|----------|-------------|
| `GET /api/stats/bandwidth` | Get bandwidth summary with in/out breakdown |
| `GET /api/stats/channels` | Get status of all active channels |
| `GET /api/stats/channels/{id}` | Get detailed stats for a channel |
| `GET /api/stats/activity` | Get system activity events |
| `POST /api/stats/channels/{id}/stop` | Stop a channel |
| `POST /api/stats/channels/{id}/stop-client` | Stop a specific client connection |
| `GET /api/stats/top-watched` | Get top watched channels |
| `GET /api/stats/unique-viewers` | Get unique viewer summary for period |
| `GET /api/stats/channel-bandwidth` | Get per-channel bandwidth stats |
| `GET /api/stats/unique-viewers-by-channel` | Get unique viewers per channel |
| `GET /api/stats/watch-history` | Get watch history log (paginated) |

### Popularity

| Endpoint | Description |
|----------|-------------|
| `GET /api/stats/popularity/rankings` | Get channel popularity rankings (paginated) |
| `GET /api/stats/popularity/channel/{id}` | Get popularity score for specific channel |
| `GET /api/stats/popularity/trending` | Get trending channels (up or down) |
| `POST /api/stats/popularity/calculate` | Trigger popularity score calculation |

### Normalization

| Endpoint | Description |
|----------|-------------|
| `GET /api/normalization/rules` | Get all rules organized by group |
| `GET /api/normalization/rules/{id}` | Get a specific rule |
| `POST /api/normalization/rules` | Create rule |
| `PATCH /api/normalization/rules/{id}` | Update rule |
| `DELETE /api/normalization/rules/{id}` | Delete rule |
| `GET /api/normalization/groups` | List rule groups |
| `POST /api/normalization/groups` | Create rule group |
| `GET /api/normalization/groups/{id}` | Get rule group |
| `PATCH /api/normalization/groups/{id}` | Update rule group |
| `DELETE /api/normalization/groups/{id}` | Delete rule group and all its rules |
| `POST /api/normalization/groups/reorder` | Reorder rule groups |
| `POST /api/normalization/groups/{id}/rules/reorder` | Reorder rules within a group |
| `POST /api/normalization/test` | Test a rule against sample text |
| `POST /api/normalization/test-batch` | Test all enabled rules against multiple texts |
| `POST /api/normalization/normalize` | Normalize text using all enabled rules |
| `GET /api/normalization/rule-stats` | Get stream match statistics per rule |
| `GET /api/normalization/migration/status` | Get migration status |
| `POST /api/normalization/migration/run` | Run demo rules migration |

### Tags

| Endpoint | Description |
|----------|-------------|
| `GET /api/tags/groups` | List all tag groups with counts |
| `POST /api/tags/groups` | Create tag group |
| `GET /api/tags/groups/{id}` | Get tag group with all tags |
| `PATCH /api/tags/groups/{id}` | Update tag group |
| `DELETE /api/tags/groups/{id}` | Delete tag group and all tags |
| `POST /api/tags/groups/{id}/tags` | Add tags to a group |
| `PATCH /api/tags/groups/{gid}/tags/{tid}` | Update a tag |
| `DELETE /api/tags/groups/{gid}/tags/{tid}` | Delete a tag |
| `POST /api/tags/test` | Test text against a tag group |

### Stream Preview

| Endpoint | Description |
|----------|-------------|
| `GET /api/stream-preview/{id}` | Preview a stream (proxy with optional transcoding) |
| `GET /api/channel-preview/{id}` | Preview a channel (proxy with optional transcoding) |

### Journal

| Endpoint | Description |
|----------|-------------|
| `GET /api/journal` | Get journal entries (paginated, filterable) |
| `GET /api/journal/stats` | Get journal statistics |
| `DELETE /api/journal/purge` | Purge old journal entries |

### Notifications

| Endpoint | Description |
|----------|-------------|
| `GET /api/notifications` | Get notifications (paginated, filterable by read status) |
| `POST /api/notifications` | Create a notification |
| `PATCH /api/notifications/{id}` | Update notification (mark as read) |
| `DELETE /api/notifications/{id}` | Delete notification |
| `PATCH /api/notifications/mark-all-read` | Mark all notifications as read |
| `DELETE /api/notifications` | Clear notifications (read only or all) |
| `DELETE /api/notifications/by-source` | Delete notifications by source |

### Alert Methods

| Endpoint | Description |
|----------|-------------|
| `GET /api/alert-methods` | List all alert methods |
| `GET /api/alert-methods/types` | Get available alert method types |
| `POST /api/alert-methods` | Create alert method |
| `GET /api/alert-methods/{id}` | Get alert method details |
| `PATCH /api/alert-methods/{id}` | Update alert method |
| `DELETE /api/alert-methods/{id}` | Delete alert method |
| `POST /api/alert-methods/{id}/test` | Send test notification |

### Scheduled Tasks

| Endpoint | Description |
|----------|-------------|
| `GET /api/tasks` | List all tasks with status |
| `GET /api/tasks/{id}` | Get task details with schedules |
| `PATCH /api/tasks/{id}` | Update task configuration |
| `POST /api/tasks/{id}/run` | Run task immediately |
| `POST /api/tasks/{id}/cancel` | Cancel running task |
| `GET /api/tasks/{id}/history` | Get task execution history |
| `GET /api/tasks/engine/status` | Get task engine status |
| `GET /api/tasks/history/all` | Get execution history for all tasks |
| `GET /api/tasks/{id}/parameter-schema` | Get parameter schema for a task type |
| `GET /api/tasks/parameter-schemas` | Get all task parameter schemas |
| `GET /api/tasks/{id}/schedules` | Get task schedules |
| `POST /api/tasks/{id}/schedules` | Add schedule to task |
| `PATCH /api/tasks/{id}/schedules/{sid}` | Update schedule |
| `DELETE /api/tasks/{id}/schedules/{sid}` | Delete schedule |

### Auto-Creation

| Endpoint | Description |
|----------|-------------|
| `GET /api/auto-creation/rules` | List all rules sorted by priority |
| `GET /api/auto-creation/rules/{id}` | Get rule details |
| `POST /api/auto-creation/rules` | Create rule |
| `PUT /api/auto-creation/rules/{id}` | Update rule |
| `DELETE /api/auto-creation/rules/{id}` | Delete rule |
| `POST /api/auto-creation/rules/reorder` | Reorder rules by priority |
| `POST /api/auto-creation/rules/{id}/toggle` | Toggle rule enabled state |
| `POST /api/auto-creation/rules/{id}/duplicate` | Duplicate a rule |
| `POST /api/auto-creation/rules/{id}/run` | Run a single rule (supports dry_run) |
| `POST /api/auto-creation/run` | Run the full pipeline (execute or dry_run) |
| `GET /api/auto-creation/executions` | Get execution history (paginated) |
| `GET /api/auto-creation/executions/{id}` | Get execution details (optional log/entities) |
| `POST /api/auto-creation/executions/{id}/rollback` | Rollback an execution |
| `POST /api/auto-creation/validate` | Validate a rule definition |
| `GET /api/auto-creation/export/yaml` | Export all rules as YAML |
| `POST /api/auto-creation/import/yaml` | Import rules from YAML |
| `GET /api/auto-creation/schema/conditions` | Get available condition types |
| `GET /api/auto-creation/schema/actions` | Get available action types |
| `GET /api/auto-creation/schema/template-variables` | Get available template variables |

### Cache

| Endpoint | Description |
|----------|-------------|
| `POST /api/cache/invalidate` | Invalidate cached data (optional prefix filter) |
| `GET /api/cache/stats` | Get cache statistics |

### TLS

| Endpoint | Description |
|----------|-------------|
| `GET /api/tls/status` | Get TLS configuration status |
| `GET /api/tls/settings` | Get TLS settings |
| `POST /api/tls/configure` | Configure TLS settings |
| `POST /api/tls/request-cert` | Request Let's Encrypt certificate (DNS-01 challenge) |
| `POST /api/tls/complete-challenge` | Complete pending DNS challenge |
| `POST /api/tls/upload-cert` | Upload custom certificate and key |
| `POST /api/tls/renew` | Manually trigger certificate renewal |
| `DELETE /api/tls/certificate` | Delete certificate and disable TLS |
| `POST /api/tls/test-dns-provider` | Test DNS provider credentials |
| `POST /api/tls/https/start` | Start HTTPS server |
| `POST /api/tls/https/stop` | Stop HTTPS server |
| `POST /api/tls/https/restart` | Restart HTTPS server |
| `GET /api/tls/https/status` | Get HTTPS server status |

### Auto-Creation
- `GET /api/auto-creation/rules` - List all rules sorted by priority
- `GET /api/auto-creation/rules/{id}` - Get rule details
- `POST /api/auto-creation/rules` - Create rule
- `PUT /api/auto-creation/rules/{id}` - Update rule
- `DELETE /api/auto-creation/rules/{id}` - Delete rule
- `POST /api/auto-creation/rules/reorder` - Reorder rules by priority
- `POST /api/auto-creation/rules/{id}/toggle` - Toggle rule enabled state
- `POST /api/auto-creation/rules/{id}/duplicate` - Duplicate a rule
- `POST /api/auto-creation/rules/{id}/run` - Run a single rule (supports dry_run query param)
- `POST /api/auto-creation/run` - Run the full pipeline (execute or dry_run mode)
- `GET /api/auto-creation/executions` - Get execution history (paginated)
- `GET /api/auto-creation/executions/{id}` - Get execution details (with optional log and entities)
- `POST /api/auto-creation/executions/{id}/rollback` - Rollback an execution
- `POST /api/auto-creation/validate` - Validate a rule definition
- `GET /api/auto-creation/export/yaml` - Export all rules as YAML
- `POST /api/auto-creation/import/yaml` - Import rules from YAML
- `GET /api/auto-creation/schema/conditions` - Get available condition types
- `GET /api/auto-creation/schema/actions` - Get available action types
- `GET /api/auto-creation/schema/template-variables` - Get available template variables

### Authentication

| Endpoint | Description |
|----------|-------------|
| `GET /api/auth/status` | Get authentication status and configuration |
| `GET /api/auth/setup-required` | Check if first-run setup is needed |
| `POST /api/auth/setup` | Complete first-run setup (create admin account) |
| `POST /api/auth/login` | Login with username/password |
| `POST /api/auth/logout` | Logout and clear session |
| `POST /api/auth/refresh` | Refresh access token |
| `GET /api/auth/me` | Get current user info |
| `PUT /api/auth/me` | Update current user profile |
| `POST /api/auth/change-password` | Change current user's password |
| `POST /api/auth/forgot-password` | Request password reset email |
| `POST /api/auth/reset-password` | Reset password with token |
| `GET /api/auth/providers` | List available auth providers |
| `POST /api/auth/dispatcharr/login` | Login via Dispatcharr credentials |

### User Management (Admin)

| Endpoint | Description |
|----------|-------------|
| `GET /api/admin/users` | List all users (paginated, searchable) |
| `POST /api/admin/users` | Create new user |
| `GET /api/admin/users/{id}` | Get user details |
| `PATCH /api/admin/users/{id}` | Update user |
| `DELETE /api/admin/users/{id}` | Delete (deactivate) user |

### Health

| Endpoint | Description |
|----------|-------------|
| `GET /api/health` | Health check |
| `GET /api/debug/request-rates` | Request rate statistics (diagnostics) |

## Utility Scripts

### Password Reset Script

The `reset_password.py` utility allows you to reset user passwords from the command line when locked out or when SMTP is not configured:

```bash
# Interactive mode — lists users, prompts for everything
docker exec -it enhancedchannelmanager python /app/reset_password.py

# Non-interactive — specify username and password
docker exec enhancedchannelmanager python /app/reset_password.py -u admin -p 'NewPass123'

# Semi-interactive — specify username, prompt for password securely
docker exec -it enhancedchannelmanager python /app/reset_password.py -u admin

# Skip password strength validation
docker exec enhancedchannelmanager python /app/reset_password.py -u admin -p 'simple' --force
```

**Features:**
- Interactive mode shows a table of all users with username, email, admin status, active status, and auth provider
- Password strength validation (8+ chars, upper/lower/digit) with `--force` to bypass
- Secure password prompts (not echoed to terminal)
- Warns when resetting password for non-local auth users
- Uses bcrypt with 12 rounds, matching the web UI authentication system

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
