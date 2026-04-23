import { useState, useEffect, useCallback } from 'react';
import type { CloudTarget } from '../../types/export';
import * as exportApi from '../../services/exportApi';
import { useNotifications } from '../../contexts/NotificationContext';
import { CloudTargetEditor } from './CloudTargetEditor';
import { ModalOverlay } from '../ModalOverlay';
import '../ModalBase.css';

const PROVIDER_ICONS: Record<string, string> = {
  s3: 'cloud',
  gdrive: 'add_to_drive',
  onedrive: 'cloud_queue',
  dropbox: 'cloud_circle',
};

const PROVIDER_LABELS: Record<string, string> = {
  s3: 'Amazon S3',
  gdrive: 'Google Drive',
  onedrive: 'OneDrive',
  dropbox: 'Dropbox',
};

export function CloudTargetList() {
  const notifications = useNotifications();
  const [targets, setTargets] = useState<CloudTarget[]>([]);
  const [loading, setLoading] = useState(true);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingTarget, setEditingTarget] = useState<CloudTarget | null>(null);
  const [deletingTarget, setDeletingTarget] = useState<CloudTarget | null>(null);
  const [testingId, setTestingId] = useState<number | null>(null);

  const loadTargets = useCallback(async () => {
    try {
      const data = await exportApi.getCloudTargets();
      setTargets(data);
    } catch (e) {
      notifications.error(e instanceof Error ? e.message : 'Failed to load targets');
    } finally {
      setLoading(false);
    }
  }, [notifications]);

  useEffect(() => { loadTargets(); }, [loadTargets]);

  const handleEdit = (target: CloudTarget) => {
    setEditingTarget(target);
    setEditorOpen(true);
  };

  const handleCreate = () => {
    setEditingTarget(null);
    setEditorOpen(true);
  };

  const handleDelete = async () => {
    if (!deletingTarget) return;
    try {
      await exportApi.deleteCloudTarget(deletingTarget.id);
      notifications.success(`Deleted target '${deletingTarget.name}'`);
      setDeletingTarget(null);
      loadTargets();
    } catch (e) {
      notifications.error(e instanceof Error ? e.message : 'Delete failed');
    }
  };

  const handleTest = async (target: CloudTarget) => {
    setTestingId(target.id);
    try {
      const result = await exportApi.testCloudTarget(target.id);
      if (result.success) {
        notifications.success(`Connection to '${target.name}' successful`);
      } else {
        notifications.error(`Connection failed: ${result.message}`);
      }
    } catch (e) {
      notifications.error(e instanceof Error ? e.message : 'Test failed');
    } finally {
      setTestingId(null);
    }
  };

  const handleToggle = async (target: CloudTarget) => {
    try {
      await exportApi.updateCloudTarget(target.id, { enabled: !target.enabled });
      loadTargets();
    } catch (e) {
      notifications.error(e instanceof Error ? e.message : 'Update failed');
    }
  };

  if (loading) {
    return (
      <div className="export-loading">
        <span className="material-icons spinning">sync</span>
        <p>Loading cloud targets...</p>
      </div>
    );
  }

  return (
    <div className="cloud-target-list">
      <div className="profile-list-header">
        <h3>Cloud Storage Targets</h3>
        <button className="btn btn-primary" onClick={handleCreate}>
          <span className="material-icons">add</span>
          New Target
        </button>
      </div>

      {targets.length === 0 ? (
        <div className="profile-list-empty">
          <span className="material-icons">cloud_off</span>
          <p>No cloud targets configured</p>
          <p className="export-hint">Cloud targets are optional — you can generate and download exports locally without one.</p>
          <button className="btn btn-primary" onClick={handleCreate}>
            Add Cloud Target
          </button>
        </div>
      ) : (
        <div className="profile-list-items">
          {targets.map(target => (
            <div key={target.id} className={`profile-card ${!target.enabled ? 'is-disabled' : ''}`}>
              <div className="profile-card-header">
                <div className="profile-card-info">
                  <span className="material-icons cloud-target-icon">
                    {PROVIDER_ICONS[target.provider_type] || 'cloud'}
                  </span>
                  <span className="profile-card-name">{target.name}</span>
                  <span className="profile-card-mode">
                    {PROVIDER_LABELS[target.provider_type] || target.provider_type}
                  </span>
                  <span className="profile-card-size">{target.upload_path}</span>
                  {!target.enabled && <span className="status-badge status-disabled">Disabled</span>}
                </div>
                <div className="profile-card-actions">
                  <button
                    className="btn btn-sm"
                    onClick={() => handleTest(target)}
                    disabled={testingId === target.id}
                    title="Test Connection"
                  >
                    <span className={`material-icons${testingId === target.id ? ' spinning' : ''}`}>
                      {testingId === target.id ? 'sync' : 'wifi_tethering'}
                    </span>
                  </button>
                  <button
                    className="btn btn-sm btn-icon"
                    onClick={() => handleToggle(target)}
                    title={target.enabled ? 'Disable' : 'Enable'}
                  >
                    <span className="material-icons">
                      {target.enabled ? 'toggle_on' : 'toggle_off'}
                    </span>
                  </button>
                  <button className="btn btn-sm btn-icon" onClick={() => handleEdit(target)} title="Edit">
                    <span className="material-icons">edit</span>
                  </button>
                  <button className="btn btn-sm btn-icon" onClick={() => setDeletingTarget(target)} title="Delete">
                    <span className="material-icons">delete</span>
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {editorOpen && (
        <CloudTargetEditor
          target={editingTarget}
          onClose={() => setEditorOpen(false)}
          onSaved={loadTargets}
        />
      )}

      {deletingTarget && (
        <ModalOverlay onClose={() => setDeletingTarget(null)}>
          <div className="modal-container modal-sm">
            <div className="modal-header"><h3>Delete Target</h3></div>
            <div className="modal-body">
              <p>Delete cloud target <strong>{deletingTarget.name}</strong>? Publish configs using this target will switch to local-only.</p>
            </div>
            <div className="modal-footer">
              <button className="modal-btn modal-btn-secondary" onClick={() => setDeletingTarget(null)}>Cancel</button>
              <button className="modal-btn modal-btn-danger" onClick={handleDelete}>Delete</button>
            </div>
          </div>
        </ModalOverlay>
      )}
    </div>
  );
}
