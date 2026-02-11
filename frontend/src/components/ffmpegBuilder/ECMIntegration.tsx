import { useState, useEffect, useCallback } from 'react';
import type { FFMPEGChannelProfile } from '../../types/ffmpegBuilder';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface SavedConfig {
  id: number;
  name: string;
}

interface ECMIntegrationProps {
  profiles: FFMPEGChannelProfile[];
  onProfileCreate: (profile: Omit<FFMPEGChannelProfile, 'id'>) => void;
  onProfileUpdate: (profile: FFMPEGChannelProfile) => void;
  onProfileDelete: (id: number) => void;
  onSelectProfile: (profile: FFMPEGChannelProfile) => void;
}

// ---------------------------------------------------------------------------
// Tooltips
// ---------------------------------------------------------------------------

const FIELD_TOOLTIPS: Record<string, string> = {
  applyTo: 'Choose how this profile is applied: to all channels, a specific group, or a specific channel.',
  profiles: 'FFMPEG channel profiles define encoding settings applied to streams when they are processed.',
};

// ---------------------------------------------------------------------------
// InfoIcon
// ---------------------------------------------------------------------------

function InfoIcon({ tooltip }: { tooltip: string }) {
  const [show, setShow] = useState(false);
  return (
    <span
      data-testid="info-icon"
      className="info-icon"
      onMouseEnter={() => setShow(true)}
      onMouseLeave={() => setShow(false)}
    >
      {'\u24D8'}
      {show && <div role="tooltip" className="tooltip">{tooltip}</div>}
    </span>
  );
}

// ---------------------------------------------------------------------------
// ECMIntegration component
// ---------------------------------------------------------------------------

