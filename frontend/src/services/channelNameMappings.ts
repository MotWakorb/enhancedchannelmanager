import { fetchJson } from './httpClient';

export interface ChannelNameMapping {
  id: number;
  preferred_name: string;
  aliases: string[];
}

const BASE = '/api/normalization/mappings';

export function getChannelNameMappings(): Promise<{ mappings: ChannelNameMapping[] }> {
  return fetchJson(BASE);
}

export function saveChannelNameMapping(mapping: Omit<ChannelNameMapping, 'id'>, id?: number): Promise<ChannelNameMapping> {
  return fetchJson(id === undefined ? BASE : `${BASE}/${id}`, {
    method: id === undefined ? 'POST' : 'PUT', body: JSON.stringify(mapping),
  });
}

export function deleteChannelNameMapping(id: number): Promise<void> {
  return fetchJson(`${BASE}/${id}`, { method: 'DELETE' });
}
