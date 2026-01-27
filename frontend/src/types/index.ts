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
  channel_uuid?: string | null;  // Used by dummy EPG sources to match via channel UUID
  // Dispatcharr may also return these alternate field names
  start?: string;  // Alternate for start_time
  stop?: string;   // Alternate for end_time
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
  is_auto_sync?: boolean;
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
  custom_properties?: Record<string, unknown> | null;  // Extra M3U attributes like tvc-guide-stationid
}

// Stream group with count (returned by /api/stream-groups)
export interface StreamGroupInfo {
  name: string;
  count: number;
}

// Stream probe statistics - metadata gathered via ffprobe
export interface StreamStats {
  stream_id: number;
  stream_name: string | null;
  resolution: string | null;       // e.g., "1920x1080"
  fps: string | null;              // e.g., "29.97"
  video_codec: string | null;      // e.g., "h264", "hevc"
  audio_codec: string | null;      // e.g., "aac", "ac3"
  audio_channels: number | null;   // e.g., 2, 6
  stream_type: string | null;      // e.g., "HLS", "MPEG-TS"
  bitrate: number | null;          // bits per second (overall stream)
  video_bitrate: number | null;    // bits per second (video stream only)
  probe_status: 'success' | 'failed' | 'pending' | 'timeout';
  error_message: string | null;
  last_probed: string | null;      // ISO timestamp
  created_at: string;
}

export interface StreamStatsSummary {
  total: number;
  success: number;
  failed: number;
  timeout: number;
  pending: number;
}

export interface BulkProbeResult {
  probed: number;
  results: StreamStats[];
}

// M3U Account types
export type M3UAccountType = 'STD' | 'XC';
export type M3UAccountStatus = 'idle' | 'fetching' | 'parsing' | 'error' | 'success' | 'pending_setup' | 'disabled';

export interface M3UAccountProfile {
  id: number;
  name: string;
  max_streams: number;
  is_active: boolean;
  is_default?: boolean;
  search_pattern?: string;
  replace_pattern?: string;
  expire_date: string | null;
  status: string;
  custom_properties?: Record<string, unknown> | null;
}

