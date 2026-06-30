/**
 * Cloud storage target API client — DBAS backup upload destinations.
 *
 * Relocated from the removed Export-tab `services/exportApi.ts`
 * (beads vrrxv / 1w428). Endpoints moved from `/api/export/cloud-targets` to
 * the dedicated `/api/cloud-targets` router.
 */
import { fetchJson } from './httpClient';
import type { CloudTarget } from '../types/cloudTargets';

const API_BASE = '/api/cloud-targets';
const LOG_PREFIX = 'Cloud Targets API';

function api<T>(url: string, options?: RequestInit): Promise<T> {
  return fetchJson<T>(url, options, LOG_PREFIX);
}

export async function getCloudTargets(): Promise<CloudTarget[]> {
  return api<CloudTarget[]>(`${API_BASE}`);
}

export async function createCloudTarget(data: Partial<CloudTarget>): Promise<CloudTarget> {
  return api<CloudTarget>(`${API_BASE}`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export async function updateCloudTarget(id: number, data: Partial<CloudTarget>): Promise<CloudTarget> {
  return api<CloudTarget>(`${API_BASE}/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export async function deleteCloudTarget(id: number): Promise<void> {
  await api<void>(`${API_BASE}/${id}`, { method: 'DELETE' });
}

export async function testCloudTarget(id: number): Promise<{ success: boolean; message: string }> {
  return api<{ success: boolean; message: string }>(`${API_BASE}/${id}/test`, { method: 'POST' });
}

export async function testCloudConnectionInline(data: {
  provider_type: string;
  credentials: Record<string, string>;
}): Promise<{ success: boolean; message: string }> {
  return api<{ success: boolean; message: string }>(`${API_BASE}/test`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}
