# Enhanced Channel Manager

A professional-grade web interface for managing IPTV configurations with Dispatcharr. Features a tab-based interface for channel management, EPG data, logos, and more. Built with React + TypeScript frontend and Python FastAPI backend.

## Application Tabs

- **M3U Manager** - M3U playlist management (coming soon)
- **EPG Manager** - Manage EPG sources with drag-and-drop priority ordering
- **Channel Manager** - Full-featured channel and stream management with split-pane layout
- **Logo Manager** - Logo management (coming soon)
- **Settings** - Configure Dispatcharr connection and channel defaults with sidebar navigation

## Features

### Channel Management

- **Create, Edit, Delete Channels** - Full CRUD operations for channel management
- **Channel Numbering** - Assign channel numbers (including decimal numbers like 4.1)
- **Auto-Rename** - Optionally update channel names when numbers change (e.g., "101 | Sports Channel" becomes "102 | Sports Channel")
- **Channel Groups** - Organize channels into groups for better organization
- **Delete Groups** - Delete channel groups with option to also delete contained channels
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
- **Drag-and-Drop Priority** - Reorder EPG sources to set matching priority (higher priority sources take precedence)
- **Source Status** - View status of each source (success, error, fetching, parsing)
- **Refresh Sources** - Manually refresh individual sources or all at once
- **Enable/Disable** - Toggle sources active/inactive without deleting
- **Channel Count** - See how many EPG channels each source provides
- **Last Updated** - Track when each source was last refreshed

### Bulk Channel Creation

- **Create from Stream Group** - Create multiple channels from an entire M3U stream group at once
- **Auto-Assign Streams** - Each created channel automatically gets its source stream assigned
- **Merge Duplicate Names** - Streams with identical names from different M3U providers are merged into a single channel with all streams assigned (provides multi-provider redundancy)
- **Quality Variant Normalization** - Streams with quality suffixes (FHD, UHD, HD, SD, 4K, 1080P, etc.) are automatically merged into one channel
- **Smart Stream Ordering** - Streams are automatically ordered by quality (UHD/4K → FHD/1080p → HD/720p → SD) and interleaved by provider for failover redundancy (e.g., Provider1-FHD, Provider2-FHD, Provider1-HD, Provider2-HD)
- **Network Prefix Stripping** - Option to strip network prefixes (e.g., "CHAMP | Queens Park Rangers" → "Queens Park Rangers") to merge streams from different networks into the same channel
- **East/West Timezone Preference** - When streams have regional variants (e.g., "Movies Channel" and "Movies Channel West"), choose to create East feeds only, West feeds only, or keep both as separate channels
- **Country Prefix Options** - Choose to remove country prefixes (e.g., "US: Sports Channel" → "Sports Channel") or keep them with normalized formatting (e.g., "US: Sports Channel" → "US | Sports Channel") with configurable separator
- **Channel Number Prefix** - Option to prepend channel numbers to names with configurable separator (-, :, or |), e.g., "100 | Sports Channel"
- **Prefix Order Choice** - When both country and channel number are enabled, choose the order: "Number first" (100 | US | Sports Channel) or "Country first" (US | 100 | Sports Channel)
- **Custom Starting Number** - Choose the starting channel number for sequential assignment
- **Flexible Group Options** - Create in same-named group, select existing group, or create new group
- **Preview Before Creating** - See a preview of channels that will be created with merged stream counts
- **Edit Mode Integration** - Requires edit mode, button visible on stream group headers on hover

### Edit Mode (Staged Editing)

A unique workflow that lets you stage changes locally before committing to the server:

- **Enter Edit Mode** - Start a new editing session
- **Stage Changes** - All edits are queued locally (including deletes)
- **Preview Changes** - See pending operations count
- **Local Undo/Redo** - Undo/redo within your edit session (Ctrl+Z / Ctrl+Shift+Z)
- **Undoable Deletes** - Channel and group deletions can be undone before committing
- **Batch Operations** - Group related changes together
- **Commit or Discard** - Apply all changes at once or discard everything
- **Exit Dialog** - Review a summary of all changes before committing
- **Modified Indicators** - Visual highlighting of channels with pending changes

### Drag and Drop

- **Add Streams** - Drag streams onto channels to add them
- **Bulk Add** - Drag multiple selected streams at once
- **Reorder Channels** - Drag channels to reorder within a group (edit mode)
- **Move Between Groups** - Drag channels to different groups with confirmation modal
- **Reorder Streams** - Drag to reorder streams within a channel
- **Visual Feedback** - Drop indicators and highlighting

### Multi-Select Channels

- **Ctrl/Cmd + Click** - Toggle individual channel selection
- **Shift + Click** - Select range of channels
- **Visual Indicators** - Selection checkboxes and highlighting
- **Bulk Operations** - Move or modify multiple channels at once

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
  - **HD Variant Preference** - Automatically prefers HD EPG entries over SD when available
  - **Regional Variant Handling** - Intelligent matching for regional variants (East, West, Pacific, etc.) with East as default when no region specified
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

#### Channel Defaults
Default options applied when using bulk channel creation:

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

### Channel List Filters

Fine-tune which groups appear in the channel list:

- **Show/Hide Empty Groups** - Toggle visibility of groups with no channels
- **Show/Hide Newly Created Groups** - Toggle visibility of groups created this session
- **Show/Hide Provider Groups** - Toggle visibility of auto-populated provider groups
- **Show/Hide Manual Groups** - Toggle visibility of manually created groups
- **Show/Hide Auto-Sync Groups** - Toggle visibility of auto-channel sync groups
- **Persistent Settings** - Filter preferences saved to localStorage

### Provider Management

- **View Providers** - See all configured M3U accounts
- **Provider Groups** - View stream groups from each provider
- **Auto-Channel Sync** - Configure automatic channel synchronization
- **Group Settings** - Per-group provider configuration

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
- `POST /api/channel-groups` - Create group
- `PATCH /api/channel-groups/{id}` - Update group
- `DELETE /api/channel-groups/{id}` - Delete group

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
- `GET /api/epg/data` - Search EPG data (paginated)

### Stream Profiles
- `GET /api/stream-profiles` - List available profiles

### Providers
- `GET /api/providers` - List M3U accounts
- `GET /api/providers/group-settings` - Get provider group settings

### Settings
- `GET /api/settings` - Get current settings
- `POST /api/settings` - Update settings
- `POST /api/settings/test` - Test connection

### Health
- `GET /api/health` - Health check

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
