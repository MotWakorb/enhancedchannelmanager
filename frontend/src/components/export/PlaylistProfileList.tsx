import { useState, useEffect, useCallback } from 'react';
import type { PlaylistProfile } from '../../types/export';
import * as exportApi from '../../services/exportApi';
import { useNotifications } from '../../contexts/NotificationContext';
import { PlaylistProfileEditor } from './PlaylistProfileEditor';
import { GenerateControls } from './GenerateControls';
import { ModalOverlay } from '../ModalOverlay';
import { formatDateTime } from '../../utils/formatting';
import '../ModalBase.css';

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const SELECTION_LABELS: Record<string, string> = {
  all: 'All Channels',
  groups: 'By Group',
  channels: 'By Channel',
};

export function PlaylistProfileList() {
  const notifications = useNotifications();
  const [profiles, setProfiles] = useState<PlaylistProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingProfile, setEditingProfile] = useState<PlaylistProfile | null>(null);
  const [deletingProfile, setDeletingProfile] = useState<PlaylistProfile | null>(null);
  const [expandedId, setExpandedId] = useState<number | null>(null);

  const loadProfiles = useCallback(async () => {
    try {
      const data = await exportApi.getProfiles();
      setProfiles(data);
    } catch (e) {
      notifications.error(e instanceof Error ? e.message : 'Failed to load profiles');
    } finally {
      setLoading(false);
    }
  }, [notifications]);

  useEffect(() => { loadProfiles(); }, [loadProfiles]);

  const handleEdit = (profile: PlaylistProfile) => {
    setEditingProfile(profile);
    setEditorOpen(true);
  };

  const handleCreate = () => {
    setEditingProfile(null);
    setEditorOpen(true);
  };

  const handleDelete = async () => {
    if (!deletingProfile) return;
    try {
      await exportApi.deleteProfile(deletingProfile.id);
      notifications.success(`Deleted profile '${deletingProfile.name}'`);
      setDeletingProfile(null);
      loadProfiles();
    } catch (e) {
      notifications.error(e instanceof Error ? e.message : 'Delete failed');
    }
  };

  if (loading) {
    return (
      <div className="export-loading">
        <span className="material-icons spinning">sync</span>
        <p>Loading profiles...</p>
      </div>
    );
  }

  return (
    <div className="profile-list">
      <div className="profile-list-header">
        <h3>Export Profiles</h3>
        <button className="btn btn-primary" onClick={handleCreate}>
          <span className="material-icons">add</span>
          New Profile
        </button>
      </div>

      {profiles.length === 0 ? (
        <div className="profile-list-empty">
          <span className="material-icons">cloud_upload</span>
          <p>No export profiles yet</p>
          <button className="btn btn-primary" onClick={handleCreate}>
            Create Your First Profile
          </button>
        </div>
      ) : (
        <div className="profile-list-items">
          {profiles.map(profile => (
            <div key={profile.id} className="profile-card">
              <div className="profile-card-header" onClick={() => setExpandedId(expandedId === profile.id ? null : profile.id)}>
                <div className="profile-card-info">
                  <span className="profile-card-name">{profile.name}</span>
                  <span className="profile-card-mode">{SELECTION_LABELS[profile.selection_mode] || profile.selection_mode}</span>
                  {profile.has_generated && profile.m3u_size != null && (
                    <span className="profile-card-size">M3U: {formatSize(profile.m3u_size)}</span>
                  )}
                </div>
                <div className="profile-card-actions">
                  <button className="btn btn-sm btn-icon" onClick={(e) => { e.stopPropagation(); handleEdit(profile); }} title="Edit">
                    <span className="material-icons">edit</span>
                  </button>
                  <button className="btn btn-sm btn-icon" onClick={(e) => { e.stopPropagation(); setDeletingProfile(profile); }} title="Delete">
                    <span className="material-icons">delete</span>
                  </button>
                  <span className="material-icons profile-card-expand">
                    {expandedId === profile.id ? 'expand_less' : 'expand_more'}
                  </span>
                </div>
              </div>
              {expandedId === profile.id && (
                <div className="profile-card-body">
                  {profile.description && (
                    <p className="profile-card-description">{profile.description}</p>
                  )}
                  <div className="profile-card-details">
                    <span>URL Mode: {profile.stream_url_mode}</span>
                    <span>Sort: {profile.sort_order}</span>
                    <span>Prefix: {profile.filename_prefix}</span>
                    <span>Created: {formatDateTime(profile.created_at)}</span>
                  </div>
                  <GenerateControls profile={profile} onGenerated={loadProfiles} />
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {editorOpen && (
        <PlaylistProfileEditor
          profile={editingProfile}
          onClose={() => setEditorOpen(false)}
          onSaved={loadProfiles}
        />
      )}

      {deletingProfile && (
        <ModalOverlay onClose={() => setDeletingProfile(null)}>
          <div className="modal-container modal-sm">
            <div className="modal-header">
              <h3>Delete Profile</h3>
            </div>
            <div className="modal-body">
              <p>Delete profile <strong>{deletingProfile.name}</strong>? This will also remove generated files.</p>
            </div>
            <div className="modal-footer">
              <button className="modal-btn modal-btn-secondary" onClick={() => setDeletingProfile(null)}>Cancel</button>
              <button className="modal-btn modal-btn-danger" onClick={handleDelete}>Delete</button>
            </div>
          </div>
        </ModalOverlay>
      )}
    </div>
  );
}
