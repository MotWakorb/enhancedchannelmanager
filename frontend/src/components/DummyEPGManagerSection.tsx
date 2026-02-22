import { useState, useEffect, useCallback, memo } from 'react';
import type { DummyEPGProfile, DummyEPGCustomProperties } from '../types';
import * as api from '../services/api';
import { copyToClipboard } from '../utils/clipboard';
import { DummyEPGProfileModal } from './DummyEPGProfileModal';
import { ImportDummyEPGModal } from './ImportDummyEPGModal';
import { useNotifications } from '../contexts/NotificationContext';
import './DummyEPGManagerSection.css';

/** Map an ECM DummyEPGProfile to Dispatcharr custom_properties for an XMLTV EPG source. */
function mapProfileToCustomProperties(profile: DummyEPGProfile): DummyEPGCustomProperties {
  return {
    name_source: profile.name_source,
    stream_index: profile.stream_index,
    title_pattern: profile.title_pattern ?? undefined,
    time_pattern: profile.time_pattern ?? undefined,
    date_pattern: profile.date_pattern ?? undefined,
    title_template: profile.title_template ?? undefined,
    description_template: profile.description_template ?? undefined,
    upcoming_title_template: profile.upcoming_title_template ?? undefined,
    upcoming_description_template: profile.upcoming_description_template ?? undefined,
    ended_title_template: profile.ended_title_template ?? undefined,
    ended_description_template: profile.ended_description_template ?? undefined,
    fallback_title_template: profile.fallback_title_template ?? undefined,
    fallback_description_template: profile.fallback_description_template ?? undefined,
    event_timezone: profile.event_timezone,
    output_timezone: profile.output_timezone ?? undefined,
    program_duration: profile.program_duration,
    categories: profile.categories ?? undefined,
    channel_logo_url: profile.channel_logo_url_template ?? undefined,
    program_poster_url: profile.program_poster_url_template ?? undefined,
    include_date_tag: profile.include_date_tag,
    include_live_tag: profile.include_live_tag,
    include_new_tag: profile.include_new_tag,
  };
}

