import { useState, useEffect, useCallback } from 'react';
import type { M3UAccount, M3UAccountProfile } from '../types';
import type { M3UProfileCreateRequest } from '../services/api';
import * as api from '../services/api';
import { useAsyncOperation } from '../hooks/useAsyncOperation';
import './M3UProfileModal.css';

interface M3UProfileModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSaved: () => void;
  account: M3UAccount;
}

interface EditingProfile {
  id?: number;
  name: string;
  max_streams: number;
  is_active: boolean;
  is_default?: boolean;
  search_pattern: string;
  replace_pattern: string;
}

const emptyProfile: EditingProfile = {
  name: '',
  max_streams: 1,
  is_active: true,
  search_pattern: '^(.*)$',
  replace_pattern: '$1',
};

export function M3UProfileModal({
  isOpen,
  onClose,
  onSaved,
  account,
}: M3UProfileModalProps) {
  const [profiles, setProfiles] = useState<M3UAccountProfile[]>([]);
  const [editingProfile, setEditingProfile] = useState<EditingProfile | null>(null);
  const [isAddingNew, setIsAddingNew] = useState(false);

  // Separate async operations for loading vs saving
  const { loading, error, execute: executeLoad, setError } = useAsyncOperation<M3UAccountProfile[]>();
  const { loading: saving, execute: executeSave } = useAsyncOperation();

  const loadProfiles = useCallback(async () => {
    const data = await executeLoad(async () => {
      return await api.getM3UProfiles(account.id);
    });
    if (data) {
      setProfiles(data);
    }
  }, [account.id, executeLoad]);

  // Load profiles when modal opens
  useEffect(() => {
    if (isOpen) {
      loadProfiles();
      setEditingProfile(null);
      setIsAddingNew(false);
    }
  }, [isOpen, loadProfiles]);

  const handleAddNew = () => {
    setEditingProfile({ ...emptyProfile, max_streams: account.max_streams });
    setIsAddingNew(true);
  };

  const handleEdit = (profile: M3UAccountProfile) => {
    setEditingProfile({
      id: profile.id,
      name: profile.name,
      max_streams: profile.max_streams,
      is_active: profile.is_active,
      is_default: profile.is_default,
      search_pattern: profile.search_pattern || '^(.*)$',
      replace_pattern: profile.replace_pattern || '$1',
    });
    setIsAddingNew(false);
  };

  const handleCancelEdit = () => {
    setEditingProfile(null);
    setIsAddingNew(false);
  };

  const handleSaveProfile = async () => {
    if (!editingProfile) return;

    if (!editingProfile.name.trim()) {
      setError('Profile name is required');
      return;
    }

    // For non-default profiles, search and replace patterns are required
    if (!editingProfile.is_default) {
      if (!editingProfile.search_pattern.trim()) {
        setError('Search pattern is required for non-default profiles');
        return;
      }
      if (!editingProfile.replace_pattern.trim()) {
        setError('Replace pattern is required for non-default profiles');
        return;
      }
    }

    await executeSave(async () => {
      const profileData: M3UProfileCreateRequest = {
        name: editingProfile.name.trim(),
        max_streams: editingProfile.max_streams,
        is_active: editingProfile.is_active,
        search_pattern: editingProfile.search_pattern.trim(),
        replace_pattern: editingProfile.replace_pattern.trim(),
      };

      if (isAddingNew) {
        await api.createM3UProfile(account.id, profileData);
      } else if (editingProfile.id) {
        // For default profiles, only allow editing name
        const updateData = editingProfile.is_default
          ? { name: editingProfile.name.trim() }
          : profileData;
        await api.updateM3UProfile(account.id, editingProfile.id, updateData);
      }

      await loadProfiles();
      setEditingProfile(null);
      setIsAddingNew(false);
      onSaved();
    });
  };

  const handleDelete = async (profile: M3UAccountProfile) => {
    if (profile.is_default) {
      setError('Cannot delete the default profile');
      return;
    }

    if (!confirm(`Are you sure you want to delete the profile "${profile.name}"?`)) {
      return;
    }

    await executeSave(async () => {
      await api.deleteM3UProfile(account.id, profile.id);
      await loadProfiles();
      onSaved();
    });
  };

  const handleToggleActive = async (profile: M3UAccountProfile) => {
    await executeSave(async () => {
      await api.updateM3UProfile(account.id, profile.id, { is_active: !profile.is_active });
      await loadProfiles();
      onSaved();
    });
  };

  if (!isOpen) return null;

  const isEditingDefault = editingProfile?.is_default === true;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content m3u-profile-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>
            <span className="material-icons">account_circle</span>
            Manage Profiles - {account.name}
          </h2>
          <button className="modal-close" onClick={onClose}>
            <span className="material-icons">close</span>
          </button>
        </div>

        <div className="modal-body">
          {error && (
            <div className="error-message">
              <span className="material-icons">error</span>
              {error}
            </div>
          )}

          {loading ? (
            <div className="loading-state">
              <span className="material-icons spinning">sync</span>
              <p>Loading profiles...</p>
            </div>
          ) : (
            <>
              {/* Profile List */}
              {!editingProfile && (
                <div className="profiles-list">
                  <div className="profiles-header">
                    <h3>Profiles</h3>
                    <button className="btn-primary" onClick={handleAddNew}>
                      <span className="material-icons">add</span>
                      Add Profile
                    </button>
                  </div>

                  {profiles.length === 0 ? (
                    <div className="empty-state">
                      <span className="material-icons">account_circle</span>
                      <p>No profiles found</p>
                    </div>
                  ) : (
                    <div className="profile-cards">
                      {profiles.map((profile) => (
                        <div key={profile.id} className={`profile-card ${profile.is_default ? 'default' : ''} ${!profile.is_active ? 'inactive' : ''}`}>
                          <div className="profile-info">
                            <div className="profile-name">
                              {profile.name}
                              {profile.is_default && (
                                <span className="default-badge" title="Default profile">
                                  <span className="material-icons">star</span>
                                  Default
                                </span>
                              )}
                            </div>
                            <div className="profile-details">
                              <span className="detail-item">
                                <span className="material-icons">stream</span>
                                {profile.max_streams === 0 ? 'Unlimited' : `${profile.max_streams} streams`}
                              </span>
                              {profile.search_pattern && (
                                <span className="detail-item pattern" title={`Pattern: ${profile.search_pattern} → ${profile.replace_pattern}`}>
                                  <span className="material-icons">find_replace</span>
                                  Pattern configured
                                </span>
                              )}
                              <span className={`detail-item status ${profile.is_active ? 'active' : 'inactive'}`}>
                                <span className="material-icons">
                                  {profile.is_active ? 'check_circle' : 'cancel'}
                                </span>
                                {profile.is_active ? 'Active' : 'Inactive'}
                              </span>
                            </div>
                          </div>

                          <div className="profile-actions">
                            <button
                              className="action-btn"
                              onClick={() => handleToggleActive(profile)}
                              title={profile.is_active ? 'Deactivate' : 'Activate'}
                            >
                              <span className="material-icons">
                                {profile.is_active ? 'toggle_on' : 'toggle_off'}
                              </span>
                            </button>
                            <button
                              className="action-btn"
                              onClick={() => handleEdit(profile)}
                              title="Edit"
                            >
                              <span className="material-icons">edit</span>
                            </button>
                            {!profile.is_default && (
                              <button
                                className="action-btn delete"
                                onClick={() => handleDelete(profile)}
                                title="Delete"
                              >
                                <span className="material-icons">delete</span>
                              </button>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {/* Edit/Add Form */}
              {editingProfile && (
                <div className="profile-form">
                  <h3>{isAddingNew ? 'Add New Profile' : 'Edit Profile'}</h3>

                  {isEditingDefault && (
                    <div className="info-message">
                      <span className="material-icons">info</span>
                      Default profiles can only have their name modified. Other settings are managed through the M3U account.
                    </div>
                  )}

                  <div className="form-fields">
                    <label>
                      Profile Name *
                      <input
                        type="text"
                        value={editingProfile.name}
                        onChange={(e) => setEditingProfile({ ...editingProfile, name: e.target.value })}
                        placeholder="e.g., Premium Account, Backup Connection"
                        disabled={saving}
                      />
                    </label>

                    {!isEditingDefault && (
                      <>
                        <label>
                          Maximum Streams
                          <input
                            type="number"
                            min="0"
                            value={editingProfile.max_streams}
                            onChange={(e) => setEditingProfile({ ...editingProfile, max_streams: parseInt(e.target.value, 10) || 0 })}
                            disabled={saving}
                          />
                          <span className="field-hint">Set to 0 for unlimited</span>
                        </label>

                        <label>
                          Search Pattern (Regex) *
                          <input
                            type="text"
                            value={editingProfile.search_pattern}
                            onChange={(e) => setEditingProfile({ ...editingProfile, search_pattern: e.target.value })}
                            placeholder="e.g., ^(.*)$"
                            disabled={saving}
                          />
                          <span className="field-hint">Regular expression to match stream names</span>
                        </label>

                        <label>
                          Replace Pattern *
                          <input
                            type="text"
                            value={editingProfile.replace_pattern}
                            onChange={(e) => setEditingProfile({ ...editingProfile, replace_pattern: e.target.value })}
                            placeholder="e.g., $1"
                            disabled={saving}
                          />
                          <span className="field-hint">Replacement pattern for matched streams</span>
                        </label>
                      </>
                    )}
                  </div>

                  <div className="form-actions">
                    {!isEditingDefault && (
                      <label className="checkbox-label">
                        <input
                          type="checkbox"
                          checked={editingProfile.is_active}
                          onChange={(e) => setEditingProfile({ ...editingProfile, is_active: e.target.checked })}
                          disabled={saving}
                        />
                        Profile is active
                      </label>
                    )}
                    <div className="button-group">
                      <button
                        className="btn-secondary"
                        onClick={handleCancelEdit}
                        disabled={saving}
                      >
                        Cancel
                      </button>
                      <button
                        className="btn-primary"
                        onClick={handleSaveProfile}
                        disabled={saving}
                      >
                        {saving ? 'Saving...' : 'Save Profile'}
                      </button>
                    </div>
                  </div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
