export type SelectionMode = 'all' | 'groups' | 'channels';
export type StreamUrlMode = 'direct' | 'proxy';
export type SortOrder = 'name' | 'number' | 'group';
export type ProviderType = 's3' | 'gdrive' | 'onedrive' | 'dropbox';
export type ScheduleType = 'manual' | 'cron' | 'event';
export type PublishStatus = 'running' | 'success' | 'failed';

export interface PlaylistProfile {
  id: number;
  name: string;
  description: string | null;
  selection_mode: SelectionMode;
  selected_groups: number[];
  selected_channels: number[];
  stream_url_mode: StreamUrlMode;
  include_logos: boolean;
  include_epg_ids: boolean;
  include_channel_numbers: boolean;
  sort_order: SortOrder;
  filename_prefix: string;
  created_at: string;
  updated_at: string;
  // Added by list endpoint
  has_generated?: boolean;
  m3u_size?: number;
  xmltv_size?: number;
  last_generated_at?: number;
}

export interface ProfileCreateRequest {
  name: string;
  description?: string;
  selection_mode?: SelectionMode;
  selected_groups?: number[];
  selected_channels?: number[];
  stream_url_mode?: StreamUrlMode;
  include_logos?: boolean;
  include_epg_ids?: boolean;
  include_channel_numbers?: boolean;
  sort_order?: SortOrder;
  filename_prefix?: string;
}

export interface ProfileUpdateRequest {
  name?: string;
  description?: string;
  selection_mode?: SelectionMode;
  selected_groups?: number[];
  selected_channels?: number[];
  stream_url_mode?: StreamUrlMode;
  include_logos?: boolean;
  include_epg_ids?: boolean;
  include_channel_numbers?: boolean;
  sort_order?: SortOrder;
  filename_prefix?: string;
}

export interface PreviewChannel {
  name: string;
  number: number | string;
  group: string;
  epg_id: string | null;
  logo_url: string | null;
  stream_url: string;
}

export interface ProfilePreview {
  channels: PreviewChannel[];
  total_count: number;
  m3u_preview: string;
}

export interface GenerationResult {
  channels_count: number;
  m3u_size: number;
  xmltv_size: number;
  duration_ms: number;
  m3u_path: string;
  xmltv_path: string;
}

export interface CloudTarget {
  id: number;
  name: string;
  provider_type: ProviderType;
  credentials: Record<string, string>;
  upload_path: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface PublishConfig {
  id: number;
  name: string;
  profile_id: number;
  target_id: number | null;
  schedule_type: ScheduleType;
  cron_expression: string | null;
  event_triggers: string[];
  enabled: boolean;
  webhook_url: string | null;
  created_at: string;
  updated_at: string;
  // Joined names from list endpoint
  profile_name?: string;
  target_name?: string | null;
}

export interface PublishHistoryEntry {
  id: number;
  config_id: number;
  started_at: string;
  completed_at: string | null;
  status: PublishStatus;
  channels_count: number | null;
  file_size_bytes: number | null;
  error_message: string | null;
  details: Record<string, unknown> | null;
  config_name?: string;
  profile_name?: string;
}

export interface PublishHistoryResponse {
  total: number;
  page: number;
  per_page: number;
  entries: PublishHistoryEntry[];
}

export interface PublishResult {
  success: boolean;
  channels_count: number;
  m3u_size: number;
  xmltv_size: number;
  upload_result: Record<string, unknown> | null;
  duration_ms: number;
  error: string;
}
