export interface Channel {
  id: number;
  channel_number: number | null;
  name: string;
  channel_group_id: number | null;
  tvg_id: string | null;
  tvc_guide_stationid: string | null;
  epg_data_id: number | null;
  streams: number[];
  stream_profile_id: number | null;
  uuid: string;
  logo_id: number | null;
  auto_created: boolean;
  auto_created_by: number | null;
  auto_created_by_name: string | null;
  // Client-side only: temporary logo URL for staged channels before commit
  _stagedLogoUrl?: string;
}

export type EPGSourceType = 'xmltv' | 'schedules_direct' | 'dummy';
export type EPGSourceStatus = 'idle' | 'fetching' | 'parsing' | 'error' | 'success' | 'disabled';

// Custom properties for Dummy EPG sources
export interface DummyEPGCustomProperties {
  // Pattern Configuration
  name_source?: 'channel' | 'stream';       // What to parse (channel name or stream name)
  stream_index?: number;                     // Which stream (1-based) if name_source is 'stream'
  title_pattern?: string;                    // Regex with named groups to extract info
  time_pattern?: string;                     // Optional time extraction regex
  date_pattern?: string;                     // Optional date extraction regex

  // Output Templates
  title_template?: string;                   // Format EPG title using extracted groups
  description_template?: string;             // Format EPG description

  // Upcoming/Ended Templates
  upcoming_title_template?: string;          // Title for programs before event starts
  upcoming_description_template?: string;    // Description before event
  ended_title_template?: string;             // Title for programs after event ends
  ended_description_template?: string;       // Description after event

  // Fallback Templates (when patterns don't match)
  fallback_title_template?: string;
  fallback_description_template?: string;

  // EPG Settings
  event_timezone?: string;                   // Timezone of event times (e.g., "US/Eastern")
  output_timezone?: string;                  // Optional different display timezone
  program_duration?: number;                 // Minutes (default 180)
  categories?: string;                       // Comma-separated categories
  channel_logo_url?: string;                 // URL template with placeholders
  program_poster_url?: string;               // URL template for program icons
  include_date_tag?: boolean;                // Add <date> tag to EPG output
  include_live_tag?: boolean;                // Mark programs as live content
  include_new_tag?: boolean;                 // Mark programs as new content
}

export interface EPGSource {
  id: number;
  name: string;
  source_type: EPGSourceType;
  url: string | null;
  api_key: string | null;
  is_active: boolean;
  file_path: string | null;
  refresh_interval: number;
  priority: number;
  status: EPGSourceStatus;
  last_message: string | null;
  created_at: string;
  updated_at: string | null;
  custom_properties: DummyEPGCustomProperties | Record<string, unknown> | null;
  epg_data_count: string;
}

export interface EPGData {
  id: number;
  tvg_id: string;
  name: string;
  icon_url: string | null;
  epg_source: number;
}

export interface EPGProgram {
  id: number;
  start_time: string;
  end_time: string;
  title: string;
  sub_title?: string | null;
  description?: string | null;
  tvg_id?: string | null;
}

export interface StreamProfile {
  id: number;
  name: string;
  command: string;
  parameters: string;
  is_active: boolean;
  locked: boolean;
}

// Channel Profile - for creating separate M3U playlists per user
export interface ChannelProfile {
  id: number;
  name: string;
  channels: number[]; // channel IDs enabled for this profile (read-only from API)
}

export interface ChannelGroup {
  id: number;
  name: string;
  channel_count: number;
}

export interface Stream {
  id: number;
  name: string;
  url: string | null;
  m3u_account: number | null;
  logo_url: string | null;
  tvg_id: string | null;
  channel_group: number | null;
  channel_group_name: string | null;
  is_custom: boolean;
}

// M3U Account types
export type M3UAccountType = 'STD' | 'XC';
export type M3UAccountStatus = 'idle' | 'fetching' | 'parsing' | 'error' | 'success' | 'pending_setup' | 'disabled';

export interface M3UAccountProfile {
  id: number;
  name: string;
  max_streams: number;
  is_active: boolean;
  expire_date: string | null;
  status: string;
}

// Auto-sync custom properties for channel groups
export interface AutoSyncCustomProperties {
  xc_id?: string | null;                    // Force EPG Source ID (string for API compatibility)
  group_override?: number | null;           // Override Channel Group ID
  name_regex_pattern?: string;              // Find pattern (regex)
  name_replace_pattern?: string;            // Replace pattern
  channel_name_filter?: string;             // Channel name filter (regex)
  channel_profile_ids?: string[];           // Channel Profile IDs (strings for API compatibility)
  channel_sort_order?: 'provider' | 'name' | 'tvg_id' | 'updated_at' | null; // Sort field
  channel_sort_reverse?: boolean; // Reverse sort order
  stream_profile_id?: number | null;        // Stream Profile ID
  custom_logo_id?: number | null;           // Custom Logo ID
}

export interface ChannelGroupM3UAccount {
  id: number;
  channel_group: number;
  channel_group_name: string;
  enabled: boolean;
  enabled_vod: boolean;
  enabled_series: boolean;
  auto_channel_sync: boolean;
  auto_sync_channel_start: number | null;
  custom_properties: AutoSyncCustomProperties | null;
}

