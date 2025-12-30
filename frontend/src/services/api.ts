import type {
  Channel,
  ChannelGroup,
  Stream,
  M3UAccount,
  Logo,
  PaginatedResponse,
  EPGSource,
  EPGData,
  StreamProfile,
} from '../types';

const API_BASE = '/api';

async function fetchJson<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
  });

  if (!response.ok) {
    throw new Error(`API error: ${response.status} ${response.statusText}`);
  }

  return response.json();
}

// Channels
export async function getChannels(params?: {
  page?: number;
  pageSize?: number;
  search?: string;
  channelGroup?: number;
}): Promise<PaginatedResponse<Channel>> {
  const searchParams = new URLSearchParams();
  if (params?.page) searchParams.set('page', String(params.page));
  if (params?.pageSize) searchParams.set('page_size', String(params.pageSize));
  if (params?.search) searchParams.set('search', params.search);
  if (params?.channelGroup) searchParams.set('channel_group', String(params.channelGroup));

  const query = searchParams.toString();
  return fetchJson(`${API_BASE}/channels${query ? `?${query}` : ''}`);
}

export async function getChannel(id: number): Promise<Channel> {
  return fetchJson(`${API_BASE}/channels/${id}`);
}

export async function getChannelStreams(channelId: number): Promise<Stream[]> {
  return fetchJson(`${API_BASE}/channels/${channelId}/streams`);
}

