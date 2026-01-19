# Release Notes: v0.7.3

## Major Features

### Stream Probing Enhancements
- **Auto-Reorder After Probe** - Scheduled probes now automatically reorder streams by quality and status when enabled in settings
- **Persistent Probe History** - Probe results now save to `/config/probe_history.json` and persist across container restarts
- **Scheduled Probe Progress** - UI now auto-detects and displays progress for scheduled probes (not just manual probes)
- **Improved HDHomeRun Support** - Tuned parallelism and added delays for more reliable HDHomeRun stream probing
- **VLC User-Agent** - Added VLC user agent to probe requests for better stream compatibility
- **Failed Streams Modal** - View detailed error messages for failed streams in probe results
- **Force Reset Endpoint** - Added endpoint to recover from stuck probe states

### Delete Orphaned Channel Groups
- **Orphaned Group Detection** - New feature to identify and delete channel groups that have no streams, no channels, and no M3U association
- **Selective Deletion** - Option to delete all orphaned groups or select specific ones
- **API Endpoints** - New `GET /api/channel-groups/orphaned` and `DELETE /api/channel-groups/orphaned` endpoints
- **Settings UI** - Added "Delete Orphaned Groups" button in Settings → Maintenance section

### Utility Scripts
- **Search Stream Script** - New `scripts/search-stream.sh` command-line utility for searching streams in Dispatcharr
  - Handles authentication automatically
  - URL-encodes special characters and spaces
  - Returns pretty-printed JSON results
  - Supports jq filtering for extracting specific fields

## Improvements

### EPG & Guide
- **EPG Grid Timeout Fix** - Fixed timeout issues when loading guide with large channel counts by using correct Dispatcharr endpoint
- **EPG Program Matching** - Improved EPG program matching via `epg_data_id` indirection

### Stream Auto-Reorder
- **Configured Sort Settings** - Auto-reorder now uses configured sort priority settings instead of hardcoded logic
- **Sort Config Display** - Reorder results modal now shows which sort configuration was used

### M3U Manager
- **Auto-Detect Refresh Status** - M3U Manager now auto-detects when M3U accounts are being refreshed and shows status

### Settings & Configuration
- **Settings Persistence Fix** - Fixed missing fields in settings model that prevented some settings from persisting
- **Timezone Clarification** - Added clarification that timezone setting affects both stats collection and scheduler
- **Restart Notifications** - Added notification when probe schedule changes require container restart

### Probing System
- **Unified Probe Endpoint** - Consolidated probe operations to use `/probe/all` endpoint for consistency
- **Channel Group Filtering** - Added debug logging to show which channels are included/excluded by group filters
- **Scheduler Robustness** - Fixed scheduler stopping after probe cancellation
- **Probe Error Messages** - Improved ffprobe error messages for better debugging

### Docker & Deployment
- **Docker Cache Invalidation** - Fixed critical Docker layer caching issue where code updates weren't being deployed
  - Added GIT_COMMIT build arg to both frontend and backend stages
  - Ensures fresh builds on every git commit change

## Bug Fixes

### Critical Fixes
- **FastAPI Route Ordering** - Fixed 422 error on delete orphaned groups endpoint caused by incorrect route ordering
- **Docker Cache Bug** - Fixed GitHub Actions Docker caching preventing backend code updates from deploying
- **Scheduler Auto-Reorder** - Fixed scheduled probe not calling auto-reorder even when enabled
- **Settings Persistence** - Fixed multiple settings not saving correctly due to missing model fields

### Minor Fixes
- **Pydantic Validation** - Fixed optional request parameter type hints for proper FastAPI validation
- **Frontend Request Body** - Fixed frontend sending incorrect request body format for delete operations
- **Copy Button** - Fixed copy button in failed streams modal
- **Debug Logging** - Moved verbose PROBE-FILTER logs from INFO to DEBUG level

## API Changes

### New Endpoints
- `GET /api/channel-groups/orphaned` - List orphaned channel groups
- `DELETE /api/channel-groups/orphaned` - Delete orphaned groups (with optional filtering by group IDs)
- `POST /api/stream-stats/probe/reset` - Force reset stuck probe state

### Enhanced Endpoints
- Stream probing endpoints now support auto-reorder integration
- Probe endpoints return more detailed error information

## Documentation

### README Updates
- Added Auto-Reorder After Probe documentation
- Added Persistent Probe History documentation
- Added Delete Orphaned Groups feature
- Added Utility Scripts section with search-stream.sh documentation
- Updated API endpoints with orphaned groups endpoints

## Technical Details

### Backend
- Python FastAPI route ordering fix for path parameters
- Enhanced Pydantic models with proper optional type hints
- Improved request validation error logging
- Persistent probe history storage to config volume

### Frontend
- TypeScript compilation v0.7.3-0145
- Auto-detection of scheduled probe progress
- Enhanced error message display in modals

### DevOps
- GitHub Actions cache invalidation improvements
- Multi-stage Docker build optimization
- GIT_COMMIT environment variable for cache busting

## Upgrade Notes

### Breaking Changes
None

### Recommended Actions
1. Review and configure auto-reorder settings if using scheduled probes
2. Check for orphaned groups and clean up using new deletion feature
3. Update any automation scripts to use new orphaned groups API if needed

### Config Volume
The `/config` directory now contains:
- `settings.json` - Application settings (existing)
- `probe_history.json` - Persistent probe results (new)

## Contributors

This release includes contributions and collaboration with Claude Sonnet 4.5.

---

**Full Changelog**: v0.7.2...v0.7.3
