import { useState } from 'react';
import type { FFMPEGBuilderState } from '../../types/ffmpegBuilder';
import { SavedProfile, IPTV_PRESETS } from './iptvPresets';

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface IPTVPresetBarProps {
  activePresetId?: string;
  onApply: (config: FFMPEGBuilderState, presetId: string) => void;
  savedProfiles: SavedProfile[];
  onSaveProfile: (name: string) => void;
  onDeleteProfile: (id: number) => void;
}

export function IPTVPresetBar({ activePresetId, onApply, savedProfiles, onSaveProfile, onDeleteProfile }: IPTVPresetBarProps) {
  const [showSaveDialog, setShowSaveDialog] = useState(false);
  const [saveName, setSaveName] = useState('');

  const handleSave = () => {
    const name = saveName.trim();
    if (!name) return;
    onSaveProfile(name);
    setSaveName('');
    setShowSaveDialog(false);
  };

  const handleSaveKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') handleSave();
    if (e.key === 'Escape') { setShowSaveDialog(false); setSaveName(''); }
  };

  return (
    <div data-testid="iptv-preset-bar" className="iptv-preset-bar-container">
      {/* Built-in presets row */}
      <div className="iptv-preset-bar">
        <span className="material-icons iptv-preset-bar-icon">bolt</span>
        <span className="iptv-preset-bar-label">Quick Start</span>
        <div className="iptv-preset-buttons">
          {IPTV_PRESETS.map((preset) => (
            <button
              key={preset.id}
              type="button"
              data-testid={`iptv-preset-${preset.id}`}
              className={`iptv-preset-btn${activePresetId === preset.id ? ' active' : ''}`}
              aria-pressed={activePresetId === preset.id}
              title={preset.description}
              onClick={() => onApply(preset.config, preset.id)}
            >
              <span className="material-icons">{preset.icon}</span>
              {preset.name}
            </button>
          ))}
        </div>
      </div>

      {/* Saved profiles row */}
      <div className="iptv-preset-bar saved-profiles-bar">
        <span className="material-icons iptv-preset-bar-icon">folder</span>
        <span className="iptv-preset-bar-label">My Profiles</span>
        <div className="iptv-preset-buttons">
          {savedProfiles.map((profile) => {
            const profileKey = `profile-${profile.id}`;
            return (
              <div key={profile.id} className={`saved-profile-chip${activePresetId === profileKey ? ' active' : ''}`}>
                <button
                  type="button"
                  data-testid={`saved-profile-${profile.id}`}
                  className="saved-profile-load"
                  aria-pressed={activePresetId === profileKey}
                  title={`Load "${profile.name}"`}
                  onClick={() => onApply(profile.config, profileKey)}
                >
                  <span className="material-icons">person</span>
                  {profile.name}
                </button>
                <button
                  type="button"
                  data-testid={`delete-profile-${profile.id}`}
                  className="saved-profile-delete"
                  aria-label={`Delete ${profile.name}`}
                  title={`Delete "${profile.name}"`}
                  onClick={(e) => { e.stopPropagation(); onDeleteProfile(profile.id); }}
                >
                  <span className="material-icons">close</span>
                </button>
              </div>
            );
          })}

          {/* Save button / inline dialog */}
          {showSaveDialog ? (
            <div className="save-profile-inline">
              <input
                type="text"
                data-testid="save-profile-name"
                className="save-profile-input"
                placeholder="Profile name..."
                value={saveName}
                onChange={(e) => setSaveName(e.target.value)}
                onKeyDown={handleSaveKeyDown}
                autoFocus
              />
              <button
                type="button"
                data-testid="save-profile-confirm"
                className="save-profile-confirm"
                onClick={handleSave}
                disabled={!saveName.trim()}
                title="Save profile"
              >
                <span className="material-icons">check</span>
              </button>
              <button
                type="button"
                className="save-profile-cancel"
                onClick={() => { setShowSaveDialog(false); setSaveName(''); }}
                title="Cancel"
              >
                <span className="material-icons">close</span>
              </button>
            </div>
          ) : (
            <button
              type="button"
              data-testid="save-profile-btn"
              className="iptv-preset-btn save-profile-btn"
              title="Save current settings as a profile"
              onClick={() => setShowSaveDialog(true)}
            >
              <span className="material-icons">add</span>
              Save Profile
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export { IPTV_PRESETS };
