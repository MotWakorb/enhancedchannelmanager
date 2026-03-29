import { useState } from 'react';
import type { CloudTarget, ProviderType } from '../../types/export';
import * as exportApi from '../../services/exportApi';
import { useNotifications } from '../../contexts/NotificationContext';
import { ModalOverlay } from '../ModalOverlay';
import { CustomSelect } from '../CustomSelect';
import '../ModalBase.css';

interface CloudTargetEditorProps {
  target: CloudTarget | null;
  onClose: () => void;
  onSaved: () => void;
}

const PROVIDER_OPTIONS = [
  { value: 's3', label: 'Amazon S3 / S3-Compatible' },
  { value: 'gdrive', label: 'Google Drive' },
  { value: 'onedrive', label: 'OneDrive' },
  { value: 'dropbox', label: 'Dropbox' },
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
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);

  const [name, setName] = useState(target?.name || '');
  const [providerType, setProviderType] = useState<ProviderType>(target?.provider_type || 's3');
  const [credentials, setCredentials] = useState<Record<string, string>>({});
  const [uploadPath, setUploadPath] = useState(target?.upload_path || '/');
  const [enabled, setEnabled] = useState(target?.enabled ?? true);

  const isEditing = !!target;
  const fields = PROVIDER_FIELDS[providerType] || [];

  const updateCred = (key: string, value: string) => {
    setCredentials(prev => ({ ...prev, [key]: value }));
  };

  const getCredValue = (key: string): string => {
    if (credentials[key] !== undefined) return credentials[key];
    if (isEditing && target?.credentials[key]) return target.credentials[key];
    return '';
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

  const handleTest = async () => {
    const creds = buildCredentials();
    if (Object.keys(creds).length === 0 && !isEditing) {
      notifications.error('Enter credentials first');
      return;
    }

    setTesting(true);
    try {
      let result;
      if (isEditing && Object.keys(creds).length === 0) {
        result = await exportApi.testCloudTarget(target!.id);
      } else {
        result = await exportApi.testCloudConnectionInline({
          provider_type: providerType,
          credentials: creds,
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
    }

    setSaving(true);
    try {
      const data: Record<string, unknown> = {
        name: name.trim(),
        provider_type: providerType,
        upload_path: uploadPath,
        enabled,
      };
      if (Object.keys(creds).length > 0) {
        data.credentials = creds;
      }

      if (isEditing) {
        await exportApi.updateCloudTarget(target!.id, data as Partial<CloudTarget>);
        notifications.success(`Target '${name}' updated`);
      } else {
        data.credentials = creds;
        await exportApi.createCloudTarget(data as Partial<CloudTarget>);
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

  return (
    <ModalOverlay onClose={onClose}>
      <div className="modal-container modal-lg">
        <div className="modal-header">
          <h3>{isEditing ? 'Edit Cloud Target' : 'New Cloud Target'}</h3>
          <button className="modal-close-btn" onClick={onClose}>
            <span className="material-icons">close</span>
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
              {isEditing && (
                <p className="form-hint">Leave fields empty to keep existing values. Only changed fields will be updated.</p>
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
                      placeholder={field.placeholder || (isEditing ? '(unchanged)' : '')}
                      rows={4}

                    />
                  ) : (
                    <input
                      type={field.type}
                      value={getCredValue(field.key)}
                      onChange={e => updateCred(field.key, e.target.value)}
                      placeholder={field.placeholder || (isEditing ? '(unchanged)' : '')}
                    />
                  )}
                </div>
              ))}
            </div>

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
          <button className="modal-btn modal-btn-secondary" onClick={onClose}>Cancel</button>
          <button className="modal-btn modal-btn-primary" onClick={handleSave} disabled={saving}>
            {saving ? 'Saving...' : isEditing ? 'Save Changes' : 'Create Target'}
          </button>
        </div>
      </div>
    </ModalOverlay>
  );
}
