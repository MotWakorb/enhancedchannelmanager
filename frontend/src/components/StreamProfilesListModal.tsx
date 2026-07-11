import { useState } from 'react';
import type { StreamProfile } from '../types';
import * as api from '../services/api';
import { useNotifications } from '../contexts/NotificationContext';
import { ModalOverlay } from './ModalOverlay';
import './ModalBase.css';
import './StreamProfilesListModal.css';

interface StreamProfilesListModalProps {
  streamProfiles: StreamProfile[];
  onClose: () => void;
  /** Called after a successful create so the caller can refresh its own streamProfiles list. */
  onChanged: () => void;
}

/**
 * List stream profiles and create a new one (enhancedchannelmanager-hq3de.j),
 * following ChannelProfilesListModal's list pattern. Read-only list — no
 * edit/delete here since only POST /api/stream-profiles was in scope
 * (unlike channel profiles, ECM has no PATCH/DELETE stream-profile
 * endpoints wired at all yet; editing/deleting remain Dispatcharr-side
 * operations outside this bead).
 */
export function StreamProfilesListModal({ streamProfiles, onClose, onChanged }: StreamProfilesListModalProps) {
  const notifications = useNotifications();
  const [name, setName] = useState('');
  const [command, setCommand] = useState('');
  const [parameters, setParameters] = useState('');
  const [isActive, setIsActive] = useState(true);
  const [creating, setCreating] = useState(false);
  const [showCreateForm, setShowCreateForm] = useState(false);

  const canCreate = name.trim() && command.trim();

  const handleCreate = async () => {
    if (!canCreate) return;
    setCreating(true);
    try {
      await api.createStreamProfile({
        name: name.trim(),
        command: command.trim(),
        parameters: parameters.trim(),
        is_active: isActive,
      });
      notifications.success(`Created stream profile "${name.trim()}"`, 'Stream Profiles');
      setName('');
      setCommand('');
      setParameters('');
      setIsActive(true);
      setShowCreateForm(false);
      onChanged();
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Failed to create stream profile', 'Stream Profiles');
    } finally {
      setCreating(false);
    }
  };

  return (
    <ModalOverlay onClose={onClose}>
      <div className="modal-container modal-md stream-profiles-modal">
        <div className="modal-header">
          <h2 className="modal-title">Stream Profiles</h2>
          <button className="modal-close-btn" onClick={onClose} aria-label="Close" title="Close">
            <span className="material-icons" aria-hidden="true">close</span>
          </button>
        </div>

        <div className="modal-body">
          {streamProfiles.length === 0 ? (
            <div className="modal-empty-state">
              <p>No stream profiles yet. Create one below.</p>
            </div>
          ) : (
            <div className="stream-profiles-list">
              {streamProfiles.map((profile) => (
                <div key={profile.id} className="stream-profile-row">
                  <div className="stream-profile-info">
                    <span className="stream-profile-name">{profile.name}</span>
                    <span className="stream-profile-command" title={`${profile.command} ${profile.parameters}`}>
                      {profile.command}
                    </span>
                  </div>
                  <span className={`badge badge-sm ${profile.is_active ? 'badge-success' : ''}`}>
                    {profile.is_active ? 'Active' : 'Inactive'}
                  </span>
                </div>
              ))}
            </div>
          )}

          {showCreateForm ? (
            <div className="stream-profile-create-form">
              <div className="modal-form-group">
                <label>Name</label>
                <input
                  type="text"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g., FFmpeg Transcode"
                  autoFocus
                />
              </div>
              <div className="modal-form-group">
                <label>Command</label>
                <input
                  type="text"
                  value={command}
                  onChange={(e) => setCommand(e.target.value)}
                  placeholder="e.g., ffmpeg"
                />
              </div>
              <div className="modal-form-group">
                <label>Parameters</label>
                <input
                  type="text"
                  value={parameters}
                  onChange={(e) => setParameters(e.target.value)}
                  placeholder="e.g., -hide_banner -i {streamUrl} ..."
                />
                <span className="form-hint">Dispatcharr's ffmpeg/streamlink parameter template.</span>
              </div>
              <label className="modal-checkbox-label">
                <input
                  type="checkbox"
                  checked={isActive}
                  onChange={(e) => setIsActive(e.target.checked)}
                />
                Active
              </label>
              <div className="stream-profile-create-actions">
                <button
                  className="modal-btn-secondary"
                  onClick={() => setShowCreateForm(false)}
                  disabled={creating}
                >
                  Cancel
                </button>
                <button
                  className="modal-btn-primary"
                  onClick={handleCreate}
                  disabled={!canCreate || creating}
                >
                  {creating ? 'Creating...' : 'Create Profile'}
                </button>
              </div>
            </div>
          ) : (
            <button className="modal-btn-primary" onClick={() => setShowCreateForm(true)}>
              <span className="material-icons">add</span>
              New Stream Profile
            </button>
          )}
        </div>

        <div className="modal-footer">
          <button className="modal-btn-secondary" onClick={onClose}>Close</button>
        </div>
      </div>
    </ModalOverlay>
  );
}
