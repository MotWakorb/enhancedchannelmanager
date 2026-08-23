import { useState } from 'react';
import type { CloudTarget, ProviderType } from '../../types/cloudTargets';
import * as cloudApi from '../../services/cloudTargetsApi';
import { useNotifications } from '../../contexts/NotificationContext';
import { ModalOverlay } from '../ModalOverlay';
import { useOwnedDialog } from '../../hooks/useOwnedDialog';
import { CustomSelect } from '../CustomSelect';
import '../ModalBase.css';

interface CloudTargetEditorProps {
  target: CloudTarget | null;
  onClose: () => void;
  onSaved: () => void;
}

// OneDrive and Dropbox are deferred this release (no SSRF hardening yet —
// PO decision 2026-07-25): they stay visible but disabled so operators are
// not invited to configure targets that only fail at Test Connection.
// Existing onedrive/dropbox rows still render and edit — the provider select
// is disabled while editing, so the disabled options never block an edit.
const PROVIDER_OPTIONS = [
  { value: 's3', label: 'Amazon S3 / S3-Compatible' },
  { value: 'gdrive', label: 'Google Drive' },
  { value: 'webdav', label: 'WebDAV (Nextcloud, ownCloud, NAS)' },
  { value: 'onedrive', label: 'OneDrive (not yet supported)', disabled: true },
  { value: 'dropbox', label: 'Dropbox (not yet supported)', disabled: true },
];

interface CredentialField {
  key: string;
  label: string;
  type: 'text' | 'password' | 'textarea';
  placeholder?: string;
  required?: boolean;
}

const PROVIDER_FIELDS: Record<string, CredentialField[]> = {
  s3: [
    { key: 'endpoint_url', label: 'Endpoint URL', type: 'text', placeholder: 'https://s3.amazonaws.com' },
    { key: 'bucket_name', label: 'Bucket Name', type: 'text', required: true },
    { key: 'access_key_id', label: 'Access Key ID', type: 'password', required: true },
    { key: 'secret_access_key', label: 'Secret Access Key', type: 'password', required: true },
    { key: 'region', label: 'Region', type: 'text', placeholder: 'us-east-1' },
  ],
  gdrive: [
    { key: 'service_account_json', label: 'Service Account JSON', type: 'textarea', required: true },
    { key: 'folder_id', label: 'Folder ID', type: 'text', placeholder: 'Google Drive folder ID' },
  ],
  webdav: [
    { key: 'base_url', label: 'WebDAV URL', type: 'text', placeholder: 'https://nas.example.com/remote.php/dav/files/admin', required: true },
    { key: 'username', label: 'Username', type: 'text', placeholder: '(optional for anonymous endpoints)' },
    { key: 'password', label: 'Password', type: 'password' },
  ],
  onedrive: [
    { key: 'client_id', label: 'Client ID', type: 'password', required: true },
    { key: 'client_secret', label: 'Client Secret', type: 'password', required: true },
    { key: 'tenant_id', label: 'Tenant ID', type: 'text', required: true },
    { key: 'drive_id', label: 'Drive ID', type: 'text' },
    { key: 'folder_path', label: 'Folder Path', type: 'text', placeholder: '/exports' },
  ],
  dropbox: [
    { key: 'access_token', label: 'Access Token', type: 'password', required: true },
    { key: 'app_key', label: 'App Key', type: 'password' },
    { key: 'app_secret', label: 'App Secret', type: 'password' },
  ],
};