export const DummyEPGManagerSection = memo(function DummyEPGManagerSection() {
  const notifications = useNotifications();
  const [profiles, setProfiles] = useState<DummyEPGProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [regenerating, setRegenerating] = useState(false);
  const [addingToDispatcharr, setAddingToDispatcharr] = useState<number | 'all' | null>(null);

  // Modal state
  const [profileModalOpen, setProfileModalOpen] = useState(false);
  const [editingProfile, setEditingProfile] = useState<DummyEPGProfile | null>(null);
  const [importModalOpen, setImportModalOpen] = useState(false);
  const [importData, setImportData] = useState<Partial<DummyEPGProfile> | null>(null);

  const loadProfiles = useCallback(async () => {
    try {
      const data = await api.getDummyEPGProfiles();
      setProfiles(data);
    } catch (err) {
      notifications.error('Failed to load Dummy EPG profiles', 'Dummy EPG');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadProfiles();
  }, [loadProfiles]);

  const handleAddProfile = () => {
    setEditingProfile(null);
    setProfileModalOpen(true);
  };

  const handleEditProfile = async (profile: DummyEPGProfile) => {
    try {
      const full = await api.getDummyEPGProfile(profile.id);
      setEditingProfile(full);
      setProfileModalOpen(true);
    } catch (err) {
      notifications.error('Failed to load profile details', 'Dummy EPG');
    }
  };

  const handleDeleteProfile = async (profile: DummyEPGProfile) => {
    if (!confirm(`Delete profile "${profile.name}"?`)) {
      return;
    }
    try {
      await api.deleteDummyEPGProfile(profile.id);
      await loadProfiles();
    } catch (err) {
      notifications.error('Failed to delete profile', 'Dummy EPG');
    }
  };

  const handleToggleEnabled = async (profile: DummyEPGProfile) => {
    try {
      await api.updateDummyEPGProfile(profile.id, { enabled: !profile.enabled });
      setProfiles(prev => prev.map(p =>
        p.id === profile.id ? { ...p, enabled: !p.enabled } : p
      ));
    } catch (err) {
      notifications.error('Failed to update profile', 'Dummy EPG');
    }
  };

  const handleRegenerate = async () => {
    setRegenerating(true);
    try {
      await api.regenerateDummyEPG();
      notifications.success('XMLTV regenerated successfully', 'Dummy EPG');
      await loadProfiles();
    } catch (err) {
      notifications.error('Failed to regenerate XMLTV', 'Dummy EPG');
    } finally {
      setRegenerating(false);
    }
  };

  const handleCopyXmltvUrl = async () => {
    const url = api.getDummyEPGXmltvUrl();
    const ok = await copyToClipboard(url, 'XMLTV URL');
    if (ok) {
      notifications.success('XMLTV URL copied to clipboard', 'Dummy EPG');
    } else {
      notifications.error('Failed to copy URL — check browser permissions', 'Dummy EPG');
    }
  };

  const handleProfileSaved = () => {
    loadProfiles();
  };

  const handleImport = (data: Partial<DummyEPGProfile>) => {
    setImportData(data);
    setEditingProfile(null);
    setProfileModalOpen(true);
  };

  const addProfileToDispatcharr = async (profile: DummyEPGProfile): Promise<boolean> => {
    const full = await api.getDummyEPGProfile(profile.id);
    const url = api.getDummyEPGProfileXmltvUrl(profile.id);
    const customProps = mapProfileToCustomProperties(full);
    await api.createEPGSource({
      name: full.name,
      source_type: 'xmltv',
      url,
      is_active: true,
      custom_properties: customProps,
    });
    return true;
  };

  const handleAddToDispatcharr = async (profile: DummyEPGProfile) => {
    setAddingToDispatcharr(profile.id);
    try {
      await addProfileToDispatcharr(profile);
      notifications.success(`"${profile.name}" added to Dispatcharr`, 'Dummy EPG');
    } catch (err) {
      notifications.error(`Failed to add "${profile.name}" to Dispatcharr`, 'Dummy EPG');
    } finally {
      setAddingToDispatcharr(null);
    }
  };

  const handleAddAllToDispatcharr = async () => {
    const enabled = profiles.filter(p => p.enabled);
    if (enabled.length === 0) {
      notifications.warning('No enabled profiles to add', 'Dummy EPG');
      return;
    }
    setAddingToDispatcharr('all');
    let added = 0;
    let failed = 0;
    for (const profile of enabled) {
      try {
        await addProfileToDispatcharr(profile);
        added++;
      } catch {
        failed++;
      }
    }
    setAddingToDispatcharr(null);
    if (failed === 0) {
      notifications.success(`Added ${added} profile${added !== 1 ? 's' : ''} to Dispatcharr`, 'Dummy EPG');
    } else {
      notifications.warning(`Added ${added}, failed ${failed} profile${failed !== 1 ? 's' : ''}`, 'Dummy EPG');
    }
  };

  if (loading) {
    return (
      <div className="dep-manager-section">
        <div className="dep-manager-header">
          <div className="header-title">
            <h2>ECM Dummy EPG Profiles</h2>
          </div>
        </div>
        <div className="dep-loading">
          <span className="material-icons spinning">sync</span>
          Loading profiles...
        </div>
      </div>
    );
  }

  return (
    <div className="dep-manager-section">
      <div className="dep-manager-header">
        <div className="header-title">
          <h2>ECM Dummy EPG Profiles</h2>
          <p className="header-description">
            Generate EPG data from channel/stream names using regex patterns and substitution rules. Copy the XMLTV URL to add as a source in Dispatcharr.
          </p>
        </div>
        <div className="header-actions">
          {profiles.length > 0 && (
            <>
              <button className="btn-secondary" onClick={handleCopyXmltvUrl} title="Copy combined XMLTV URL">
                <span className="material-icons">content_copy</span>
                XMLTV URL
              </button>
              <button className="btn-secondary" onClick={handleRegenerate} disabled={regenerating}>
                <span className={`material-icons ${regenerating ? 'spinning' : ''}`}>refresh</span>
                {regenerating ? 'Regenerating...' : 'Regenerate'}
              </button>
              <button
                className="btn-secondary"
                onClick={handleAddAllToDispatcharr}
                disabled={addingToDispatcharr !== null}
                title="Add all enabled profiles to Dispatcharr as EPG sources"
              >
                <span className={`material-icons ${addingToDispatcharr === 'all' ? 'spinning' : ''}`}>cloud_upload</span>
                {addingToDispatcharr === 'all' ? 'Adding...' : 'Add All to Dispatcharr'}
              </button>
            </>
          )}
          <button className="btn-secondary" onClick={() => setImportModalOpen(true)}>
            <span className="material-icons">download</span>
            Import from Dispatcharr
          </button>
          <button className="btn-primary" onClick={handleAddProfile}>
            <span className="material-icons">add</span>
            Add Profile
          </button>
        </div>
      </div>

      {profiles.length === 0 ? (
        <div className="dep-empty-state">
          <span className="material-icons">auto_fix_high</span>
          <p>No ECM Dummy EPG profiles. Create one to generate EPG data from channel names using regex patterns and substitution pairs.</p>
        </div>
      ) : (
        <div className="dep-profiles-list">
          {profiles.map(profile => (
            <div key={profile.id} className={`dep-profile-row ${!profile.enabled ? 'inactive' : ''}`}>
              <div className={`dep-profile-status ${profile.enabled ? 'active' : 'disabled'}`}>
                <span className="material-icons">
                  {profile.enabled ? 'check_circle' : 'block'}
                </span>
              </div>

              <div className="dep-profile-info">
                <div className="dep-profile-name">{profile.name}</div>
                <div className="dep-profile-details">
                  <span className="dep-profile-type">ECM Dummy</span>
                  <span className="dep-profile-channels">
                    {profile.group_count ?? 0} group{(profile.group_count ?? 0) !== 1 ? 's' : ''}
                  </span>
                  {profile.substitution_pairs && profile.substitution_pairs.length > 0 && (
                    <span className="dep-profile-subs">
                      {profile.substitution_pairs.length} sub{profile.substitution_pairs.length !== 1 ? 's' : ''}
                    </span>
                  )}
                </div>
              </div>

              <div className="dep-profile-actions">
                <button
                  className="action-btn"
                  onClick={async () => {
                    const url = api.getDummyEPGProfileXmltvUrl(profile.id);
                    const ok = await copyToClipboard(url, 'profile XMLTV URL');
                    if (ok) {
                      notifications.success('Profile XMLTV URL copied', 'Dummy EPG');
                    } else {
                      notifications.error('Failed to copy URL — check browser permissions', 'Dummy EPG');
                    }
                  }}
                  title="Copy profile XMLTV URL"
                >
                  <span className="material-icons">content_copy</span>
                </button>
                <button
                  className={`action-btn toggle ${profile.enabled ? 'active' : ''}`}
                  onClick={() => handleToggleEnabled(profile)}
                  title={profile.enabled ? 'Disable' : 'Enable'}
                >
                  <span className="material-icons">
                    {profile.enabled ? 'toggle_on' : 'toggle_off'}
                  </span>
                </button>
                <button
                  className="action-btn"
                  onClick={() => handleAddToDispatcharr(profile)}
                  disabled={addingToDispatcharr !== null}
                  title="Add to Dispatcharr as EPG source"
                >
                  <span className={`material-icons ${addingToDispatcharr === profile.id ? 'spinning' : ''}`}>
                    publish
                  </span>
                </button>
                <button
                  className="action-btn"
                  onClick={() => handleEditProfile(profile)}
                  title="Edit"
                >
                  <span className="material-icons">edit</span>
                </button>
                <button
                  className="action-btn delete"
                  onClick={() => handleDeleteProfile(profile)}
                  title="Delete"
                >
                  <span className="material-icons">delete</span>
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      <DummyEPGProfileModal
        isOpen={profileModalOpen}
        profile={editingProfile}
        onClose={() => { setProfileModalOpen(false); setImportData(null); }}
        onSave={handleProfileSaved}
        importData={importData}
      />

      <ImportDummyEPGModal
        isOpen={importModalOpen}
        onClose={() => setImportModalOpen(false)}
        onImport={handleImport}
      />

    </div>
  );
});