export interface M3UAccount {
  id: number;
  name: string;
  server_url: string | null;
  file_path: string | null;
  server_group: number | null;
  max_streams: number;
  is_active: boolean;
  created_at: string;
  updated_at: string | null;
  user_agent: number | null;
  profiles: M3UAccountProfile[];
  locked: boolean;
  channel_groups: ChannelGroupM3UAccount[];
  refresh_interval: number;
  custom_properties: Record<string, unknown> | null;
  account_type: M3UAccountType;
  username: string | null;
  password: string | null;
  stale_stream_days: number;
  priority: number;
  status: M3UAccountStatus;
  last_message: string | null;
  enable_vod: boolean;
  auto_enable_new_groups_live: boolean;
  auto_enable_new_groups_vod: boolean;
  auto_enable_new_groups_series: boolean;
}

export interface M3UAccountCreateRequest {
  name: string;
  server_url?: string | null;
  file_path?: string | null;
  server_group?: number | null;
  max_streams?: number;
  is_active?: boolean;
  refresh_interval?: number;
  account_type: M3UAccountType;
  username?: string | null;
  password?: string | null;
  stale_stream_days?: number;
  enable_vod?: boolean;
  auto_enable_new_groups_live?: boolean;
  auto_enable_new_groups_vod?: boolean;
  auto_enable_new_groups_series?: boolean;
}

export interface M3UFilter {
  id: number;
  m3u_account: number;
  filter_type: 'group' | 'name' | 'url';
  regex_pattern: string;
  exclude: boolean;
  order: number;
}

export interface M3UFilterCreateRequest {
  filter_type: 'group' | 'name' | 'url';
  regex_pattern: string;
  exclude: boolean;
  order?: number;
}

export interface ServerGroup {
  id: number;
  name: string;
}

export interface M3UGroupSetting {
  channel_group: number;
  enabled: boolean;
  auto_channel_sync: boolean;
  auto_sync_channel_start: number | null;
  m3u_account_id: number;
  m3u_account_name: string;
  custom_properties?: {
    group_override?: number;
    [key: string]: unknown;
  };
}

export interface ChannelListFilterSettings {
  showEmptyGroups: boolean;
  showNewlyCreatedGroups: boolean;
  showProviderGroups: boolean;
  showManualGroups: boolean;
  showAutoChannelGroups: boolean;
}

export interface Logo {
  id: number;
  name: string;
  url: string;
  cache_url: string;
  channel_count: number;
  is_used: boolean;
}

export interface PaginatedResponse<T> {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
}

// Re-export history types
export * from './history';

// Re-export edit mode types
export * from './editMode';

// Re-export journal types
export * from './journal';

// =============================================================================
// Stats & Monitoring Types
// =============================================================================

// Client connection info for an active stream
export interface StreamClient {
  client_id: string;
  ip_address: string;
  user_agent: string;
  connected_at: string;
  last_active: string;
  connection_duration?: string;
  bytes_sent?: number;
  avg_rate_KBps?: number;
  current_rate_KBps?: number;
}

// Active channel stats from /proxy/ts/status
// Note: Fields match what Dispatcharr actually returns
export interface ChannelStats {
  channel_id: number | string;  // UUID string from Dispatcharr
  channel_name?: string;
  channel_number?: number;

  // State & timing
  state?: string;
  uptime?: string | number;  // Can be number (seconds) or string
  started_at?: string;
  state_duration?: string;

  // Clients
  client_count: number;
  clients?: StreamClient[];

  // Bitrate & bandwidth (Dispatcharr provides avg_bitrate and avg_bitrate_kbps)
  avg_bitrate?: string;         // e.g., "4.40 Mbps"
  avg_bitrate_kbps?: number;    // e.g., 4403.08

  // Speed & performance
  ffmpeg_speed?: number | string;  // Can be number (1.02) or string ("1.02x")
  ffmpeg_fps?: number;
  actual_fps?: number;
  source_fps?: number;  // This is what Dispatcharr returns

  // Buffer & data
  buffer_index?: number;
  total_bytes?: number;
  total_data?: string;

  // Stream quality
  video_codec?: string;
  audio_codec?: string;
  resolution?: string;
  audio_channels?: string | number;  // Can be "stereo", "5.1", or number
  stream_type?: string;  // e.g., "mpegts"

  // Stream source info (from Dispatcharr)
  stream_id?: number;
  stream_name?: string;
  m3u_profile_id?: number;
  m3u_profile_name?: string;
  stream_profile?: string;  // Stream profile ID as string
  url?: string;
}

// Response from /proxy/ts/status
export interface ChannelStatsResponse {
  channels: ChannelStats[];
  count: number;
}

// System event types
export type SystemEventType =
  | 'channel_start'
  | 'channel_stop'
  | 'client_connect'
  | 'client_disconnect'
  | 'buffering'
  | 'stream_switch'
  | 'error';

// System event from /api/core/system-events/
export interface SystemEvent {
  id: number;
  event_type: SystemEventType | string;
  channel_id?: number;
  channel_name?: string;
  client_id?: string;
  ip_address?: string;
  message?: string;
  details?: Record<string, unknown>;
  timestamp: string;
  created_at: string;
}

// Response from /api/core/system-events/
export interface SystemEventsResponse {
  results: SystemEvent[];
  count: number;
  next?: string | null;
  previous?: string | null;
}
