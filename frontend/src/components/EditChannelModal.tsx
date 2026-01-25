import { useState, useRef, useEffect, memo } from 'react';
import type { Channel, Logo } from '../types';
import * as api from '../services/api';
import './ModalBase.css';

export interface ChannelMetadataChanges {
  channel_number?: number;
  name?: string;
  logo_id?: number | null;
  tvg_id?: string | null;
  tvc_guide_stationid?: string | null;
  epg_data_id?: number | null;
  stream_profile_id?: number | null;
}

export interface EditChannelModalProps {
  channel: Channel;
  logos: Logo[];
  epgData: { id: number; tvg_id: string; name: string; icon_url: string | null; epg_source: number }[];
  epgSources: { id: number; name: string; source_type?: string }[];
  streamProfiles: { id: number; name: string; is_active: boolean }[];
  onClose: () => void;
  onSave: (changes: ChannelMetadataChanges) => Promise<void>;
  onLogoCreate: (url: string) => Promise<Logo>;
  onLogoUpload: (file: File) => Promise<Logo>;
  epgDataLoading?: boolean;
}

export const EditChannelModal = memo(function EditChannelModal({
  channel,
  logos,
  epgData,
  epgSources,
  streamProfiles,
  onClose,
  onSave,
  onLogoCreate,
  onLogoUpload,
  epgDataLoading,
}: EditChannelModalProps) {
  // Create a map for quick EPG source name lookup
  const epgSourceMap = new Map(epgSources.map((s) => [s.id, s.name]));

  // Channel basic info state
  const [channelNumber, setChannelNumber] = useState<string>(String(channel.channel_number));
  const [channelName, setChannelName] = useState<string>(channel.name);

  // Logo state
  const [selectedLogoId, setSelectedLogoId] = useState<number | null>(channel.logo_id);
  const [logoSearch, setLogoSearch] = useState('');
  const [newLogoUrl, setNewLogoUrl] = useState('');
  const [addingLogo, setAddingLogo] = useState(false);
  const [uploadingLogo, setUploadingLogo] = useState(false);
  const [addingEpgLogo, setAddingEpgLogo] = useState(false);
  const [pendingLogo, setPendingLogo] = useState<Logo | null>(null);
  const [immediateLogoUrl, setImmediateLogoUrl] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const epgDropdownRef = useRef<HTMLDivElement>(null);

  // Metadata state
  const [tvgId, setTvgId] = useState<string>(channel.tvg_id || '');
  const [tvcGuideStationId, setTvcGuideStationId] = useState<string>(channel.tvc_guide_stationid || '');
  const [selectedEpgDataId, setSelectedEpgDataId] = useState<number | null>(channel.epg_data_id);
  const [selectedStreamProfileId, setSelectedStreamProfileId] = useState<number | null>(channel.stream_profile_id);

  // EPG search state
  const [epgSearch, setEpgSearch] = useState('');
  const [epgDropdownOpen, setEpgDropdownOpen] = useState(false);

  // EPG source filter state - IDs of sources to search (empty = all non-dummy sources)
  const [selectedEpgSourceIds, setSelectedEpgSourceIds] = useState<Set<number>>(new Set());
  const [epgSourceFilterOpen, setEpgSourceFilterOpen] = useState(false);
  const epgSourceFilterRef = useRef<HTMLDivElement>(null);

  // TVG-ID from EPG picker state
  const [tvgIdPickerOpen, setTvgIdPickerOpen] = useState(false);
  const [tvgIdSearch, setTvgIdSearch] = useState('');

  // Stream Profile dropdown state
  const [streamProfileDropdownOpen, setStreamProfileDropdownOpen] = useState(false);
  const streamProfileDropdownRef = useRef<HTMLDivElement>(null);

  // Filter EPG sources to exclude dummy types
  const nonDummyEpgSources = epgSources.filter(s => s.source_type !== 'dummy');

  const [saving, setSaving] = useState(false);
  const [showDiscardConfirm, setShowDiscardConfirm] = useState(false);

  // LCN fetch state
  const [fetchingLcn, setFetchingLcn] = useState(false);
  const [lcnError, setLcnError] = useState<string | null>(null);

  // Filter logos by search term
  const filteredLogos = logos.filter((logo) =>
    logo.name.toLowerCase().includes(logoSearch.toLowerCase())
  );

  // Get currently selected logo (use pendingLogo if not yet in logos array)
  const currentLogo = selectedLogoId
    ? (logos.find((l) => l.id === selectedLogoId) || (pendingLogo?.id === selectedLogoId ? pendingLogo : null))
    : null;

  // Get currently selected EPG data
  const currentEpgData = selectedEpgDataId ? epgData.find((e) => e.id === selectedEpgDataId) : null;

  // Check if any changes were made
  const parsedChannelNumber = parseFloat(channelNumber);
  const hasChanges =
    (!isNaN(parsedChannelNumber) && parsedChannelNumber !== channel.channel_number) ||
    channelName !== channel.name ||
    selectedLogoId !== channel.logo_id ||
    tvgId !== (channel.tvg_id || '') ||
    tvcGuideStationId !== (channel.tvc_guide_stationid || '') ||
    selectedEpgDataId !== channel.epg_data_id ||
    selectedStreamProfileId !== channel.stream_profile_id;

  // Handle close with unsaved changes check
  const handleClose = () => {
    if (hasChanges) {
      setShowDiscardConfirm(true);
    } else {
      onClose();
    }
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const changes: ChannelMetadataChanges = {};

      if (!isNaN(parsedChannelNumber) && parsedChannelNumber !== channel.channel_number) {
        changes.channel_number = parsedChannelNumber;
      }
      if (channelName !== channel.name) {
        changes.name = channelName;
      }
      if (selectedLogoId !== channel.logo_id) {
        changes.logo_id = selectedLogoId;
      }
      if (tvgId !== (channel.tvg_id || '')) {
        changes.tvg_id = tvgId || null;
      }
      if (tvcGuideStationId !== (channel.tvc_guide_stationid || '')) {
        changes.tvc_guide_stationid = tvcGuideStationId || null;
      }
      if (selectedEpgDataId !== channel.epg_data_id) {
        changes.epg_data_id = selectedEpgDataId;
      }
      if (selectedStreamProfileId !== channel.stream_profile_id) {
        changes.stream_profile_id = selectedStreamProfileId;
      }

      await onSave(changes);
    } finally {
      setSaving(false);
    }
  };

  const handleAddLogoFromUrl = async () => {
    if (!newLogoUrl.trim()) return;

    setAddingLogo(true);
    try {
      const newLogo = await onLogoCreate(newLogoUrl.trim());
      setPendingLogo(newLogo);
      setSelectedLogoId(newLogo.id);
      setNewLogoUrl('');
    } catch (err) {
      console.error('Failed to add logo:', err);
    } finally {
      setAddingLogo(false);
    }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    if (!file.type.startsWith('image/')) {
      console.error('Invalid file type. Please select an image file.');
      return;
    }

    setUploadingLogo(true);
    try {
      await onLogoUpload(file);
    } catch (err) {
      console.error('Failed to upload logo:', err);
    } finally {
      setUploadingLogo(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    }
  };

  const handleUseEpgLogo = async () => {
    if (!currentEpgData?.icon_url) return;

    setImmediateLogoUrl(currentEpgData.icon_url);
    setAddingEpgLogo(true);
    try {
      const newLogo = await onLogoCreate(currentEpgData.icon_url);
      if (newLogo && newLogo.id) {
        setPendingLogo(newLogo);
        setSelectedLogoId(newLogo.id);
      }
    } catch (err) {
      console.error('Failed to create logo from EPG:', err);
      setImmediateLogoUrl(null);
    } finally {
      setAddingEpgLogo(false);
    }
  };

  useEffect(() => {
    if (pendingLogo && logos.find((l) => l.id === pendingLogo.id)) {
      setPendingLogo(null);
    }
  }, [logos, pendingLogo]);

  useEffect(() => {
    if (immediateLogoUrl && (!selectedLogoId || (currentLogo && currentLogo.url !== immediateLogoUrl))) {
      setImmediateLogoUrl(null);
    }
  }, [selectedLogoId, currentLogo, immediateLogoUrl]);

  const handleEpgSearch = (value: string) => {
    setEpgSearch(value);
  };

  const handleTvgIdSearch = (value: string) => {
    setTvgIdSearch(value);
  };

  // Get non-dummy EPG source IDs for filtering
  const nonDummyEpgSourceIds = new Set(nonDummyEpgSources.map(s => s.id));

  const filteredEpgData = epgData.filter((epg) => {
    // First filter by EPG source
    // If specific sources selected, only show from those; otherwise show all non-dummy sources
    if (selectedEpgSourceIds.size > 0) {
      if (!selectedEpgSourceIds.has(epg.epg_source)) return false;
    } else {
      // When no filter selected, exclude dummy EPG sources
      if (!nonDummyEpgSourceIds.has(epg.epg_source)) return false;
    }
    // Then filter by search term
    const searchTerm = (epgDropdownOpen ? epgSearch : tvgIdSearch).toLowerCase();
    if (!searchTerm) return true;
    return (
      epg.name.toLowerCase().includes(searchTerm) ||
      epg.tvg_id.toLowerCase().includes(searchTerm)
    );
  });

  const filteredTvgIdEpgData = epgData.filter((epg) => {
    // First filter by EPG source (same logic)
    if (selectedEpgSourceIds.size > 0) {
      if (!selectedEpgSourceIds.has(epg.epg_source)) return false;
    } else {
      if (!nonDummyEpgSourceIds.has(epg.epg_source)) return false;
    }
    // Then filter by search term
    const searchTerm = tvgIdSearch.toLowerCase();
    if (!searchTerm) return true;
    return (
      epg.name.toLowerCase().includes(searchTerm) ||
      epg.tvg_id.toLowerCase().includes(searchTerm)
    );
  });

  const handleSelectTvgIdFromEpg = (epg: { tvg_id: string }) => {
    setTvgId(epg.tvg_id);
    setTvgIdPickerOpen(false);
    setTvgIdSearch('');
  };

  const handleFetchLcn = async () => {
    // Use the current tvgId or the one from selected EPG data
    const lookupTvgId = tvgId || currentEpgData?.tvg_id;
    if (!lookupTvgId) {
      setLcnError('Set a TVG-ID first');
      return;
    }

    setFetchingLcn(true);
    setLcnError(null);
    try {
      const result = await api.getEPGLcnByTvgId(lookupTvgId);
      if (result.lcn) {
        setTvcGuideStationId(result.lcn);
      }
    } catch (err) {
      if (err instanceof Error && err.message.includes('404')) {
        setLcnError('No LCN found for this TVG-ID');
      } else {
        setLcnError('Failed to fetch LCN');
      }
    } finally {
      setFetchingLcn(false);
    }
  };

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (epgDropdownRef.current && !epgDropdownRef.current.contains(event.target as Node)) {
        setEpgDropdownOpen(false);
      }
    };

    if (epgDropdownOpen) {
      document.addEventListener('mousedown', handleClickOutside);
      return () => document.removeEventListener('mousedown', handleClickOutside);
    }
  }, [epgDropdownOpen]);

  // Close EPG source filter dropdown on outside click
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (epgSourceFilterRef.current && !epgSourceFilterRef.current.contains(event.target as Node)) {
        setEpgSourceFilterOpen(false);
      }
    };

    if (epgSourceFilterOpen) {
      document.addEventListener('mousedown', handleClickOutside);
      return () => document.removeEventListener('mousedown', handleClickOutside);
    }
  }, [epgSourceFilterOpen]);

  // Close stream profile dropdown on outside click
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (streamProfileDropdownRef.current && !streamProfileDropdownRef.current.contains(event.target as Node)) {
        setStreamProfileDropdownOpen(false);
      }
    };

    if (streamProfileDropdownOpen) {
      document.addEventListener('mousedown', handleClickOutside);
      return () => document.removeEventListener('mousedown', handleClickOutside);
    }
  }, [streamProfileDropdownOpen]);

  // Toggle EPG source selection
  const handleToggleEpgSource = (sourceId: number) => {
    setSelectedEpgSourceIds(prev => {
      const next = new Set(prev);
      if (next.has(sourceId)) {
        next.delete(sourceId);
      } else {
        next.add(sourceId);
      }
      return next;
    });
  };

  // Get display text for EPG source filter
  const epgSourceFilterLabel = selectedEpgSourceIds.size === 0
    ? 'All Sources'
    : selectedEpgSourceIds.size === 1
      ? nonDummyEpgSources.find(s => selectedEpgSourceIds.has(s.id))?.name || '1 source'
      : `${selectedEpgSourceIds.size} sources`;

  return (
    <div className="modal-overlay">
      <div className="modal-container edit-channel-modal">
        <div className="modal-header">
          <h2>Edit Channel</h2>
          <button className="modal-close-btn" onClick={handleClose} title="Close">
            <span className="material-icons">close</span>
          </button>
        </div>
        <div className="modal-body">
        {/* Channel Number and Name Section */}
        <div className="edit-channel-header-fields">
          <div className="edit-channel-number-field">
            <label>Channel #</label>
            <input
              type="text"
              className="edit-channel-number-input"
              value={channelNumber}
              onChange={(e) => setChannelNumber(e.target.value)}
              placeholder="123"
            />
          </div>
          <div className="edit-channel-name-field">
            <label>Channel Name</label>
            <input
              type="text"
              className="edit-channel-name-input"
              value={channelName}
              onChange={(e) => setChannelName(e.target.value)}
              placeholder="Enter channel name..."
            />
          </div>
        </div>

        {/* TVG-ID Section */}
        <div className="edit-channel-section">
          <label>TVG-ID</label>
          <div className="edit-channel-input-row">
            <input
              type="text"
              className="edit-channel-text-input"
              placeholder="Enter TVG-ID for EPG matching..."
              value={tvgId}
              onChange={(e) => setTvgId(e.target.value)}
            />
            <button
              className="edit-channel-get-epg-btn"
              onClick={() => {
                if (currentEpgData?.tvg_id) {
                  setTvgId(currentEpgData.tvg_id);
                } else {
                  setTvgIdPickerOpen(!tvgIdPickerOpen);
                }
              }}
              title={currentEpgData ? "Copy TVG-ID from selected EPG data" : "Search EPG data for TVG-ID"}
            >
              <span className="material-icons">{currentEpgData ? 'content_copy' : 'search'}</span>
              {currentEpgData ? 'Copy from EPG' : 'Get from EPG'}
            </button>
          </div>
          {tvgIdPickerOpen && (
            <div className="tvg-id-picker">
              <div className="search-input-wrapper">
                <input
                  type="text"
                  className="edit-channel-text-input"
                  placeholder="Search EPG data..."
                  value={tvgIdSearch}
                  onChange={(e) => handleTvgIdSearch(e.target.value)}
                  autoFocus
                />
                {tvgIdSearch && (
                  <button
                    type="button"
                    className="search-clear-btn"
                    onClick={() => handleTvgIdSearch('')}
                    title="Clear search"
                  >
                    <span className="material-icons">close</span>
                  </button>
                )}
              </div>
              <div className="tvg-id-picker-dropdown">
                {epgDataLoading ? (
                  <div className="epg-dropdown-loading">Loading...</div>
                ) : filteredTvgIdEpgData.length === 0 ? (
                  <div className="epg-dropdown-empty">
                    {tvgIdSearch ? 'No EPG data found' : 'Type to search EPG data'}
                  </div>
                ) : (
                  filteredTvgIdEpgData.slice(0, 100).map((epg) => (
                    <div
                      key={epg.id}
                      className="epg-dropdown-item"
                      onClick={() => handleSelectTvgIdFromEpg(epg)}
                    >
                      {epg.icon_url && (
                        <img
                          src={epg.icon_url}
                          alt=""
                          className="epg-dropdown-icon"
                          onError={(e) => {
                            (e.target as HTMLImageElement).style.display = 'none';
                          }}
                        />
                      )}
                      <div className="epg-dropdown-info">
                        <span className="epg-dropdown-name">{epg.name}</span>
                        <span className="epg-dropdown-tvgid">{epg.tvg_id}</span>
                        {epgSourceMap.get(epg.epg_source) && (
                          <span className="epg-dropdown-source">{epgSourceMap.get(epg.epg_source)}</span>
                        )}
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          )}
          <span className="edit-channel-hint">Used for matching EPG program data</span>
        </div>

        {/* Gracenote Station ID Section */}
        <div className="edit-channel-section">
          <label>Gracenote Station ID</label>
          <div className="edit-channel-input-row">
            <input
              type="text"
              className="edit-channel-text-input"
              placeholder="Enter Gracenote/TVC station ID..."
              value={tvcGuideStationId}
              onChange={(e) => {
                setTvcGuideStationId(e.target.value);
                setLcnError(null);
              }}
            />
            <button
              className="edit-channel-get-epg-btn"
              onClick={handleFetchLcn}
              disabled={fetchingLcn || (!tvgId && !currentEpgData?.tvg_id)}
              title={tvgId || currentEpgData?.tvg_id ? "Fetch LCN from EPG XML" : "Set a TVG-ID first"}
            >
              <span className="material-icons">{fetchingLcn ? 'hourglass_empty' : 'download'}</span>
              {fetchingLcn ? 'Fetching...' : 'Get from EPG'}
            </button>
          </div>
          {lcnError && <span className="edit-channel-error">{lcnError}</span>}
          <span className="edit-channel-hint">Numeric ID for Gracenote/TVC guide data (from EPG &lt;lcn&gt; tag)</span>
        </div>

        {/* EPG Data Section */}
        <div className="edit-channel-section">
          <label>EPG Data</label>
          {currentEpgData && (
            <div className="current-epg-preview">
              {currentEpgData.icon_url && (
                <img
                  src={currentEpgData.icon_url}
                  alt=""
                  onError={(e) => {
                    (e.target as HTMLImageElement).style.display = 'none';
                  }}
                />
              )}
              <div className="current-epg-info">
                <span className="current-epg-name">{currentEpgData.name}</span>
                <span className="current-epg-tvgid">{currentEpgData.tvg_id}</span>
              </div>
              <button
                className="current-epg-remove-btn"
                onClick={() => setSelectedEpgDataId(null)}
              >
                Remove
              </button>
            </div>
          )}
          {/* EPG Source Filter */}
          {nonDummyEpgSources.length > 1 && (
            <div className="epg-source-filter" ref={epgSourceFilterRef}>
              <button
                type="button"
                className="epg-source-filter-btn"
                onClick={() => setEpgSourceFilterOpen(!epgSourceFilterOpen)}
              >
                <span className="material-icons">filter_list</span>
                <span>{epgSourceFilterLabel}</span>
                <span className="material-icons">{epgSourceFilterOpen ? 'expand_less' : 'expand_more'}</span>
              </button>
              {epgSourceFilterOpen && (
                <div className="epg-source-filter-dropdown">
                  <div className="epg-source-filter-actions">
                    <button
                      type="button"
                      onClick={() => setSelectedEpgSourceIds(new Set(nonDummyEpgSources.map(s => s.id)))}
                    >
                      Select All
                    </button>
                    <button
                      type="button"
                      onClick={() => setSelectedEpgSourceIds(new Set())}
                    >
                      Clear
                    </button>
                  </div>
                  <div className="epg-source-filter-options">
                    {nonDummyEpgSources.map(source => (
                      <label key={source.id} className="epg-source-filter-option">
                        <input
                          type="checkbox"
                          checked={selectedEpgSourceIds.has(source.id)}
                          onChange={() => handleToggleEpgSource(source.id)}
                        />
                        <span>{source.name}</span>
                      </label>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}
          <div className="epg-search-container" ref={epgDropdownRef}>
            <div className="search-input-wrapper">
              <input
                type="text"
                className="edit-channel-text-input"
                placeholder="Search EPG data..."
                value={epgSearch}
                onChange={(e) => handleEpgSearch(e.target.value)}
                onFocus={() => setEpgDropdownOpen(true)}
              />
              {epgSearch && (
                <button
                  type="button"
                  className="search-clear-btn"
                  onClick={() => handleEpgSearch('')}
                  title="Clear search"
                >
                  <span className="material-icons">close</span>
                </button>
              )}
            </div>
            {epgDropdownOpen && (
              <div className="epg-dropdown">
                {epgDataLoading ? (
                  <div className="epg-dropdown-loading">Loading...</div>
                ) : filteredEpgData.length === 0 ? (
                  <div className="epg-dropdown-empty">
                    {epgSearch ? 'No EPG data found' : 'Type to search or scroll to browse'}
                  </div>
                ) : (
                  filteredEpgData.slice(0, 100).map((epg) => (
                    <div
                      key={epg.id}
                      className={`epg-dropdown-item ${selectedEpgDataId === epg.id ? 'selected' : ''}`}
                      onClick={() => {
                        setSelectedEpgDataId(epg.id);
                        if (epg.tvg_id) {
                          setTvgId(epg.tvg_id);
                        }
                        setEpgDropdownOpen(false);
                        setEpgSearch('');
                      }}
                    >
                      {epg.icon_url && (
                        <img
                          src={epg.icon_url}
                          alt=""
                          className="epg-dropdown-icon"
                          onError={(e) => {
                            (e.target as HTMLImageElement).style.display = 'none';
                          }}
                        />
                      )}
                      <div className="epg-dropdown-info">
                        <span className="epg-dropdown-name">{epg.name}</span>
                        <span className="epg-dropdown-tvgid">{epg.tvg_id}</span>
                        {epgSourceMap.get(epg.epg_source) && (
                          <span className="epg-dropdown-source">{epgSourceMap.get(epg.epg_source)}</span>
                        )}
                      </div>
                    </div>
                  ))
                )}
              </div>
            )}
          </div>
        </div>

        {/* Stream Profile Section */}
        <div className="edit-channel-section">
          <label>Stream Profile</label>
          <div className="searchable-select-dropdown" ref={streamProfileDropdownRef}>
            <button
              type="button"
              className="dropdown-trigger"
              onClick={() => setStreamProfileDropdownOpen(!streamProfileDropdownOpen)}
            >
              <span className="dropdown-value">
                {selectedStreamProfileId
                  ? streamProfiles.find(p => p.id === selectedStreamProfileId)?.name || 'Unknown'
                  : 'Default (no profile)'}
              </span>
              <span className="material-icons">expand_more</span>
            </button>
            {streamProfileDropdownOpen && (
              <div className="dropdown-menu">
                <div className="dropdown-options">
                  <div
                    className={`dropdown-option-item${selectedStreamProfileId === null ? ' selected' : ''}`}
                    onClick={() => { setSelectedStreamProfileId(null); setStreamProfileDropdownOpen(false); }}
                  >
                    Default (no profile)
                  </div>
                  {streamProfiles
                    .filter((p) => p.is_active)
                    .map((profile) => (
                      <div
                        key={profile.id}
                        className={`dropdown-option-item${selectedStreamProfileId === profile.id ? ' selected' : ''}`}
                        onClick={() => { setSelectedStreamProfileId(profile.id); setStreamProfileDropdownOpen(false); }}
                      >
                        {profile.name}
                      </div>
                    ))}
                </div>
              </div>
            )}
          </div>
          <span className="edit-channel-hint">Determines how streams are processed/transcoded</span>
        </div>

        {/* Logo Section */}
        <div className="edit-channel-section">
          <div className="logo-section-header">
            <label>Channel Logo</label>
            {currentEpgData?.icon_url && (
              <button
                onClick={handleUseEpgLogo}
                disabled={addingEpgLogo}
                className="logo-epg-btn"
                title="Use the logo from the assigned EPG data"
              >
                <span className="material-icons">live_tv</span>
                {addingEpgLogo ? 'Adding...' : 'Use EPG Logo'}
              </button>
            )}
          </div>

          {/* Current logo preview */}
          {currentLogo && (
            <div className="current-logo-preview">
              <img
                src={immediateLogoUrl || currentLogo.cache_url || currentLogo.url}
                alt={currentLogo.name}
                onError={(e) => {
                  const target = e.target as HTMLImageElement;
                  if (immediateLogoUrl && target.src === immediateLogoUrl) {
                    target.src = currentLogo.cache_url || currentLogo.url;
                  } else if (currentLogo.cache_url && target.src === currentLogo.cache_url) {
                    target.src = currentLogo.url;
                  }
                }}
              />
              <span>{currentLogo.name}</span>
              <button
                className="current-logo-remove-btn"
                onClick={() => {
                  setSelectedLogoId(null);
                  setImmediateLogoUrl(null);
                }}
              >
                Remove
              </button>
            </div>
          )}

          {/* Logo search */}
          <div className="search-input-wrapper">
            <input
              type="text"
              className="logo-search-input"
              placeholder="Search logos..."
              value={logoSearch}
              onChange={(e) => setLogoSearch(e.target.value)}
            />
            {logoSearch && (
              <button
                type="button"
                className="search-clear-btn"
                onClick={() => setLogoSearch('')}
                title="Clear search"
              >
                <span className="material-icons">close</span>
              </button>
            )}
          </div>

          {/* Logo grid */}
          <div className="logo-selection-grid">
            <div
              className={`logo-option ${selectedLogoId === null ? 'selected' : ''}`}
              onClick={() => setSelectedLogoId(null)}
            >
              <div className="logo-option-none">No Logo</div>
            </div>
            {filteredLogos.map((logo) => (
              <div
                key={logo.id}
                className={`logo-option ${selectedLogoId === logo.id ? 'selected' : ''}`}
                onClick={() => setSelectedLogoId(logo.id)}
                title={logo.name}
              >
                <img
                  src={logo.cache_url || logo.url}
                  alt={logo.name}
                  onError={(e) => {
                    (e.target as HTMLImageElement).src = logo.url;
                  }}
                />
              </div>
            ))}
          </div>

          {/* Add logo from URL or file */}
          <div className="logo-add-row">
            <input
              type="text"
              placeholder="Add logo from URL..."
              value={newLogoUrl}
              onChange={(e) => setNewLogoUrl(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  handleAddLogoFromUrl();
                }
              }}
            />
            <button
              onClick={handleAddLogoFromUrl}
              disabled={!newLogoUrl.trim() || addingLogo}
              className="logo-add-btn"
            >
              {addingLogo ? 'Adding...' : 'Add'}
            </button>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              onChange={handleFileUpload}
              style={{ display: 'none' }}
              id="logo-file-input"
            />
            <button
              onClick={() => fileInputRef.current?.click()}
              disabled={uploadingLogo}
              className="logo-upload-btn"
            >
              <span className="material-icons">upload_file</span>
              {uploadingLogo ? 'Uploading...' : 'Upload'}
            </button>
          </div>
        </div>

        </div>

        <div className="modal-footer">
          <button
            className="modal-btn modal-btn-primary"
            onClick={handleSave}
            disabled={saving || !hasChanges}
          >
            {saving ? 'Saving...' : 'Save Changes'}
          </button>
        </div>

        {/* Discard Changes Confirmation Dialog */}
        {showDiscardConfirm && (
          <div className="discard-confirm-overlay" onClick={() => setShowDiscardConfirm(false)}>
            <div className="discard-confirm-dialog" onClick={(e) => e.stopPropagation()}>
              <div className="discard-confirm-title">Unsaved Changes</div>
              <div className="discard-confirm-message">
                You have unsaved changes. Are you sure you want to close without saving?
              </div>
              <div className="discard-confirm-actions">
                <button
                  className="discard-confirm-cancel"
                  onClick={() => setShowDiscardConfirm(false)}
                >
                  Keep Editing
                </button>
                <button
                  className="discard-confirm-discard"
                  onClick={onClose}
                >
                  Discard Changes
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
});

export default EditChannelModal;