export function ECMIntegration({
  profiles,
  onProfileCreate,
  onProfileUpdate,
  onProfileDelete,
  onSelectProfile,
}: ECMIntegrationProps) {
  const [configs, setConfigs] = useState<SavedConfig[]>([]);
  const [selectedProfileId, setSelectedProfileId] = useState<number | null>(
    profiles.length > 0 ? profiles[0].id : null
  );
  const [profileSelectOpen, setProfileSelectOpen] = useState(false);
  const [applyToOpen, setApplyToOpen] = useState(false);

  // Edit/Create state
  const [editing, setEditing] = useState(false);
  const [creating, setCreating] = useState(false);
  const [editName, setEditName] = useState('');
  const [editDirty, setEditDirty] = useState(false);

  // Confirm delete
  const [confirmDeleteId, setConfirmDeleteId] = useState<number | null>(null);

  const selectedProfile = profiles.find(p => p.id === selectedProfileId) ?? profiles[0] ?? null;

  // Fetch configs for name resolution
  useEffect(() => {
    fetch('/api/ffmpeg/configs')
      .then(res => res.json())
      .then(data => {
        setConfigs((data.configs || []).map((c: SavedConfig) => ({ id: c.id, name: c.name })));
      })
      .catch(() => {});
  }, []);

  // Sync selectedProfileId when profiles change
  useEffect(() => {
    if (profiles.length > 0 && !profiles.find(p => p.id === selectedProfileId)) {
      setSelectedProfileId(profiles[0].id);
    }
  }, [profiles, selectedProfileId]);

  const getConfigName = useCallback((configId: number) => {
    return configs.find(c => c.id === configId)?.name || `Config #${configId}`;
  }, [configs]);

  const handleSelectProfile = (e: React.MouseEvent, profile: FFMPEGChannelProfile) => {
    e.stopPropagation();
    setSelectedProfileId(profile.id);
    setProfileSelectOpen(false);
    setEditing(false);
    setCreating(false);
    setEditDirty(false);
    onSelectProfile(profile);
  };

  const handleApplyToChange = (e: React.MouseEvent, applyTo: 'all' | 'group' | 'channel') => {
    e.stopPropagation();
    if (selectedProfile) {
      onProfileUpdate({ ...selectedProfile, applyTo });
    }
    setApplyToOpen(false);
  };

  const handleEnableToggle = () => {
    if (selectedProfile) {
      onProfileUpdate({ ...selectedProfile, enabled: !selectedProfile.enabled });
    }
  };

  const handleStartEdit = () => {
    if (selectedProfile) {
      setEditName(selectedProfile.name);
      setEditing(true);
      setEditDirty(false);
    }
  };

  const handleEditNameChange = (name: string) => {
    setEditName(name);
    setEditDirty(true);
  };

  const handleSaveEdit = () => {
    if (selectedProfile) {
      onProfileUpdate({ ...selectedProfile, name: editName });
      setEditing(false);
      setEditDirty(false);
    }
  };

  const handleStartCreate = () => {
    setCreating(true);
    setEditing(false);
    setEditName('');
    setEditDirty(false);
  };

  const handleSaveCreate = () => {
    onProfileCreate({
      name: editName,
      configId: configs[0]?.id ?? 0,
      applyTo: 'all',
      enabled: true,
    });
    setCreating(false);
    setEditName('');
    setEditDirty(false);
  };

  const handleDelete = (id: number) => {
    setConfirmDeleteId(id);
  };

  const handleConfirmDelete = () => {
    if (confirmDeleteId !== null) {
      onProfileDelete(confirmDeleteId);
      setConfirmDeleteId(null);
    }
  };

  const applyToLabel = selectedProfile
    ? selectedProfile.applyTo === 'all' ? 'All Channels'
      : selectedProfile.applyTo === 'group' ? 'Group'
      : 'Channel'
    : 'All Channels';

  return (
    <div className="ecm-integration">
      {/* Info icon for profiles */}
      <InfoIcon tooltip={FIELD_TOOLTIPS.profiles} />

      {/* Profile Selector — outer div clickable to toggle dropdown */}
      <div
        data-testid="profile-select"
        className="profile-select"
        onClick={() => setProfileSelectOpen(!profileSelectOpen)}
      >
        <div
          role="combobox"
          aria-label="Profile"
          aria-expanded={profileSelectOpen}
          className="dropdown-trigger"
          title={selectedProfile?.name || 'Select Profile...'}
        >
          {selectedProfile?.name || 'Select Profile...'}
        </div>
        {profileSelectOpen && (
          <div role="listbox" className="dropdown-list">
            {profiles.map(p => (
              <div
                key={p.id}
                role="option"
                aria-selected={p.id === selectedProfileId}
                className={`dropdown-option${p.id === selectedProfileId ? ' selected' : ''}`}
                onClick={(e) => handleSelectProfile(e, p)}
              >
                {p.name}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Profile List — hidden when profile dropdown is open */}
      {!profileSelectOpen && (
        <div className="profile-list">
          {profiles.map(p => (
            <div key={p.id} className={`profile-item${p.id === selectedProfileId ? ' selected' : ''}`}>
              <span className="profile-name">{p.name}</span>
              {p.enabled && <span className="profile-badge active">Active</span>}
              {!p.enabled && <span className="profile-badge inactive">Inactive</span>}
              {selectedProfile && p.id === selectedProfile.id && (
                <span className="profile-config">{getConfigName(p.configId)}</span>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Actions for selected profile — hidden during editing/creating and confirm dialog */}
      {selectedProfile && !editing && !creating && confirmDeleteId === null && (
        <div className="profile-actions">
          <button className="btn-secondary" onClick={handleStartCreate}>Add New</button>
          <button className="btn-secondary" onClick={handleStartEdit}>Edit</button>
          <button className="btn-danger" onClick={() => handleDelete(selectedProfile.id)}>Delete</button>

          {/* Enable/Disable Toggle */}
          <label className="enable-toggle">
            <input
              type="checkbox"
              aria-label="Enable"
              checked={selectedProfile.enabled}
              onChange={handleEnableToggle}
            />
          </label>
        </div>
      )}

      {/* No profiles — show create button */}
      {profiles.length === 0 && !creating && (
        <div className="no-profiles">
          <button className="btn-primary" onClick={handleStartCreate}>Add New</button>
        </div>
      )}

      {/* Edit/Create Form */}
      {(editing || creating) && (
        <div className="profile-form">
          <div className="form-group">
            <label htmlFor="profile-name-input">Profile Name</label>
            <input
              id="profile-name-input"
              type="text"
              value={editName}
              onChange={e => handleEditNameChange(e.target.value)}
            />
          </div>

          {editDirty && <div className="unsaved-indicator">Unsaved changes</div>}

          <div className="form-actions">
            <button className="btn-cancel" onClick={() => { setEditing(false); setCreating(false); setEditDirty(false); }}>
              Cancel
            </button>
            <button className="btn-primary" onClick={editing ? handleSaveEdit : handleSaveCreate}>
              Save
            </button>
          </div>
        </div>
      )}

      {/* Apply-To Selector — outer div clickable to toggle dropdown */}
      <div
        data-testid="apply-to-select"
        className="apply-to-select"
        onClick={() => setApplyToOpen(!applyToOpen)}
      >
        <InfoIcon tooltip={FIELD_TOOLTIPS.applyTo} />
        <div
          role="combobox"
          aria-label="Apply To"
          aria-expanded={applyToOpen}
          className="dropdown-trigger"
          title={applyToLabel}
        >
          {applyToLabel}
        </div>
        {applyToOpen && (
          <div role="listbox" className="dropdown-list">
            <div
              role="option"
              aria-selected={selectedProfile?.applyTo === 'all'}
              onClick={(e) => handleApplyToChange(e, 'all')}
            >
              All
            </div>
            <div
              role="option"
              aria-selected={selectedProfile?.applyTo === 'group'}
              onClick={(e) => handleApplyToChange(e, 'group')}
            >
              Group
            </div>
            <div
              role="option"
              aria-selected={selectedProfile?.applyTo === 'channel'}
              onClick={(e) => handleApplyToChange(e, 'channel')}
            >
              Channel
            </div>
          </div>
        )}
      </div>

      {/* Channel/Group Selector — hidden when apply-to dropdown is open */}
      {!applyToOpen && selectedProfile?.applyTo === 'channel' && (
        <div data-testid="channel-select" className="channel-select">
          <span>Select channels to apply this profile to</span>
        </div>
      )}
      {!applyToOpen && selectedProfile?.applyTo === 'group' && (
        <div data-testid="group-select" className="group-select">
          <span>Select groups to apply this profile to</span>
        </div>
      )}
      {!applyToOpen && (!selectedProfile || selectedProfile.applyTo === 'all') && (
        <div data-testid="channel-select" className="channel-select">
          <span>Profile applies to all channels</span>
        </div>
      )}

      {/* Delete Confirmation */}
      {confirmDeleteId !== null && (
        <div className="confirm-dialog" role="dialog" aria-label="Confirm Delete">
          <p>Delete this profile?</p>
          <button className="btn-cancel" onClick={() => setConfirmDeleteId(null)}>Cancel</button>
          <button className="btn-danger" onClick={handleConfirmDelete}>Confirm</button>
        </div>
      )}
    </div>
  );
}