export async function updateChannel(id: number, data: Partial<Channel>): Promise<Channel> {
  return fetchJson(`${API_BASE}/channels/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export async function addStreamToChannel(channelId: number, streamId: number): Promise<Channel> {
  return fetchJson(`${API_BASE}/channels/${channelId}/add-stream`, {
    method: 'POST',
    body: JSON.stringify({ stream_id: streamId }),
  });
}

export async function removeStreamFromChannel(channelId: number, streamId: number): Promise<Channel> {
  return fetchJson(`${API_BASE}/channels/${channelId}/remove-stream`, {
    method: 'POST',
    body: JSON.stringify({ stream_id: streamId }),
  });
}

export async function reorderChannelStreams(channelId: number, streamIds: number[]): Promise<Channel> {
  return fetchJson(`${API_BASE}/channels/${channelId}/reorder-streams`, {
    method: 'POST',
    body: JSON.stringify({ stream_ids: streamIds }),
  });
}

export async function bulkAssignChannelNumbers(
  channelIds: number[],
  startingNumber?: number
): Promise<void> {
  return fetchJson(`${API_BASE}/channels/assign-numbers`, {
    method: 'POST',
    body: JSON.stringify({ channel_ids: channelIds, starting_number: startingNumber }),
  });
}

export async function deleteChannel(channelId: number): Promise<void> {
  return fetchJson(`${API_BASE}/channels/${channelId}`, {
    method: 'DELETE',
  });
}

export async function createChannel(data: {
  name: string;
  channel_number?: number;
  channel_group_id?: number;
}): Promise<Channel> {
  return fetchJson(`${API_BASE}/channels`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

// Channel Groups
export async function getChannelGroups(): Promise<ChannelGroup[]> {
  return fetchJson(`${API_BASE}/channel-groups`);
}

export async function createChannelGroup(name: string): Promise<ChannelGroup> {
  return fetchJson(`${API_BASE}/channel-groups`, {
    method: 'POST',
    body: JSON.stringify({ name }),
  });
}

export async function updateChannelGroup(id: number, data: Partial<ChannelGroup>): Promise<ChannelGroup> {
  return fetchJson(`${API_BASE}/channel-groups/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export async function deleteChannelGroup(id: number): Promise<void> {
  await fetch(`${API_BASE}/channel-groups/${id}`, { method: 'DELETE' });
}

// Streams
export async function getStreams(params?: {
  page?: number;
  pageSize?: number;
  search?: string;
  channelGroup?: string;
  m3uAccount?: number;
}): Promise<PaginatedResponse<Stream>> {
  const searchParams = new URLSearchParams();
  if (params?.page) searchParams.set('page', String(params.page));
  if (params?.pageSize) searchParams.set('page_size', String(params.pageSize));
  if (params?.search) searchParams.set('search', params.search);
  if (params?.channelGroup) searchParams.set('channel_group_name', params.channelGroup);
  if (params?.m3uAccount) searchParams.set('m3u_account', String(params.m3uAccount));

  const query = searchParams.toString();
  return fetchJson(`${API_BASE}/streams${query ? `?${query}` : ''}`);
}

export async function getStreamGroups(): Promise<string[]> {
  return fetchJson(`${API_BASE}/stream-groups`);
}

// M3U Accounts (Providers)
export async function getM3UAccounts(): Promise<M3UAccount[]> {
  return fetchJson(`${API_BASE}/providers`);
}

// Health check
export async function getHealth(): Promise<{ status: string; service: string }> {
  return fetchJson(`${API_BASE}/health`);
}

// Settings
export interface SettingsResponse {
  url: string;
  username: string;
  configured: boolean;
  auto_rename_channel_number: boolean;
}

export interface TestConnectionResult {
  success: boolean;
  message: string;
}

export async function getSettings(): Promise<SettingsResponse> {
  return fetchJson(`${API_BASE}/settings`);
}

export async function saveSettings(settings: {
  url: string;
  username: string;
  password?: string;  // Optional - only required when changing URL or username
  auto_rename_channel_number: boolean;
}): Promise<{ status: string; configured: boolean }> {
  return fetchJson(`${API_BASE}/settings`, {
    method: 'POST',
    body: JSON.stringify(settings),
  });
}

export async function testConnection(settings: {
  url: string;
  username: string;
  password: string;
}): Promise<TestConnectionResult> {
  return fetchJson(`${API_BASE}/settings/test`, {
    method: 'POST',
    body: JSON.stringify(settings),
  });
}

// Logos
export async function getLogos(params?: {
  page?: number;
  pageSize?: number;
  search?: string;
}): Promise<PaginatedResponse<Logo>> {
  const searchParams = new URLSearchParams();
  if (params?.page) searchParams.set('page', String(params.page));
  if (params?.pageSize) searchParams.set('page_size', String(params.pageSize));
  if (params?.search) searchParams.set('search', params.search);

  const query = searchParams.toString();
  return fetchJson(`${API_BASE}/channels/logos${query ? `?${query}` : ''}`);
}

export async function getLogo(id: number): Promise<Logo> {
  return fetchJson(`${API_BASE}/channels/logos/${id}`);
}

export async function createLogo(data: { name: string; url: string }): Promise<Logo> {
  return fetchJson(`${API_BASE}/channels/logos`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function updateLogo(id: number, data: Partial<Logo>): Promise<Logo> {
  return fetchJson(`${API_BASE}/channels/logos/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export async function deleteLogo(id: number): Promise<void> {
  return fetchJson(`${API_BASE}/channels/logos/${id}`, {
    method: 'DELETE',
  });
}

export async function uploadLogo(file: File): Promise<Logo> {
  const formData = new FormData();
  formData.append('file', file);
  formData.append('name', file.name);

  const response = await fetch(`${API_BASE}/channels/logos/upload`, {
    method: 'POST',
    body: formData,
  });

  if (!response.ok) {
    throw new Error(`API error: ${response.status} ${response.statusText}`);
  }

  return response.json();
}

// EPG Sources
export async function getEPGSources(): Promise<EPGSource[]> {
  return fetchJson(`${API_BASE}/epg/sources`);
}

// EPG Data
export async function getEPGData(params?: {
  search?: string;
  epgSource?: number;
}): Promise<EPGData[]> {
  const searchParams = new URLSearchParams();
  if (params?.search) searchParams.set('search', params.search);
  if (params?.epgSource) searchParams.set('epg_source', String(params.epgSource));

  const query = searchParams.toString();
  return fetchJson(`${API_BASE}/epg/data${query ? `?${query}` : ''}`);
}

export async function getEPGDataById(id: number): Promise<EPGData> {
  return fetchJson(`${API_BASE}/epg/data/${id}`);
}

// Stream Profiles
export async function getStreamProfiles(): Promise<StreamProfile[]> {
  return fetchJson(`${API_BASE}/stream-profiles`);
}
