/**
 * Cloud storage target types — DBAS backup upload destinations.
 *
 * Relocated from the removed Export-tab `types/export.ts` (beads vrrxv / 1w428).
 * Cloud targets are now managed under Settings → Backup & Restore and backed by
 * the `/api/cloud-targets` router.
 */

export type ProviderType = 's3' | 'gdrive' | 'webdav' | 'onedrive' | 'dropbox';

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
