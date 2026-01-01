# Enhanced Channel Manager

A professional-grade web interface for managing TV channels, streams, and metadata for IPTV setups. Built with React + TypeScript frontend and Python FastAPI backend, designed to work with Dispatcharr.

## Features

### Channel Management

- **Create, Edit, Delete Channels** - Full CRUD operations for channel management
- **Channel Numbering** - Assign channel numbers (including decimal numbers like 4.1)
- **Auto-Rename** - Optionally update channel names when numbers change (e.g., "101 | ESPN" becomes "102 | ESPN")
- **Channel Groups** - Organize channels into groups for better organization
- **Delete Groups** - Delete channel groups with option to also delete contained channels
- **Bulk Delete** - Select multiple channels and delete them at once
- **Search & Filter** - Search channels by name, filter by one or more groups
- **Inline Stream Display** - View assigned streams directly in the channel list

### Stream Management

- **View Available Streams** - Browse all streams from your M3U providers
- **Assign Streams to Channels** - Drag-and-drop or click to add streams
- **Stream Priority** - Reorder streams within a channel (order determines playback priority)
- **Multi-Select** - Select multiple streams and bulk-add to channels
- **Filter by Provider** - Filter streams by M3U account
- **Filter by Group** - Filter streams by their source group

### Bulk Channel Creation

- **Create from Stream Group** - Create multiple channels from an entire M3U stream group at once
- **Auto-Assign Streams** - Each created channel automatically gets its source stream assigned
- **Merge Duplicate Names** - Streams with identical names from different M3U providers are merged into a single channel with all streams assigned (provides multi-provider redundancy)
- **Quality Variant Normalization** - Streams with quality suffixes (FHD, UHD, HD, SD, 4K, 1080P, etc.) are automatically merged into one channel
- **East/West Timezone Preference** - When streams have regional variants (e.g., "HBO" and "HBO West"), choose to create East feeds only, West feeds only, or keep both as separate channels
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
- **Smart Name Sorting** - Option to ignore channel numbers in names when sorting (e.g., "101 | ESPN" sorts as "ESPN")
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
      - "8080:8000"
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
