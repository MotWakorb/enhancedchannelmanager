import { useState, useEffect, useMemo, useRef, useCallback, memo } from 'react';
import type { AutoSyncCustomProperties, ChannelGroup, ChannelProfile, StreamProfile, EPGSource, Logo } from '../types';
import * as api from '../services/api';
import './AutoSyncSettingsModal.css';

interface AutoSyncSettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSave: (customProperties: AutoSyncCustomProperties) => void;
  groupName: string;
  customProperties: AutoSyncCustomProperties | null;
  epgSources: EPGSource[];
  channelGroups: ChannelGroup[];
  channelProfiles: ChannelProfile[];
  streamProfiles: StreamProfile[];
  onGroupsChange?: () => void;
}

export const AutoSyncSettingsModal = memo(function AutoSyncSettingsModal({
  isOpen,
  onClose,
  onSave,
  groupName,
  customProperties,
  epgSources,
  channelGroups,
  channelProfiles,
  streamProfiles,
  onGroupsChange,
}: AutoSyncSettingsModalProps) {
  // Form state
  const [epgSourceId, setEpgSourceId] = useState<string>('');
  const [groupOverride, setGroupOverride] = useState<string>('');
  const [nameRegexPattern, setNameRegexPattern] = useState<string>('');
  const [nameReplacePattern, setNameReplacePattern] = useState<string>('');
  const [channelNameFilter, setChannelNameFilter] = useState<string>('');
  const [selectedProfileIds, setSelectedProfileIds] = useState<Set<string>>(new Set());
  const [sortOrder, setSortOrder] = useState<string>('');
  const [sortReverse, setSortReverse] = useState<boolean>(false);
  const [streamProfileId, setStreamProfileId] = useState<string>('');
  const [customLogoId, setCustomLogoId] = useState<string>('');

  // UI state
  const [regexError, setRegexError] = useState<string | null>(null);
  const [filterError, setFilterError] = useState<string | null>(null);
  const [profileDropdownOpen, setProfileDropdownOpen] = useState(false);
  const [logos, setLogos] = useState<Logo[]>([]);
  const [loadingLogos, setLoadingLogos] = useState(false);
  const [logoSearch, setLogoSearch] = useState('');
  const [logoDropdownOpen, setLogoDropdownOpen] = useState(false);
  const [groupDropdownOpen, setGroupDropdownOpen] = useState(false);
  const [groupSearch, setGroupSearch] = useState('');
  const [logoUrlInput, setLogoUrlInput] = useState('');
  const [uploadingLogo, setUploadingLogo] = useState(false);
  const [showNewGroupInput, setShowNewGroupInput] = useState(false);
  const [newGroupName, setNewGroupName] = useState('');
  const [creatingGroup, setCreatingGroup] = useState(false);

  const profileDropdownRef = useRef<HTMLDivElement>(null);
  const logoDropdownRef = useRef<HTMLDivElement>(null);
  const groupDropdownRef = useRef<HTMLDivElement>(null);

  // Load logos when modal opens
  useEffect(() => {
    if (isOpen) {
      setLoadingLogos(true);
      api.getLogos({ pageSize: 10000 })
        .then(response => setLogos(response.results))
        .catch(err => console.error('Failed to load logos:', err))
        .finally(() => setLoadingLogos(false));
    }
  }, [isOpen]);

  // Populate form from existing customProperties
  useEffect(() => {
    if (isOpen) {
      setEpgSourceId(customProperties?.custom_epg_id ?? '');
      setGroupOverride(customProperties?.group_override?.toString() ?? '');
      setNameRegexPattern(customProperties?.name_regex_pattern ?? '');
      setNameReplacePattern(customProperties?.name_replace_pattern ?? '');
      setChannelNameFilter(customProperties?.name_match_regex ?? '');
      setSelectedProfileIds(new Set(customProperties?.channel_profile_ids ?? []));
      setSortOrder(customProperties?.channel_sort_order ?? '');
      setSortReverse(customProperties?.channel_sort_reverse ?? false);
      setStreamProfileId(customProperties?.stream_profile_id?.toString() ?? '');
      setCustomLogoId(customProperties?.custom_logo_id?.toString() ?? '');
      setRegexError(null);
      setFilterError(null);
    }
  }, [isOpen, customProperties]);

  // Close dropdowns on outside click
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (profileDropdownRef.current && !profileDropdownRef.current.contains(event.target as Node)) {
        setProfileDropdownOpen(false);
      }
      if (logoDropdownRef.current && !logoDropdownRef.current.contains(event.target as Node)) {
        setLogoDropdownOpen(false);
      }
      if (groupDropdownRef.current && !groupDropdownRef.current.contains(event.target as Node)) {
        setGroupDropdownOpen(false);
      }
    };

    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Validate regex on blur
  const validateRegex = useCallback((pattern: string, setError: (error: string | null) => void) => {
    if (!pattern) {
      setError(null);
      return;
    }
    try {
      new RegExp(pattern);
      setError(null);
    } catch {
      setError('Invalid regex pattern');
    }
  }, []);

  // Filter logos by search
  const filteredLogos = useMemo(() => {
    if (!logoSearch.trim()) return logos.slice(0, 100); // Limit initial display
    const search = logoSearch.toLowerCase();
    return logos.filter(logo => logo.name.toLowerCase().includes(search)).slice(0, 100);
  }, [logos, logoSearch]);

  // Get selected logo name
  const selectedLogo = useMemo(() => {
    if (!customLogoId) return null;
    return logos.find(l => l.id.toString() === customLogoId);
  }, [logos, customLogoId]);

  // Handle profile toggle
  const handleToggleProfile = (profileId: string) => {
    setSelectedProfileIds(prev => {
      const next = new Set(prev);
      if (next.has(profileId)) {
        next.delete(profileId);
      } else {
        next.add(profileId);
      }
      return next;
    });
  };

  // Get selected profile names
  const selectedProfileNames = useMemo(() => {
    if (selectedProfileIds.size === 0) return 'None selected';
    return channelProfiles
      .filter(p => selectedProfileIds.has(p.id.toString()))
      .map(p => p.name)
      .join(', ');
  }, [selectedProfileIds, channelProfiles]);

  // Filter active EPG sources (include dummy)
  const activeEpgSources = useMemo(() => {
    return epgSources.filter(s => s.is_active);
  }, [epgSources]);

  // Filter channel groups by search
  const filteredGroups = useMemo(() => {
    if (!groupSearch.trim()) return channelGroups;
    const search = groupSearch.toLowerCase();
    return channelGroups.filter(group => group.name.toLowerCase().includes(search));
  }, [channelGroups, groupSearch]);

  // Get selected group
  const selectedGroup = useMemo(() => {
    if (!groupOverride) return null;
    return channelGroups.find(g => g.id.toString() === groupOverride);
  }, [channelGroups, groupOverride]);

  // Handle logo URL upload
  const handleLogoUrlUpload = async () => {
    if (!logoUrlInput.trim()) return;

    setUploadingLogo(true);
    try {
      // Check if logo already exists
      const existingLogo = logos.find(l => l.url === logoUrlInput);
      if (existingLogo) {
        setCustomLogoId(existingLogo.id.toString());
        setLogoUrlInput('');
        setLogoDropdownOpen(false);
        return;
      }

      // Create new logo
      const name = logoUrlInput.split('/').pop()?.split('?')[0] || 'Custom Logo';
      const newLogo = await api.createLogo({ name, url: logoUrlInput });
      setLogos(prev => [...prev, newLogo]);
      setCustomLogoId(newLogo.id.toString());
      setLogoUrlInput('');
      setLogoDropdownOpen(false);
    } catch (err) {
      console.error('Failed to create logo:', err);
    } finally {
      setUploadingLogo(false);
    }
  };

  // Handle creating a new channel group
  const handleCreateGroup = async () => {
    if (!newGroupName.trim()) return;

    setCreatingGroup(true);
    try {
      const newGroup = await api.createChannelGroup(newGroupName.trim());
      setGroupOverride(newGroup.id.toString());
      setNewGroupName('');
      setShowNewGroupInput(false);
      setGroupDropdownOpen(false);
      setGroupSearch('');
      // Refresh the groups list
      if (onGroupsChange) {
        onGroupsChange();
      }
    } catch (err) {
      console.error('Failed to create group:', err);
    } finally {
      setCreatingGroup(false);
    }
  };

  // Build and save custom properties
  const handleSave = () => {
    const props: AutoSyncCustomProperties = {};

    if (epgSourceId) props.custom_epg_id = epgSourceId;
    if (groupOverride) props.group_override = parseInt(groupOverride, 10);
    if (nameRegexPattern) props.name_regex_pattern = nameRegexPattern;
    if (nameReplacePattern !== undefined && nameRegexPattern) props.name_replace_pattern = nameReplacePattern;
    if (channelNameFilter) props.name_match_regex = channelNameFilter;
    if (selectedProfileIds.size > 0) props.channel_profile_ids = Array.from(selectedProfileIds);
    if (sortOrder) props.channel_sort_order = sortOrder as 'provider' | 'name' | 'tvg_id' | 'updated_at';
    if (sortReverse) props.channel_sort_reverse = sortReverse;
    if (streamProfileId) props.stream_profile_id = parseInt(streamProfileId, 10);
    if (customLogoId) props.custom_logo_id = parseInt(customLogoId, 10);

    onSave(props);
    onClose();
  };

  // Clear all settings
  const handleClearAll = () => {
    setEpgSourceId('');
    setGroupOverride('');
    setNameRegexPattern('');
    setNameReplacePattern('');
    setChannelNameFilter('');
    setSelectedProfileIds(new Set());
    setSortOrder('');
    setSortReverse(false);
    setStreamProfileId('');
    setCustomLogoId('');
    setRegexError(null);
    setFilterError(null);
  };

  // Check if form has any values
  const hasValues = useMemo(() => {
    return Boolean(
      epgSourceId ||
      groupOverride ||
      nameRegexPattern ||
      nameReplacePattern ||
      channelNameFilter ||
      selectedProfileIds.size > 0 ||
      sortOrder ||
      sortReverse ||
      streamProfileId ||
      customLogoId
    );
  }, [epgSourceId, groupOverride, nameRegexPattern, nameReplacePattern, channelNameFilter, selectedProfileIds, sortOrder, sortReverse, streamProfileId, customLogoId]);

  if (!isOpen) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content auto-sync-settings-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div className="header-info">
            <h2>Auto-Sync Settings</h2>
            <span className="group-name-display">{groupName}</span>
          </div>
          <button className="close-btn" onClick={onClose}>
            &times;
          </button>
        </div>

        <div className="modal-body">
          <div className="settings-form">
            {/* Force EPG Source */}
            <div className="form-group">
              <label>Force EPG Source</label>
              <select
                value={epgSourceId}
                onChange={(e) => setEpgSourceId(e.target.value)}
              >
                <option value="">-- None --</option>
                {activeEpgSources.map(source => (
                  <option key={source.id} value={source.id.toString()}>
                    {source.name}
                  </option>
                ))}
              </select>
              <span className="form-hint">Override the EPG source for all channels in this group</span>
            </div>

            {/* Override Channel Group */}
            <div className="form-group" ref={groupDropdownRef}>
              <label>Override Channel Group</label>
              <div className="searchable-select-dropdown">
                <button
                  type="button"
                  className="dropdown-trigger"
                  onClick={() => setGroupDropdownOpen(!groupDropdownOpen)}
                >
                  <span className="dropdown-value">
                    {selectedGroup ? selectedGroup.name : '-- None --'}
                  </span>
                  <span className="material-icons">expand_more</span>
                </button>
                {groupDropdownOpen && (
                  <div className="dropdown-menu">
                    <div className="dropdown-search">
                      <span className="material-icons">search</span>
                      <input
                        type="text"
                        placeholder="Search groups..."
                        value={groupSearch}
                        onChange={(e) => setGroupSearch(e.target.value)}
                        autoFocus
                      />
                      {groupSearch && (
                        <button
                          type="button"
                          className="clear-search"
                          onClick={() => setGroupSearch('')}
                          title="Clear search"
                        >
                          <span className="material-icons">close</span>
                        </button>
                      )}
                    </div>
                    {/* Add New Group Input */}
                    {showNewGroupInput ? (
                      <div className="new-group-input">
                        <input
                          type="text"
                          placeholder="New group name..."
                          value={newGroupName}
                          onChange={(e) => setNewGroupName(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') {
                              e.preventDefault();
                              handleCreateGroup();
                            } else if (e.key === 'Escape') {
                              setShowNewGroupInput(false);
                              setNewGroupName('');
                            }
                          }}
                          autoFocus
                        />
                        <button
                          type="button"
                          onClick={handleCreateGroup}
                          disabled={!newGroupName.trim() || creatingGroup}
                          title="Create group"
                        >
                          {creatingGroup ? (
                            <span className="material-icons spinning">sync</span>
                          ) : (
                            <span className="material-icons">check</span>
                          )}
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            setShowNewGroupInput(false);
                            setNewGroupName('');
                          }}
                          title="Cancel"
                          className="cancel-btn"
                        >
                          <span className="material-icons">close</span>
                        </button>
                      </div>
                    ) : (
                      <div
                        className="dropdown-option-item add-new-option"
                        onClick={() => setShowNewGroupInput(true)}
                      >
                        <span className="material-icons">add</span>
                        <span>Add new group...</span>
                      </div>
                    )}
                    <div className="dropdown-options">
                      <div
                        className={`dropdown-option-item ${!groupOverride ? 'selected' : ''}`}
                        onClick={() => {
                          setGroupOverride('');
                          setGroupDropdownOpen(false);
                          setGroupSearch('');
                        }}
                      >
                        <span className="no-selection">-- None --</span>
                      </div>
                      {filteredGroups.map(group => (
                        <div
                          key={group.id}
                          className={`dropdown-option-item ${groupOverride === group.id.toString() ? 'selected' : ''}`}
                          onClick={() => {
                            setGroupOverride(group.id.toString());
                            setGroupDropdownOpen(false);
                            setGroupSearch('');
                          }}
                        >
                          <span>{group.name}</span>
                        </div>
                      ))}
                      {filteredGroups.length === 0 && groupSearch && (
                        <div className="dropdown-empty">No matching groups</div>
                      )}
                    </div>
                  </div>
                )}
              </div>
              <span className="form-hint">Move synced channels to a different channel group</span>
            </div>

            {/* Channel Name Find & Replace */}
            <div className="form-group">
              <label>Channel Name Find & Replace (Regex)</label>
              <div className="dual-input">
                <div className="input-with-label">
                  <span className="input-label">Pattern:</span>
                  <input
                    type="text"
                    placeholder="e.g., ^([A-Z]{2}|\w+):\s"
                    value={nameRegexPattern}
                    onChange={(e) => setNameRegexPattern(e.target.value)}
                    onBlur={() => validateRegex(nameRegexPattern, setRegexError)}
                    className={regexError ? 'error' : ''}
                  />
                </div>
                <div className="input-with-label">
                  <span className="input-label">Replace:</span>
                  <input
                    type="text"
                    placeholder="Leave empty to remove"
                    value={nameReplacePattern}
                    onChange={(e) => setNameReplacePattern(e.target.value)}
                  />
                </div>
              </div>
              {regexError && <span className="form-error">{regexError}</span>}
              <span className="form-hint">Find text matching the regex pattern and replace it</span>
            </div>

            {/* Channel Name Filter */}
            <div className="form-group">
              <label>Channel Name Filter (Regex)</label>
              <input
                type="text"
                placeholder="e.g., ^(ESPN|FOX).*"
                value={channelNameFilter}
                onChange={(e) => setChannelNameFilter(e.target.value)}
                onBlur={() => validateRegex(channelNameFilter, setFilterError)}
                className={filterError ? 'error' : ''}
              />
              {filterError && <span className="form-error">{filterError}</span>}
              <span className="form-hint">Only sync channels whose names match this pattern</span>
            </div>

            {/* Channel Profile Assignment */}
            <div className="form-group" ref={profileDropdownRef}>
              <label>Channel Profile Assignment</label>
              <div className="multi-select-dropdown">
                <button
                  type="button"
                  className="dropdown-trigger"
                  onClick={() => setProfileDropdownOpen(!profileDropdownOpen)}
                >
                  <span className="dropdown-value">{selectedProfileNames}</span>
                  <span className="material-icons">expand_more</span>
                </button>
                {profileDropdownOpen && (
                  <div className="dropdown-menu">
                    <div className="dropdown-actions">
                      <button type="button" onClick={() => setSelectedProfileIds(new Set(channelProfiles.map(p => p.id.toString())))}>
                        Select All
                      </button>
                      <button type="button" onClick={() => setSelectedProfileIds(new Set())}>
                        Clear All
                      </button>
                    </div>
                    <div className="dropdown-options">
                      {channelProfiles.map(profile => (
                        <label key={profile.id} className="dropdown-option">
                          <input
                            type="checkbox"
                            checked={selectedProfileIds.has(profile.id.toString())}
                            onChange={() => handleToggleProfile(profile.id.toString())}
                          />
                          <span>{profile.name}</span>
                        </label>
                      ))}
                      {channelProfiles.length === 0 && (
                        <span className="dropdown-empty">No profiles available</span>
                      )}
                    </div>
                  </div>
                )}
              </div>
              <span className="form-hint">Assign channel profiles to synced channels</span>
            </div>

            {/* Channel Sort Order */}
            <div className="form-group">
              <label>Channel Sort Order</label>
              <select
                value={sortOrder}
                onChange={(e) => setSortOrder(e.target.value)}
              >
                <option value="">Select sort order...</option>
                <option value="provider">Provider Order (Default)</option>
                <option value="name">Name</option>
                <option value="tvg_id">TVG ID</option>
                <option value="updated_at">Updated At</option>
              </select>
              <label className="checkbox-row">
                <input
                  type="checkbox"
                  checked={sortReverse}
                  onChange={(e) => setSortReverse(e.target.checked)}
                />
                <span>Reverse sort order</span>
              </label>
              <span className="form-hint">Sort channels within the group</span>
            </div>

            {/* Stream Profile Assignment */}
            <div className="form-group">
              <label>Stream Profile Assignment</label>
              <select
                value={streamProfileId}
                onChange={(e) => setStreamProfileId(e.target.value)}
              >
                <option value="">-- None --</option>
                {streamProfiles.map(profile => (
                  <option key={profile.id} value={profile.id.toString()}>
                    {profile.name}
                  </option>
                ))}
              </select>
              <span className="form-hint">Assign a stream profile to synced channels</span>
            </div>

            {/* Custom Logo */}
            <div className="form-group" ref={logoDropdownRef}>
              <label>Custom Logo</label>
              <div className="logo-select-dropdown">
                <button
                  type="button"
                  className="dropdown-trigger"
                  onClick={() => setLogoDropdownOpen(!logoDropdownOpen)}
                >
                  {selectedLogo ? (
                    <div className="selected-logo">
                      <img src={selectedLogo.cache_url || selectedLogo.url} alt="" className="logo-preview" />
                      <span>{selectedLogo.name}</span>
                    </div>
                  ) : (
                    <span className="dropdown-value">-- None --</span>
                  )}
                  <span className="material-icons">expand_more</span>
                </button>
                {logoDropdownOpen && (
                  <div className="dropdown-menu logo-dropdown-menu">
                    <div className="dropdown-search">
                      <span className="material-icons">search</span>
                      <input
                        type="text"
                        placeholder="Search logos..."
                        value={logoSearch}
                        onChange={(e) => setLogoSearch(e.target.value)}
                        autoFocus
                      />
                      {logoSearch && (
                        <button
                          type="button"
                          className="clear-search"
                          onClick={() => setLogoSearch('')}
                          title="Clear search"
                        >
                          <span className="material-icons">close</span>
                        </button>
                      )}
                    </div>
                    {/* URL Input Section */}
                    <div className="logo-url-input">
                      <input
                        type="text"
                        placeholder="Or enter logo URL..."
                        value={logoUrlInput}
                        onChange={(e) => setLogoUrlInput(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') {
                            e.preventDefault();
                            handleLogoUrlUpload();
                          }
                        }}
                      />
                      <button
                        type="button"
                        onClick={handleLogoUrlUpload}
                        disabled={!logoUrlInput.trim() || uploadingLogo}
                        title="Add logo from URL"
                      >
                        {uploadingLogo ? (
                          <span className="material-icons spinning">sync</span>
                        ) : (
                          <span className="material-icons">add</span>
                        )}
                      </button>
                    </div>
                    <div className="dropdown-options logo-options">
                      <div
                        className={`logo-option-none ${!customLogoId ? 'selected' : ''}`}
                        onClick={() => {
                          setCustomLogoId('');
                          setLogoDropdownOpen(false);
                        }}
                      >
                        <span className="no-logo">-- None --</span>
                      </div>
                      {loadingLogos ? (
                        <div className="dropdown-loading">Loading logos...</div>
                      ) : filteredLogos.length === 0 ? (
                        <div className="dropdown-empty">
                          {logoSearch ? 'No matching logos' : 'No logos available'}
                        </div>
                      ) : (
                        <div className="logo-grid">
                          {filteredLogos.map(logo => (
                            <div
                              key={logo.id}
                              className={`logo-grid-item ${customLogoId === logo.id.toString() ? 'selected' : ''}`}
                              onClick={() => {
                                setCustomLogoId(logo.id.toString());
                                setLogoDropdownOpen(false);
                              }}
                              title={logo.name}
                            >
                              <img src={logo.cache_url || logo.url} alt={logo.name} className="logo-grid-preview" />
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  </div>
                )}
              </div>
              <span className="form-hint">Override the logo for all channels in this group</span>
            </div>
          </div>
        </div>

        <div className="modal-footer">
          <button
            type="button"
            className="btn-text"
            onClick={handleClearAll}
            disabled={!hasValues}
          >
            Clear All
          </button>
          <div className="footer-buttons">
            <button className="btn-secondary" onClick={onClose}>
              Cancel
            </button>
            <button
              className="btn-primary"
              onClick={handleSave}
              disabled={Boolean(regexError) || Boolean(filterError)}
            >
              Save Settings
            </button>
          </div>
        </div>
      </div>
    </div>
  );
});
