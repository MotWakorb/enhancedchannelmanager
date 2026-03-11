import { useState, useMemo } from 'react';
import type { Channel, Logo, MergeChannelsRequest } from '../types';
import { CustomSelect, type SelectOption } from './CustomSelect';
import { ModalOverlay } from './ModalOverlay';
import * as api from '../services/api';
import './ModalBase.css';
import './MergeChannelsModal.css';

export interface MergeChannelsModalProps {
  channels: Channel[];
  logos: Logo[];
  epgData: { id: number; tvg_id: string; name: string; icon_url: string | null; epg_source: number }[];
  epgSources: { id: number; name: string; source_type?: string }[];
  channelGroups: { id: number; name: string }[];
  streamProfiles: { id: number; name: string; is_active: boolean }[];
  streams: { id: number; name: string; m3u_account?: number | null }[];
  onClose: () => void;
  onMerged: () => void;
}

export function MergeChannelsModal({
  channels,
  logos,
  epgData,
  epgSources,
  channelGroups,
  streamProfiles,
  streams,
  onClose,
  onMerged,
}: MergeChannelsModalProps) {
  // Default values from the first selected channel
  const first = channels[0];

  const [name, setName] = useState(first.name);
  const [channelNumber, setChannelNumber] = useState<string>(
    String(
      Math.min(
        ...channels
          .map((c) => c.channel_number)
          .filter((n): n is number => n !== null)
      ) || ''
    )
  );
  const [groupId, setGroupId] = useState<number | null>(first.channel_group_id);
  const [logoId, setLogoId] = useState<number | null>(first.logo_id);
  const [epgDataId, setEpgDataId] = useState<number | null>(first.epg_data_id);
  const [tvgId] = useState<string>(first.tvg_id || '');
  const [streamProfileId, setStreamProfileId] = useState<number | null>(first.stream_profile_id);
  const [merging, setMerging] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Collect all unique streams from selected channels (preserving order)
  const mergedStreams = useMemo(() => {
    const seen = new Set<number>();
    const result: number[] = [];
    for (const ch of channels) {
      for (const sid of ch.streams) {
        if (!seen.has(sid)) {
          seen.add(sid);
          result.push(sid);
        }
      }
    }
    return result;
  }, [channels]);

  // Build EPG options from the selected channels' EPG data
  const epgOptions = useMemo(() => {
    const seen = new Set<number>();
    const options: { id: number; name: string; sourceName: string }[] = [];
    for (const ch of channels) {
      if (ch.epg_data_id && !seen.has(ch.epg_data_id)) {
        seen.add(ch.epg_data_id);
        const epg = epgData.find((e) => e.id === ch.epg_data_id);
        if (epg) {
          const source = epgSources.find((s) => s.id === epg.epg_source);
          options.push({
            id: epg.id,
            name: epg.name,
            sourceName: source?.name || 'Unknown',
          });
        }
      }
    }
    return options;
  }, [channels, epgData, epgSources]);

  // Build logo options from the selected channels
  const logoOptions = useMemo(() => {
    const seen = new Set<number>();
    const options: Logo[] = [];
    for (const ch of channels) {
      if (ch.logo_id && !seen.has(ch.logo_id)) {
        seen.add(ch.logo_id);
        const logo = logos.find((l) => l.id === ch.logo_id);
        if (logo) options.push(logo);
      }
    }
    return options;
  }, [channels, logos]);

  // Group options for CustomSelect
  const groupOptions: SelectOption[] = [
    { value: '', label: 'Uncategorized' },
    ...channelGroups.map((g) => ({ value: String(g.id), label: g.name })),
  ];

  // Stream profile options
  const profileOptions: SelectOption[] = [
    { value: '', label: 'Default' },
    ...streamProfiles
      .filter((p) => p.is_active)
      .map((p) => ({ value: String(p.id), label: p.name })),
  ];

  const handleMerge = async () => {
    setMerging(true);
    setError(null);
    try {
      const parsedNumber = parseFloat(channelNumber);
      const request: MergeChannelsRequest = {
        source_channel_ids: channels.map((c) => c.id),
        target_name: name.trim(),
        target_channel_number: !isNaN(parsedNumber) ? parsedNumber : null,
        target_channel_group_id: groupId,
        target_logo_id: logoId,
        target_tvg_id: tvgId || null,
        target_epg_data_id: epgDataId,
        target_stream_profile_id: streamProfileId,
      };
      await api.mergeChannels(request);
      onMerged();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Merge failed');
    } finally {
      setMerging(false);
    }
  };

  return (
    <ModalOverlay onClose={onClose}>
      <div className="modal-container modal-lg merge-channels-modal">
        <div className="modal-header">
          <h2>Merge {channels.length} Channels</h2>
          <button className="modal-close-btn" onClick={onClose} title="Close">
            <span className="material-icons">close</span>
          </button>
        </div>

        <div className="modal-body">
          {error && (
            <div className="modal-error-banner">
              <span className="material-icons">error</span>
              {error}
            </div>
          )}

          {/* Source channels list */}
          <div className="modal-form-group">
            <label>Merging channels:</label>
            <div className="merge-source-list">
              {channels.map((ch) => (
                <div key={ch.id} className="merge-source-item">
                  <span className="merge-source-name">{ch.name}</span>
                  <span className="merge-source-streams">
                    {ch.streams.length} stream{ch.streams.length !== 1 ? 's' : ''}
                  </span>
                </div>
              ))}
            </div>
          </div>

          {/* Channel name */}
          <div className="modal-form-group">
            <label>Channel Name</label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Enter channel name..."
            />
          </div>

          {/* Channel number */}
          <div className="modal-form-group">
            <label>Channel Number</label>
            <input
              type="text"
              className="merge-input-short"
              value={channelNumber}
              onChange={(e) => setChannelNumber(e.target.value)}
              placeholder="123"
            />
          </div>

          {/* Group selector */}
          <div className="modal-form-group">
            <label>Channel Group</label>
            <CustomSelect
              options={groupOptions}
              value={groupId != null ? String(groupId) : ''}
              onChange={(val) => setGroupId(val ? parseInt(val) : null)}
              searchable
              searchPlaceholder="Search groups..."
            />
          </div>

          {/* EPG selection */}
          {epgOptions.length > 0 && (
            <div className="modal-form-group">
              <label>EPG Data</label>
              <div className="merge-radio-group">
                <label className="modal-checkbox-row">
                  <input
                    type="radio"
                    name="epg"
                    checked={epgDataId === null}
                    onChange={() => setEpgDataId(null)}
                  />
                  None
                </label>
                {epgOptions.map((epg) => (
                  <label key={epg.id} className="modal-checkbox-row">
                    <input
                      type="radio"
                      name="epg"
                      checked={epgDataId === epg.id}
                      onChange={() => setEpgDataId(epg.id)}
                    />
                    {epg.name}
                    <span className="form-hint">({epg.sourceName})</span>
                  </label>
                ))}
              </div>
            </div>
          )}

          {/* Logo selection */}
          {logoOptions.length > 0 && (
            <div className="modal-form-group">
              <label>Logo</label>
              <div className="merge-radio-group">
                <label className="modal-checkbox-row">
                  <input
                    type="radio"
                    name="logo"
                    checked={logoId === null}
                    onChange={() => setLogoId(null)}
                  />
                  None
                </label>
                {logoOptions.map((logo) => (
                  <label key={logo.id} className="modal-checkbox-row">
                    <input
                      type="radio"
                      name="logo"
                      checked={logoId === logo.id}
                      onChange={() => setLogoId(logo.id)}
                    />
                    <img
                      src={logo.url}
                      alt={logo.name}
                      className="merge-logo-preview"
                      onError={(e) => {
                        (e.target as HTMLImageElement).style.display = 'none';
                      }}
                    />
                    {logo.name}
                  </label>
                ))}
              </div>
            </div>
          )}

          {/* Stream profile */}
          <div className="modal-form-group">
            <label>Stream Profile</label>
            <CustomSelect
              options={profileOptions}
              value={streamProfileId != null ? String(streamProfileId) : ''}
              onChange={(val) => setStreamProfileId(val ? parseInt(val) : null)}
            />
          </div>

          {/* Merged streams preview */}
          <div className="modal-form-group">
            <label>Merged Streams ({mergedStreams.length})</label>
            <div className="merge-streams-preview">
              {mergedStreams.map((sid) => {
                const stream = streams.find((s) => s.id === sid);
                return (
                  <div key={sid} className="merge-stream-item">
                    <span className="material-icons merge-stream-icon">play_circle</span>
                    <span className="merge-stream-name">
                      {stream?.name || `Stream #${sid}`}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        </div>

        <div className="modal-footer">
          <button className="modal-btn modal-btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button
            className="modal-btn modal-btn-primary"
            onClick={handleMerge}
            disabled={merging || !name.trim()}
          >
            {merging ? 'Merging...' : `Merge ${channels.length} Channels`}
          </button>
        </div>
      </div>
    </ModalOverlay>
  );
}
