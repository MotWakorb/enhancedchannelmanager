import { useState, useEffect } from 'react';
import type { PlaylistProfile, ProfileCreateRequest, SelectionMode, StreamUrlMode, SortOrder } from '../../types/export';
import * as exportApi from '../../services/exportApi';
import { useNotifications } from '../../contexts/NotificationContext';
import { ModalOverlay } from '../ModalOverlay';
import { CustomSelect } from '../CustomSelect';
import { ChannelSelector } from './ChannelSelector';
import { ExportPreview } from './ExportPreview';
import '../ModalBase.css';

interface PlaylistProfileEditorProps {
  profile: PlaylistProfile | null; // null = create mode
  onClose: () => void;
  onSaved: () => void;
}

const SELECTION_MODE_OPTIONS = [
  { value: 'all', label: 'All Channels' },
  { value: 'groups', label: 'By Group' },
  { value: 'channels', label: 'By Channel' },
];

const STREAM_URL_OPTIONS = [
  { value: 'direct', label: 'Direct URL' },
  { value: 'proxy', label: 'Proxy URL' },
];

const SORT_OPTIONS = [
  { value: 'name', label: 'By Name' },
  { value: 'number', label: 'By Number' },
  { value: 'group', label: 'By Group' },
];

export function PlaylistProfileEditor({ profile, onClose, onSaved }: PlaylistProfileEditorProps) {
  const notifications = useNotifications();
  const [saving, setSaving] = useState(false);

  // Form state
  const [name, setName] = useState(profile?.name || '');
  const [description, setDescription] = useState(profile?.description || '');
  const [selectionMode, setSelectionMode] = useState<SelectionMode>(profile?.selection_mode || 'all');
  const [selectedGroups, setSelectedGroups] = useState<number[]>(profile?.selected_groups || []);
  const [selectedChannels, setSelectedChannels] = useState<number[]>(profile?.selected_channels || []);
  const [streamUrlMode, setStreamUrlMode] = useState<StreamUrlMode>(profile?.stream_url_mode || 'direct');
  const [includeLogos, setIncludeLogos] = useState(profile?.include_logos ?? true);
  const [includeEpgIds, setIncludeEpgIds] = useState(profile?.include_epg_ids ?? true);
  const [includeChannelNumbers, setIncludeChannelNumbers] = useState(profile?.include_channel_numbers ?? true);
  const [sortOrder, setSortOrder] = useState<SortOrder>(profile?.sort_order || 'number');
  const [filenamePrefix, setFilenamePrefix] = useState(profile?.filename_prefix || 'playlist');

  const handleSave = async () => {
    if (!name.trim()) {
      notifications.error('Name is required');
      return;
    }
    if (!/^[a-zA-Z0-9_-]+$/.test(filenamePrefix)) {
      notifications.error('Filename prefix must be alphanumeric, hyphens, or underscores only');
      return;
    }

    setSaving(true);
    try {
      const data: ProfileCreateRequest = {
        name: name.trim(),
        description: description.trim() || undefined,
        selection_mode: selectionMode,
        selected_groups: selectionMode === 'groups' ? selectedGroups : [],
        selected_channels: selectionMode === 'channels' ? selectedChannels : [],
        stream_url_mode: streamUrlMode,
        include_logos: includeLogos,
        include_epg_ids: includeEpgIds,
        include_channel_numbers: includeChannelNumbers,
        sort_order: sortOrder,
        filename_prefix: filenamePrefix,
      };

      if (profile) {
        await exportApi.updateProfile(profile.id, data);
        notifications.success(`Profile '${name}' updated`);
      } else {
        await exportApi.createProfile(data);
        notifications.success(`Profile '${name}' created`);
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
          <h3>{profile ? 'Edit Profile' : 'New Export Profile'}</h3>
          <button className="modal-close-btn" onClick={onClose}>
            <span className="material-icons">close</span>
          </button>
        </div>
        <div className="modal-body">
            <div className="modal-form-group">
              <label>Name</label>
              <input
                type="text"
                value={name}
                onChange={e => setName(e.target.value)}
                placeholder="My Export Profile"
              />
            </div>
            <div className="modal-form-group">
              <label>Description</label>
              <input
                type="text"
                value={description}
                onChange={e => setDescription(e.target.value)}
                placeholder="Optional description"
              />
            </div>
            <div className="modal-form-group">
              <label>Channel Selection</label>
              <CustomSelect
                value={selectionMode}
                onChange={(val) => setSelectionMode(val as SelectionMode)}
                options={SELECTION_MODE_OPTIONS}
              />
            </div>

            {selectionMode !== 'all' && (
              <div className="modal-form-group">
                <ChannelSelector
                  selectionMode={selectionMode}
                  selectedGroups={selectedGroups}
                  selectedChannels={selectedChannels}
                  onGroupsChange={setSelectedGroups}
                  onChannelsChange={setSelectedChannels}
                />
              </div>
            )}

            <div className="modal-form-group">
              <label>Stream URL Mode</label>
              <CustomSelect
                value={streamUrlMode}
                onChange={(val) => setStreamUrlMode(val as StreamUrlMode)}
                options={STREAM_URL_OPTIONS}
              />
            </div>
            <div className="modal-form-group">
              <label>Sort Order</label>
              <CustomSelect
                value={sortOrder}
                onChange={(val) => setSortOrder(val as SortOrder)}
                options={SORT_OPTIONS}
              />
            </div>
            <div className="modal-form-group">
              <label>Filename Prefix</label>
              <input
                type="text"
                value={filenamePrefix}
                onChange={e => setFilenamePrefix(e.target.value)}
                placeholder="playlist"
              />
            </div>

            <div className="export-form-checkboxes">
              <label className="modal-checkbox-label">
                <input
                  type="checkbox"
                  checked={includeLogos}
                  onChange={e => setIncludeLogos(e.target.checked)}
                />
                Include Logos
              </label>
              <label className="modal-checkbox-label">
                <input
                  type="checkbox"
                  checked={includeEpgIds}
                  onChange={e => setIncludeEpgIds(e.target.checked)}
                />
                Include EPG IDs
              </label>
              <label className="modal-checkbox-label">
                <input
                  type="checkbox"
                  checked={includeChannelNumbers}
                  onChange={e => setIncludeChannelNumbers(e.target.checked)}
                />
                Include Channel Numbers
              </label>
            </div>

            {profile && (
              <ExportPreview profileId={profile.id} />
            )}
        </div>
        <div className="modal-footer">
          <button className="modal-btn modal-btn-secondary" onClick={onClose}>Cancel</button>
          <button className="modal-btn modal-btn-primary" onClick={handleSave} disabled={saving}>
            {saving ? 'Saving...' : profile ? 'Save Changes' : 'Create Profile'}
          </button>
        </div>
      </div>
    </ModalOverlay>
  );
}
