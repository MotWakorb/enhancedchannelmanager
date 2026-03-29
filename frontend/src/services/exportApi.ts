import { fetchJson } from './httpClient';
import type {
  PlaylistProfile,
  ProfileCreateRequest,
  ProfileUpdateRequest,
  ProfilePreview,
  GenerationResult,
  CloudTarget,
  PublishConfig,
  PublishHistoryResponse,
  PublishResult,
} from '../types/export';

const API_BASE = '/api/export';
const LOG_PREFIX = 'Export API';

function api<T>(url: string, options?: RequestInit): Promise<T> {
  return fetchJson<T>(url, options, LOG_PREFIX);
}

// ---------------------------------------------------------------------------
// Profiles
// ---------------------------------------------------------------------------

export async function getProfiles(): Promise<PlaylistProfile[]> {
  return api<PlaylistProfile[]>(`${API_BASE}/profiles`);
}

export async function createProfile(data: ProfileCreateRequest): Promise<PlaylistProfile> {
  return api<PlaylistProfile>(`${API_BASE}/profiles`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function updateProfile(id: number, data: ProfileUpdateRequest): Promise<PlaylistProfile> {
  return api<PlaylistProfile>(`${API_BASE}/profiles/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export async function deleteProfile(id: number): Promise<void> {
  await api<void>(`${API_BASE}/profiles/${id}`, { method: 'DELETE' });
}

export async function generateExport(id: number): Promise<GenerationResult> {
  return api<GenerationResult>(`${API_BASE}/profiles/${id}/generate`, { method: 'POST' });
}

export async function previewProfile(id: number): Promise<ProfilePreview> {
  return api<ProfilePreview>(`${API_BASE}/profiles/${id}/preview`);
}

export async function downloadM3U(id: number): Promise<void> {
  const response = await fetch(`${API_BASE}/profiles/${id}/download/m3u`, {
    credentials: 'include',
  });
  if (!response.ok) throw new Error('Download failed');
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = response.headers.get('content-disposition')?.split('filename=')[1]?.replace(/"/g, '') || 'playlist.m3u';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

export async function downloadXMLTV(id: number): Promise<void> {
  const response = await fetch(`${API_BASE}/profiles/${id}/download/xmltv`, {
    credentials: 'include',
  });
  if (!response.ok) throw new Error('Download failed');
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = response.headers.get('content-disposition')?.split('filename=')[1]?.replace(/"/g, '') || 'epg.xml';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// ---------------------------------------------------------------------------
// Cloud Targets
// ---------------------------------------------------------------------------

export async function getCloudTargets(): Promise<CloudTarget[]> {
  return api<CloudTarget[]>(`${API_BASE}/cloud-targets`);
}

export async function createCloudTarget(data: Partial<CloudTarget>): Promise<CloudTarget> {
  return api<CloudTarget>(`${API_BASE}/cloud-targets`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function updateCloudTarget(id: number, data: Partial<CloudTarget>): Promise<CloudTarget> {
  return api<CloudTarget>(`${API_BASE}/cloud-targets/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export async function deleteCloudTarget(id: number): Promise<void> {
  await api<void>(`${API_BASE}/cloud-targets/${id}`, { method: 'DELETE' });
}

export async function testCloudTarget(id: number): Promise<{ success: boolean; message: string }> {
  return api<{ success: boolean; message: string }>(`${API_BASE}/cloud-targets/${id}/test`, { method: 'POST' });
}

export async function testCloudConnectionInline(data: {
  provider_type: string;
  credentials: Record<string, string>;
}): Promise<{ success: boolean; message: string }> {
  return api<{ success: boolean; message: string }>(`${API_BASE}/cloud-targets/test`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

// ---------------------------------------------------------------------------
// Publish Configs
// ---------------------------------------------------------------------------

export async function getPublishConfigs(): Promise<PublishConfig[]> {
  return api<PublishConfig[]>(`${API_BASE}/publish-configs`);
}

export async function createPublishConfig(data: Partial<PublishConfig>): Promise<PublishConfig> {
  return api<PublishConfig>(`${API_BASE}/publish-configs`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function updatePublishConfig(id: number, data: Partial<PublishConfig>): Promise<PublishConfig> {
  return api<PublishConfig>(`${API_BASE}/publish-configs/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export async function deletePublishConfig(id: number): Promise<void> {
  await api<void>(`${API_BASE}/publish-configs/${id}`, { method: 'DELETE' });
}

export async function publishNow(configId: number): Promise<PublishResult> {
  return api<PublishResult>(`${API_BASE}/publish-configs/${configId}/publish`, { method: 'POST' });
}

export async function dryRunPublish(configId: number): Promise<PublishResult> {
  return api<PublishResult>(`${API_BASE}/publish-configs/${configId}/dry-run`, { method: 'POST' });
}

// ---------------------------------------------------------------------------
// Publish History
// ---------------------------------------------------------------------------

export async function getPublishHistory(params?: {
  config_id?: number;
  status?: string;
  page?: number;
  per_page?: number;
}): Promise<PublishHistoryResponse> {
  const search = new URLSearchParams();
  if (params?.config_id) search.set('config_id', String(params.config_id));
  if (params?.status) search.set('status', params.status);
  if (params?.page) search.set('page', String(params.page));
  if (params?.per_page) search.set('per_page', String(params.per_page));
  const query = search.toString();
  return api<PublishHistoryResponse>(`${API_BASE}/publish-history${query ? `?${query}` : ''}`);
}

export async function deleteHistoryEntry(id: number): Promise<void> {
  await api<void>(`${API_BASE}/publish-history/${id}`, { method: 'DELETE' });
}

export async function deleteHistoryBulk(olderThanDays: number): Promise<{ deleted: number }> {
  return api<{ deleted: number }>(`${API_BASE}/publish-history?older_than_days=${olderThanDays}`, {
    method: 'DELETE',
  });
}
