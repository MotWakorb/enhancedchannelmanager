export interface Channel {
  id: number;
  channel_number: number | null;
  name: string;
  channel_group_id: number | null;
  tvg_id: string | null;
  epg_data_id: number | null;
  streams: number[];
  stream_profile_id: number | null;
  uuid: string;
  logo_id: number | null;
  auto_created: boolean;
  auto_created_by: number | null;
  auto_created_by_name: string | null;
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
