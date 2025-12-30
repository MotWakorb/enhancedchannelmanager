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
}

export interface EPGSource {
  id: number;
  name: string;
  url: string;
  is_active: boolean;
}

export interface EPGData {
  id: number;
  tvg_id: string;
  name: string;
  icon_url: string | null;
  epg_source: number;
}

export interface StreamProfile {
  id: number;
  name: string;
  command: string;
  parameters: string;
  is_active: boolean;
  locked: boolean;
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

export interface M3UAccount {
  id: number;
  name: string;
  is_active: boolean;
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

export interface ChannelWithStreams extends Channel {
  streamDetails?: Stream[];
}

export interface ChannelGroupWithChannels extends ChannelGroup {
  channels: Channel[];
  expanded?: boolean;
}

// Re-export history types
export * from './history';

// Re-export edit mode types
export * from './editMode';
