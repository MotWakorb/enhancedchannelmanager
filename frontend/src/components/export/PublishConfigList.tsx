import { useState, useEffect, useCallback } from 'react';
import type { PublishConfig, PlaylistProfile, CloudTarget } from '../../types/export';
import * as exportApi from '../../services/exportApi';
import { useNotifications } from '../../contexts/NotificationContext';
import { PublishConfigEditor } from './PublishConfigEditor';
import { ModalOverlay } from '../ModalOverlay';
import '../ModalBase.css';

function formatSchedule(config: PublishConfig): string {
  if (config.schedule_type === 'manual') return 'Manual';
  if (config.schedule_type === 'cron') return config.cron_expression || 'Cron (no expression)';
  if (config.schedule_type === 'event') {
    const triggers = config.event_triggers;
    if (!triggers.length) return 'Event (none)';
    const labels: Record<string, string> = {
      m3u_refresh: 'M3U Refresh',
      channel_edit: 'Channel Edit',
      epg_refresh: 'EPG Refresh',
    };
    return 'On ' + triggers.map(t => labels[t] || t).join(', ');
  }
  return config.schedule_type;
}

export function PublishConfigList() {
  const notifications = useNotifications();
  const [configs, setConfigs] = useState<PublishConfig[]>([]);
  const [profiles, setProfiles] = useState<PlaylistProfile[]>([]);
  const [targets, setTargets] = useState<CloudTarget[]>([]);
  const [loading, setLoading] = useState(true);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingConfig, setEditingConfig] = useState<PublishConfig | null>(null);
  const [deletingConfig, setDeletingConfig] = useState<PublishConfig | null>(null);
  const [publishingId, setPublishingId] = useState<number | null>(null);

  const loadData = useCallback(async () => {
    try {
      const [c, p, t] = await Promise.all([
        exportApi.getPublishConfigs(),
        exportApi.getProfiles(),
        exportApi.getCloudTargets(),
      ]);
      setConfigs(c);
      setProfiles(p);
      setTargets(t);
    } catch (e) {
      notifications.error(e instanceof Error ? e.message : 'Failed to load data');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadData(); }, [loadData]);

  const handleCreate = () => { setEditingConfig(null); setEditorOpen(true); };
  const handleEdit = (c: PublishConfig) => { setEditingConfig(c); setEditorOpen(true); };

  const handleDelete = async () => {
    if (!deletingConfig) return;
    try {
      await exportApi.deletePublishConfig(deletingConfig.id);
      notifications.success(`Deleted config '${deletingConfig.name}'`);
      setDeletingConfig(null);
      loadData();
    } catch (e) {
      notifications.error(e instanceof Error ? e.message : 'Delete failed');
    }
  };

  const handlePublish = async (config: PublishConfig) => {
    setPublishingId(config.id);
    try {
      const result = await exportApi.publishNow(config.id);
      if (result.success) {
        notifications.success(
          `Published '${config.name}': ${result.channels_count} channels in ${result.duration_ms}ms`
        );
      } else {
        notifications.error(`Publish failed: ${result.error}`);
      }
    } catch (e) {
      notifications.error(e instanceof Error ? e.message : 'Publish failed');
    } finally {
      setPublishingId(null);
    }
  };

  const handleToggle = async (config: PublishConfig) => {
    try {
      await exportApi.updatePublishConfig(config.id, { enabled: !config.enabled });
      loadData();
    } catch (e) {
      notifications.error(e instanceof Error ? e.message : 'Update failed');
    }
  };

  if (loading) {
    return (
      <div className="export-loading">
        <span className="material-icons spinning">sync</span>
        <p>Loading publish configs...</p>
      </div>
    );
  }

  return (
    <div className="publish-config-list">
      <div className="profile-list-header">
        <h3>Publish Configurations</h3>
        <button className="btn btn-primary" onClick={handleCreate} disabled={profiles.length === 0}>
          <span className="material-icons">add</span>
          New Config
        </button>
      </div>

      {profiles.length === 0 ? (
        <div className="profile-list-empty">
          <span className="material-icons">info</span>
          <p>Create an export profile first before setting up publishing.</p>
        </div>
      ) : configs.length === 0 ? (
        <div className="profile-list-empty">
          <span className="material-icons">publish</span>
          <p>No publish configurations yet</p>
          <p className="export-hint">Publish configs link a profile to an optional cloud target with scheduling.</p>
          <button className="btn btn-primary" onClick={handleCreate}>
            Create Publish Config
          </button>
        </div>
      ) : (
        <div className="profile-list-items">
          {configs.map(config => (
            <div key={config.id} className={`profile-card ${!config.enabled ? 'is-disabled' : ''}`}>
              <div className="profile-card-header">
                <div className="profile-card-info">
                  <span className="profile-card-name">{config.name}</span>
                  <span className="profile-card-mode">{config.profile_name || `Profile #${config.profile_id}`}</span>
                  <span className="profile-card-size">
                    {config.target_name || 'Local only'}
                  </span>
                  <span className="publish-schedule-badge">{formatSchedule(config)}</span>
                  {!config.enabled && <span className="status-badge status-disabled">Disabled</span>}
                </div>
                <div className="profile-card-actions">
                  <button
                    className="btn btn-sm"
                    onClick={() => handlePublish(config)}
                    disabled={publishingId === config.id}
                    title="Publish Now"
                  >
                    <span className={`material-icons${publishingId === config.id ? ' spinning' : ''}`}>
                      {publishingId === config.id ? 'sync' : 'play_arrow'}
                    </span>
                  </button>
                  <button
                    className="btn btn-sm btn-icon"
                    onClick={() => handleToggle(config)}
                    title={config.enabled ? 'Disable' : 'Enable'}
                  >
                    <span className="material-icons">
                      {config.enabled ? 'toggle_on' : 'toggle_off'}
                    </span>
                  </button>
                  <button className="btn btn-sm btn-icon" onClick={() => handleEdit(config)} title="Edit">
                    <span className="material-icons">edit</span>
                  </button>
                  <button className="btn btn-sm btn-icon" onClick={() => setDeletingConfig(config)} title="Delete">
                    <span className="material-icons">delete</span>
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {editorOpen && (
        <PublishConfigEditor
          config={editingConfig}
          profiles={profiles}
          targets={targets}
          onClose={() => setEditorOpen(false)}
          onSaved={loadData}
        />
      )}

      {deletingConfig && (
        <ModalOverlay onClose={() => setDeletingConfig(null)}>
          <div className="modal-container modal-sm">
            <div className="modal-header"><h3>Delete Config</h3></div>
            <div className="modal-body">
              <p>Delete publish config <strong>{deletingConfig.name}</strong>? History entries will also be removed.</p>
            </div>
            <div className="modal-footer">
              <button className="modal-btn modal-btn-secondary" onClick={() => setDeletingConfig(null)}>Cancel</button>
              <button className="modal-btn modal-btn-danger" onClick={handleDelete}>Delete</button>
            </div>
          </div>
        </ModalOverlay>
      )}
    </div>
  );
}
