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
  /**
   * Skip TLS certificate verification for this target (self-signed endpoints).
   * Top-level flag ONLY — `credentials.insecure` is reserved and rejected by
   * the API; every verification skip is audit-logged (PR #743 item 2).
   */
  insecure: boolean;
  created_at: string;
  updated_at: string;
}
