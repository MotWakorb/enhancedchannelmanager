# Changelog

All notable changes to Enhanced Channel Manager will be documented in this file.

## [0.3.1] - 2026-01-02

### Highlights

**Multi-Group Bulk Channel Creation** - The standout feature of this release! You can now select multiple stream groups and create channels from all of them at once. Choose to create separate channel groups (with independent naming and starting numbers for each) or combine everything into a single group.

### New Features

#### Multi-Group Bulk Channel Creation
- Select and drag multiple stream groups to create channels from all at once
- **Separate Groups Mode**: Create a channel group for each stream group with independent settings
  - Per-group custom naming
  - Per-group starting channel numbers
  - Automatic continuation from previous group's last channel
- **Combined Mode**: Merge all streams into a single channel group
- Visual preview showing first 3 channels per group with calculated numbers

#### Auto-Assign Logos
- Channels created from streams now automatically inherit the stream's logo
- Works for both single stream drops and bulk channel creation
- Logo matching uses stream's logo URL against existing logos in the system

#### Staged Group Creation
- New channel groups are now staged along with their channels
- Groups are only created when committing changes (not immediately)
- Prevents orphaned groups if you discard your changes

#### Bulk Delete with Groups
- When deleting all channels in a group, option to also delete the now-empty group
- Checkbox appears: "Also delete X empty group(s)"
- Both channel and group deletions shown in the exit dialog

### Bug Fixes

#### EPG Matching Improvements
- **Balanced HD Preference**: HD entries are preferred, but not when a non-HD variant has a significantly better call sign match
- **Call Sign Scoring**: Improved algorithm rates matches (exact > starts with > common prefix > none)
- **Regional Variant Detection**: Now catches WestCoastFeed, EasternFeed, WesternFeed and other compound variants
- Fixed matching for channels like FYI, GET TV, Lifetime Movie, MGM+, Ovation, Pursuit, Reelz, TCM, TV Land

#### Channel Renaming
- Fixed auto-rename not applying when moving channels between other channels
- Added support for channel names with numbers in the middle (e.g., "US | 5034 - DABL")
- Added colon as valid channel number separator in renumbering patterns

#### Multi-Group Modal UI
- Fixed layout issues with per-group starting number input
- Fixed "Create Channels" button being incorrectly disabled
- Added per-group channel preview (was previously hidden)

### Technical Changes

- Version bump from 0.3.0 series to 0.3.1
- Improved edit mode state management for staged groups
- Added newGroupName and logoUrl to createChannel API call spec

---

## [0.3.0] - 2026-01-01

### New Features

- Bulk channel creation from stream groups
- Stream name normalization (quality variants, country prefixes, network prefixes)
- Smart stream ordering by quality and provider for failover
- Bulk EPG assignment with intelligent matching
- EPG conflict resolution UI with card-based navigation
- Group header checkbox to select all channels in group
- Accept All Recommended button for bulk EPG
- High contrast and light themes

### Improvements

- Theme support with CSS variables
- Stream caching for faster loading
- Hide auto-sync groups setting
- Channel defaults settings section

---

## [0.2.0] - Initial Release

### Features

- Channel management with drag-and-drop
- Stream management with multi-select
- Edit mode with staged changes
- EPG management with drag-and-drop priority
- Logo management with upload support
- Settings with Dispatcharr connection configuration