// Auto-sync custom properties for channel groups
// Field names must match Dispatcharr's expected fields in custom_properties
export interface AutoSyncCustomProperties {
  custom_epg_id?: string | null;            // Force EPG Source ID (Dispatcharr field name)
  group_override?: number | null;           // Override Channel Group ID
  name_regex_pattern?: string;              // Find pattern (regex)
  name_replace_pattern?: string;            // Replace pattern
  name_match_regex?: string;                // Channel name filter regex (Dispatcharr field name)
  channel_profile_ids?: string[];           // Channel Profile IDs (strings for API compatibility)
  channel_sort_order?: 'provider' | 'name' | 'tvg_id' | 'updated_at' | null; // Sort field
  channel_sort_reverse?: boolean;           // Reverse sort order
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

// Response from /api/stats/activity (proxied from /api/core/system-events/)
export interface SystemEventsResponse {
  events: SystemEvent[];
  count: number;
  total: number;
  offset: number;
  limit: number;
}

// Daily bandwidth record
export interface BandwidthDailyRecord {
  date: string;
  bytes_transferred: number;
  peak_channels: number;
  peak_clients: number;
}

// Response from /api/stats/bandwidth
export interface BandwidthSummary {
  today: number;
  this_week: number;
  this_month: number;
  this_year: number;
  all_time: number;
  daily_history: BandwidthDailyRecord[];
}

// Channel watch statistics
export interface ChannelWatchStats {
  channel_id: number | string;  // Can be UUID string from Dispatcharr
  channel_name: string;
  watch_count: number;
  total_watch_seconds: number;
  last_watched: string | null;
}

// Sort mode for top watched channels
export type TopWatchedSortBy = 'views' | 'time';

// =============================================================================
// Normalization Engine Types
// =============================================================================

// Condition types for normalization rules
export type NormalizationConditionType = 'always' | 'contains' | 'starts_with' | 'ends_with' | 'regex';

// Action types for normalization rules
export type NormalizationActionType = 'remove' | 'replace' | 'regex_replace' | 'strip_prefix' | 'strip_suffix' | 'normalize_prefix';

// Logic for combining multiple conditions
export type NormalizationConditionLogic = 'AND' | 'OR';

// A single condition in a compound condition rule
export interface NormalizationCondition {
  type: NormalizationConditionType;
  value: string;
  negate?: boolean;        // NOT logic - match when condition does NOT match
  case_sensitive?: boolean;
}

// A single normalization rule
export interface NormalizationRule {
  id: number;
  group_id: number;
  name: string;
  description: string | null;
  enabled: boolean;
  priority: number;
  // Legacy single condition fields (still supported)
  condition_type: NormalizationConditionType;
  condition_value: string | null;
  case_sensitive: boolean;
  // Compound conditions (takes precedence if set)
  conditions: NormalizationCondition[] | null;
  condition_logic: NormalizationConditionLogic;
  // Action fields
  action_type: NormalizationActionType;
  action_value: string | null;
  stop_processing: boolean;
  is_builtin: boolean;
  created_at: string;
  updated_at: string;
}

// A group of normalization rules
export interface NormalizationRuleGroup {
  id: number;
  name: string;
  description: string | null;
  enabled: boolean;
  priority: number;
  is_builtin: boolean;
  created_at: string;
  updated_at: string;
  rules?: NormalizationRule[];
}

// Request to create a rule group
export interface CreateRuleGroupRequest {
  name: string;
  description?: string;
  enabled?: boolean;
  priority?: number;
}

// Request to update a rule group
export interface UpdateRuleGroupRequest {
  name?: string;
  description?: string;
  enabled?: boolean;
  priority?: number;
}

// Request to create a rule
export interface CreateRuleRequest {
  group_id: number;
  name: string;
  description?: string;
  enabled?: boolean;
  priority?: number;
  // Legacy single condition (use this OR compound conditions)
  condition_type: NormalizationConditionType;
  condition_value?: string;
  case_sensitive?: boolean;
  // Compound conditions (takes precedence if set)
  conditions?: NormalizationCondition[];
  condition_logic?: NormalizationConditionLogic;
  // Action fields
  action_type: NormalizationActionType;
  action_value?: string;
  stop_processing?: boolean;
}

// Request to update a rule
export interface UpdateRuleRequest {
  name?: string;
  description?: string;
  enabled?: boolean;
  priority?: number;
  // Legacy single condition
  condition_type?: NormalizationConditionType;
  condition_value?: string;
  case_sensitive?: boolean;
  // Compound conditions
  conditions?: NormalizationCondition[] | null;  // null to clear compound conditions
  condition_logic?: NormalizationConditionLogic;
  // Action fields
  action_type?: NormalizationActionType;
  action_value?: string;
  stop_processing?: boolean;
}

// Request to test a single rule
export interface TestRuleRequest {
  text: string;
  // Legacy single condition (use this OR compound conditions)
  condition_type: NormalizationConditionType;
  condition_value: string;
  case_sensitive: boolean;
  // Compound conditions (takes precedence if set)
  conditions?: NormalizationCondition[];
  condition_logic?: NormalizationConditionLogic;
  // Action fields
  action_type: NormalizationActionType;
  action_value?: string;
}

// Result of testing a single rule
export interface TestRuleResult {
  matched: boolean;
  before: string;
  after: string;
  match_start: number | null;
  match_end: number | null;
}

// Transformation detail in batch test result
export interface NormalizationTransformation {
  rule_id: number;
  before: string;
  after: string;
}

// Result of normalizing a single text through all rules
export interface NormalizationResult {
  original: string;
  normalized: string;
  rules_applied?: number[];
  transformations?: NormalizationTransformation[];
}

// Response from batch normalization
export interface NormalizationBatchResponse {
  results: NormalizationResult[];
}

// Migration status response
export interface NormalizationMigrationStatus {
  builtin_groups: number;
  custom_groups: number;
  builtin_rules: number;
  custom_rules: number;
  total_groups: number;
  total_rules: number;
  migration_complete: boolean;
}

// Migration run response
export interface NormalizationMigrationResult {
  groups_created: number;
  rules_created: number;
  skipped: boolean;
}