export function CloudTargetEditor({ target, onClose, onSaved }: CloudTargetEditorProps) {
  const notifications = useNotifications();
  const { titleId, containerRef } = useOwnedDialog();
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);

  const [name, setName] = useState(target?.name || '');
  const [providerType, setProviderType] = useState<ProviderType>(target?.provider_type || 's3');
  const [credentials, setCredentials] = useState<Record<string, string>>({});
  const [uploadPath, setUploadPath] = useState(target?.upload_path || '/');
  const [enabled, setEnabled] = useState(target?.enabled ?? true);
  // Top-level TLS opt-out (PR #743 item 2) — never a credentials key. Only
  // surfaced for WebDAV (the self-signed-NAS case); the API rejects
  // credentials.insecure so this flag is the single policy source.
  const [insecure, setInsecure] = useState(target?.insecure ?? false);

  const isEditing = !!target;
  const fields = PROVIDER_FIELDS[providerType] || [];

  const updateCred = (key: string, value: string) => {
    setCredentials(prev => ({ ...prev, [key]: value }));
  };

  /**
   * What goes in the box — NEVER the stored value (bead …-ybr3u, rule 2).
   *
   * The read shape masks credential values to their last four characters, so
   * `target.credentials.access_key_id` is `"***5678"`, not the key. Prefilling
   * from it put a string that is not the secret into an input the operator
   * would then "keep", and submitting it would have written the mask itself
   * into the store. Only what this operator typed in this session is shown.
   */
  const getCredValue = (key: string): string => credentials[key] ?? '';

  /**
   * Whether the SAVED target already holds a value for this credential.
   *
   * The mask hides the values but leaves the KEYS intact, which is exactly
   * enough to tell the operator which boxes they have to retype — and no more
   * (same trick as `authModeOf` in `SyncTargetsCard.tsx`).
   */
  const hasStoredCred = (key: string): boolean =>
    isEditing && target?.credentials?.[key] !== undefined && target.credentials[key] !== '';

  const credPlaceholder = (field: CredentialField): string => {
    if (!isEditing) return field.placeholder || '';
    return hasStoredCred(field.key) ? 'currently set — retype to replace' : 'not set';
  };

  const buildCredentials = (): Record<string, string> => {
    const result: Record<string, string> = {};
    for (const field of fields) {
      const val = credentials[field.key];
      if (val !== undefined && val !== '') {
        result[field.key] = val;
      }
    }
    return result;
  };

  /**
   * All-or-nothing credential entry (bead …-ybr3u, rule 1).
   *
   * `PATCH /api/cloud-targets/{id}` REPLACES the whole `credentials` dict —
   * `routers/cloud_targets.py` does `target.credentials =
   * encrypt_credentials(req.credentials)`, it does not merge — and ECM cannot
   * read the stored values back to fill the gaps. So submitting only the box
   * the operator touched blanked every sibling: rotating an S3 secret wiped
   * `bucket_name`, `access_key_id` and `region`. Refuse the partial entry and
   * say why, rather than half-write it.
   *
   * Returns the fields still missing, or an empty list when the set is
   * complete. `[]` also covers "nothing typed at all" — that means "leave the
   * stored set alone", and the caller omits `credentials` from the payload.
   */
  const missingRequiredCreds = (creds: Record<string, string>): CredentialField[] => {
    if (Object.keys(creds).length === 0) return [];
    return fields.filter(f => f.required && !creds[f.key]);
  };

  const refusePartialCredentials = (missing: CredentialField[]) => {
    notifications.error(
      `Enter every required credential in full: ${missing.map(f => f.label).join(', ')}. ` +
        'Saving credentials REPLACES the whole stored set — ECM cannot read the ' +
        'stored values back, so a partial entry would blank the rest rather than ' +
        'add to it.',
    );
  };

  const handleTest = async () => {
    const creds = buildCredentials();
    if (Object.keys(creds).length === 0 && !isEditing) {
      notifications.error('Enter credentials first');
      return;
    }

    // On the EDIT path a half-typed set is not a thing that can be tested
    // meaningfully: the inline test would fail against a dict that is neither
    // what is stored nor what would be saved (reading as "your stored
    // credentials are broken"), and falling back to the saved-target test would
    // pass while telling the operator nothing about what they just typed.
    // The CREATE path keeps its field-by-field "test as you go" affordance —
    // there is no stored set there to misrepresent.
    if (isEditing) {
      const missing = missingRequiredCreds(creds);
      if (missing.length > 0) {
        refusePartialCredentials(missing);
        return;
      }
    }

    setTesting(true);
    try {
      let result;
      if (isEditing && Object.keys(creds).length === 0) {
        result = await cloudApi.testCloudTarget(target!.id);
      } else {
        result = await cloudApi.testCloudConnectionInline({
          provider_type: providerType,
          credentials: creds,
          insecure,
        });
      }
      if (result.success) {
        notifications.success('Connection successful!');
      } else {
        notifications.error(`Connection failed: ${result.message}`);
      }
    } catch (e) {
      notifications.error(e instanceof Error ? e.message : 'Test failed');
    } finally {
      setTesting(false);
    }
  };

  const handleSave = async () => {
    if (!name.trim()) {
      notifications.error('Name is required');
      return;
    }

    const creds = buildCredentials();
    if (!isEditing) {
      const missingRequired = fields.filter(f => f.required && !creds[f.key]);
      if (missingRequired.length > 0) {
        notifications.error(`Required: ${missingRequired.map(f => f.label).join(', ')}`);
        return;
      }
    } else {
      const missing = missingRequiredCreds(creds);
      if (missing.length > 0) {
        refusePartialCredentials(missing);
        return;
      }
    }

    setSaving(true);
    try {
      const data: Record<string, unknown> = {
        name: name.trim(),
        provider_type: providerType,
        upload_path: uploadPath,
        enabled,
        insecure,
      };
      if (Object.keys(creds).length > 0) {
        data.credentials = creds;
      }

      if (isEditing) {
        await cloudApi.updateCloudTarget(target!.id, data as Partial<CloudTarget>);
        notifications.success(`Target '${name}' updated`);
      } else {
        data.credentials = creds;
        await cloudApi.createCloudTarget(data as Partial<CloudTarget>);
        notifications.success(`Target '${name}' created`);
      }
      onSaved();
      onClose();
    } catch (e) {
      notifications.error(e instanceof Error ? e.message : 'Save failed');
    } finally {
      setSaving(false);
    }
  };

  const busy = saving || testing;
  const closeEditor = () => { if (!busy) onClose(); };

  return (
    <ModalOverlay onClose={closeEditor} role="dialog" aria-modal="true" aria-labelledby={titleId}>
      <div className="modal-container modal-lg" ref={containerRef}>
        <div className="modal-header">
          <h3 id={titleId}>{isEditing ? 'Edit Cloud Target' : 'New Cloud Target'}</h3>
          <button className="modal-close-btn" onClick={closeEditor} disabled={busy} aria-label="Close" title="Close">
            <span className="material-icons" aria-hidden="true">close</span>
          </button>
        </div>
        <div className="modal-body">
            <div className="modal-form-group">
              <label>Name</label>
              <input type="text" value={name} onChange={e => setName(e.target.value)} placeholder="My S3 Bucket" />
            </div>
            <div className="modal-form-group">
              <label>Provider</label>
              <CustomSelect
                value={providerType}
                onChange={(val) => { setProviderType(val as ProviderType); setCredentials({}); }}
                options={PROVIDER_OPTIONS}
                disabled={isEditing}
              />
            </div>
            <div className="modal-form-group">
              <label>Upload Path</label>
              <input type="text" value={uploadPath} onChange={e => setUploadPath(e.target.value)} placeholder="/" />
            </div>

            <div className="cloud-target-credentials">
              <label className="modal-section-title">Credentials</label>
              {/*
                Describes what the ROUTE does, not what would be convenient
                (bead …-ybr3u). The previous text promised a per-field merge
                that `PATCH /api/cloud-targets/{id}` has never performed.
              */}
              {isEditing && (
                <p className="form-hint">
                  Stored credentials cannot be read back. Leave every box blank to
                  keep them unchanged — entering any value <strong>replaces the
                  whole set</strong>, so retype every field this target needs.
                  Anything left blank is cleared.
                </p>
              )}
              {fields.map(field => (
                <div key={field.key} className="modal-form-group">
                  <label>
                    {field.label}
                    {field.required && !isEditing && <span className="modal-required">*</span>}
                  </label>
                  {field.type === 'textarea' ? (
                    <textarea
                      value={getCredValue(field.key)}
                      onChange={e => updateCred(field.key, e.target.value)}
                      placeholder={credPlaceholder(field)}
                      rows={4}

                    />
                  ) : (
                    <input
                      type={field.type}
                      value={getCredValue(field.key)}
                      onChange={e => updateCred(field.key, e.target.value)}
                      placeholder={credPlaceholder(field)}
                    />
                  )}
                </div>
              ))}
            </div>

            {providerType === 'webdav' && (
              <div className="modal-form-group">
                <label className="modal-checkbox-label">
                  <input
                    type="checkbox"
                    data-testid="cloud-target-insecure"
                    checked={insecure}
                    onChange={e => setInsecure(e.target.checked)}
                  />
                  Skip TLS certificate verification (insecure)
                </label>
                <p className="form-hint" data-testid="cloud-target-insecure-warning">
                  Advanced: only for self-signed endpoints you control. Without
                  verification, connections can be intercepted — every upload and
                  connection test that skips verification is audit-logged.
                </p>
              </div>
            )}

            <div className="modal-form-group">
              <button className="modal-btn modal-btn-secondary" onClick={handleTest} disabled={testing}>
                <span className={`material-icons${testing ? ' spinning' : ''}`}>
                  {testing ? 'sync' : 'wifi_tethering'}
                </span>
                Test Connection
              </button>
            </div>

            <label className="modal-checkbox-label">
              <input type="checkbox" checked={enabled} onChange={e => setEnabled(e.target.checked)} />
              Enabled
            </label>
        </div>
        <div className="modal-footer">
          <button className="modal-btn modal-btn-secondary" onClick={closeEditor} disabled={busy}>Cancel</button>
          <button className="modal-btn modal-btn-primary" onClick={handleSave} disabled={saving}>
            {saving ? 'Saving...' : isEditing ? 'Save Changes' : 'Create Target'}
          </button>
        </div>
      </div>
    </ModalOverlay>
  );
}
