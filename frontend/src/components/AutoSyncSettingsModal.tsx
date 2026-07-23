import { logger } from '../utils/logger';
import { useState, useEffect, useMemo, useRef, useCallback, memo } from 'react';
import type { AutoSyncCustomProperties, ChannelGroup, ChannelProfile, StreamProfile, EPGSource, Logo } from '../types';
import * as api from '../services/api';
import { useNotifications } from '../contexts/NotificationContext';
import './ModalBase.css';
import './AutoSyncSettingsModal.css';
import { ModalOverlay } from './ModalOverlay';

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
  const notifications = useNotifications();
  // Form state
  const [epgSourceId, setEpgSourceId] = useState<string>('');
  const [groupOverride, setGroupOverride] = useState<string>('');
  const [nameRegexPattern, setNameRegexPattern] = useState<string>('');
  const [nameReplacePattern, setNameReplacePattern] = useState<string>('');
  const [channelNameFilter, setChannelNameFilter] = useState<string>('');
  const [selectedProfileIds, setSelectedProfileIds] = useState<Set<number>>(new Set());
  const [sortOrder, setSortOrder] = useState<string>('');
  const [sortReverse, setSortReverse] = useState<boolean>(false);
  const [streamProfileId, setStreamProfileId] = useState<string>('');
  const [customLogoId, setCustomLogoId] = useState<string>('');

  // UI state
  const [regexError, setRegexError] = useState<string | null>(null);
  const [filterError, setFilterError] = useState<string | null>(null);
  const [profileDropdownOpen, setProfileDropdownOpen] = useState(false);
  // Roving active-option index for the accessible profile listbox.
  const [activeProfileIndex, setActiveProfileIndex] = useState(0);
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
  const [epgDropdownOpen, setEpgDropdownOpen] = useState(false);
  const [sortDropdownOpen, setSortDropdownOpen] = useState(false);
  const [streamProfileDropdownOpen, setStreamProfileDropdownOpen] = useState(false);

  const profileDropdownRef = useRef<HTMLDivElement>(null);
  const profileTriggerRef = useRef<HTMLButtonElement>(null);
  const profileListboxRef = useRef<HTMLDivElement>(null);
  const logoDropdownRef = useRef<HTMLDivElement>(null);
  const groupDropdownRef = useRef<HTMLDivElement>(null);
  const epgDropdownRef = useRef<HTMLDivElement>(null);
  const sortDropdownRef = useRef<HTMLDivElement>(null);
  const streamProfileDropdownRef = useRef<HTMLDivElement>(null);

  // Load logos when modal opens
  useEffect(() => {
    if (isOpen) {
      setLoadingLogos(true);
      api.getLogos({ pageSize: 10000 })
        .then(response => setLogos(response.results))
        .catch(err => logger.error('Failed to load logos:', err))
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
      setSelectedProfileIds(new Set(
        (customProperties?.channel_profile_ids ?? [])
          .map(Number)
          .filter((n) => Number.isInteger(n))
      ));
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
      if (epgDropdownRef.current && !epgDropdownRef.current.contains(event.target as Node)) {
        setEpgDropdownOpen(false);
      }
      if (sortDropdownRef.current && !sortDropdownRef.current.contains(event.target as Node)) {
        setSortDropdownOpen(false);
      }
      if (streamProfileDropdownRef.current && !streamProfileDropdownRef.current.contains(event.target as Node)) {
        setStreamProfileDropdownOpen(false);
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
  const handleToggleProfile = (profileId: number) => {
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

  // Open/close the accessible profile listbox; focus follows so keyboard users
  // land on the options (open) or back on the trigger (close).
  const openProfileListbox = () => {
    setActiveProfileIndex(0);
    setProfileDropdownOpen(true);
    requestAnimationFrame(() => profileListboxRef.current?.focus());
  };
  const closeProfileListbox = (returnFocus = true) => {
    setProfileDropdownOpen(false);
    if (returnFocus) profileTriggerRef.current?.focus();
  };

  // Keyboard semantics for the listbox: Arrow/Home/End move the active option,
  // Enter/Space toggle it, Escape closes and returns focus to the trigger.
  const handleProfileListboxKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    const count = channelProfiles.length;
    if (e.key === 'Escape') {
      e.preventDefault();
      closeProfileListbox();
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (count) setActiveProfileIndex(i => (i + 1) % count);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (count) setActiveProfileIndex(i => (i - 1 + count) % count);
    } else if (e.key === 'Home') {
      e.preventDefault();
      setActiveProfileIndex(0);
    } else if (e.key === 'End') {
      e.preventDefault();
      if (count) setActiveProfileIndex(count - 1);
    } else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      const profile = channelProfiles[activeProfileIndex];
      if (profile) handleToggleProfile(profile.id);
    }
  };

  // Selected ids that no longer match ANY current profile (deleted in
  // Dispatcharr). Surfaced so a stale selection is shown clearly instead of
  // the picker silently rendering blank / dropping the missing choices.
  const missingSelectedIds = useMemo(() => {
    const known = new Set(channelProfiles.map(p => p.id));
    return Array.from(selectedProfileIds).filter(id => !known.has(id));
  }, [selectedProfileIds, channelProfiles]);

  // Get selected profile names. An EMPTY selection is NOT "clear everywhere":
  // ECM stops MANAGING this group's profiles and leaves existing memberships
  // untouched (GH #720 Part B, decision 1a) — the label reflects that. Stale
  // ids (deleted profiles) are shown as an explicit "N unknown" count so the
  // trigger is never blank when the whole selection is stale.
  const selectedProfileNames = useMemo(() => {
    if (selectedProfileIds.size === 0) return 'Not managed by Auto-Sync';
    const knownNames = channelProfiles
      .filter(p => selectedProfileIds.has(p.id))
      .map(p => p.name);
    const parts: string[] = [];
    if (knownNames.length) parts.push(knownNames.join(', '));
    if (missingSelectedIds.length) parts.push(`${missingSelectedIds.length} unknown profile(s)`);
    return parts.join(', ');
  }, [selectedProfileIds, channelProfiles, missingSelectedIds]);

  // Filter active EPG sources (include dummy)
  const activeEpgSources = useMemo(() => {
    return epgSources.filter(s => s.is_active);
  }, [epgSources]);

  // Get selected EPG source name
  const selectedEpgSource = useMemo(() => {
    if (!epgSourceId) return null;
    return activeEpgSources.find(s => s.id.toString() === epgSourceId);
  }, [activeEpgSources, epgSourceId]);

  // Get selected stream profile name
  const selectedStreamProfile = useMemo(() => {
    if (!streamProfileId) return null;
    return streamProfiles.find(p => p.id.toString() === streamProfileId);
  }, [streamProfiles, streamProfileId]);

  // Sort order options
  const sortOrderOptions = useMemo(() => [
    { value: '', label: 'Select sort order...' },
    { value: 'provider', label: 'Provider Order (Default)' },
    { value: 'name', label: 'Name' },
    { value: 'tvg_id', label: 'TVG ID' },
    { value: 'updated_at', label: 'Updated At' },
  ], []);

  // Get selected sort order label
  const selectedSortOrderLabel = useMemo(() => {
    const option = sortOrderOptions.find(o => o.value === sortOrder);
    return option?.label || 'Select sort order...';
  }, [sortOrder, sortOrderOptions]);

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
      logger.error('Failed to create logo:', err);
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
      notifications.success(`Created group "${newGroup.name}"`, 'Auto-Sync Settings');
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
      logger.error('Failed to create group:', err);
      notifications.error(err instanceof Error ? err.message : 'Failed to create group', 'Auto-Sync Settings');
    } finally {
      setCreatingGroup(false);
    }
  };

  // Build and save custom properties.
  // Start from the group's CURRENT stored custom_properties and overlay only
  // the keys this form manages — Dispatcharr's group-settings upsert replaces
  // custom_properties wholesale, and its sync consumes keys this form doesn't
  // model (channel_numbering_mode, force_dummy_epg, ...), so unknown keys
  // must survive verbatim (bead enhancedchannelmanager-igqcy). Managed keys
  // that were cleared are deleted (Dispatcharr treats absence as unset).
  const handleSave = () => {
    const props: AutoSyncCustomProperties = { ...(customProperties ?? {}) };

    if (epgSourceId) props.custom_epg_id = epgSourceId; else delete props.custom_epg_id;
    if (groupOverride) props.group_override = parseInt(groupOverride, 10); else delete props.group_override;
    if (nameRegexPattern) props.name_regex_pattern = nameRegexPattern; else delete props.name_regex_pattern;
    if (nameReplacePattern !== undefined && nameRegexPattern) props.name_replace_pattern = nameReplacePattern; else delete props.name_replace_pattern;
    if (channelNameFilter) props.name_match_regex = channelNameFilter; else delete props.name_match_regex;
    // Drop stale ids (profiles deleted in Dispatcharr) on save so the stored
    // selection matches the "N unknown profile(s) ... will be dropped on save"
    // copy — the saved value is exactly the currently-valid selection.
    {
      const known = new Set(channelProfiles.map(p => p.id));
      const validIds = Array.from(selectedProfileIds).filter(id => known.has(id));
      if (validIds.length > 0) props.channel_profile_ids = validIds; else delete props.channel_profile_ids;
    }
    if (sortOrder) props.channel_sort_order = sortOrder as 'provider' | 'name' | 'tvg_id' | 'updated_at'; else delete props.channel_sort_order;
    if (sortReverse) props.channel_sort_reverse = sortReverse; else delete props.channel_sort_reverse;
    if (streamProfileId) props.stream_profile_id = parseInt(streamProfileId, 10); else delete props.stream_profile_id;
    if (customLogoId) props.custom_logo_id = parseInt(customLogoId, 10); else delete props.custom_logo_id;

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
    <ModalOverlay onClose={onClose}>
      <div className="modal-container modal-md auto-sync-settings-modal">
        <div className="modal-header">
          <div className="header-info">
            <h2>Auto-Sync Settings</h2>
            <span className="group-name-display">{groupName}</span>
          </div>
          <button className="modal-close-btn" onClick={onClose} aria-label="Close" title="Close">
            <span className="material-icons" aria-hidden="true">close</span>
          </button>
        </div>

        <div className="modal-body">
          <div className="settings-form">
            {/* Force EPG Source */}
            <div className="modal-form-group" ref={epgDropdownRef}>
              <label>Force EPG Source</label>
              <div className="searchable-select-dropdown">
                <button
                  type="button"
                  className="dropdown-trigger"
                  onClick={() => setEpgDropdownOpen(!epgDropdownOpen)}
                >
                  <span className="dropdown-value">
                    {selectedEpgSource ? selectedEpgSource.name : '-- None --'}
                  </span>
                  <span className="material-icons">expand_more</span>
                </button>
                {epgDropdownOpen && (
                  <div className="dropdown-menu">
                    <div className="dropdown-options">
                      <div
                        className={`dropdown-option-item ${!epgSourceId ? 'selected' : ''}`}
                        onClick={() => {
                          setEpgSourceId('');
                          setEpgDropdownOpen(false);
                        }}
                      >
                        <span className="no-selection">-- None --</span>
                      </div>
                      {activeEpgSources.map(source => (
                        <div
                          key={source.id}
                          className={`dropdown-option-item ${epgSourceId === source.id.toString() ? 'selected' : ''}`}
                          onClick={() => {
                            setEpgSourceId(source.id.toString());
                            setEpgDropdownOpen(false);
                          }}
                        >
                          <span>{source.name}</span>
                        </div>
                      ))}
                      {activeEpgSources.length === 0 && (
                        <div className="dropdown-empty">No EPG sources available</div>
                      )}
                    </div>
                  </div>
                )}
              </div>
              <span className="form-hint">Override the EPG source for all channels in this group</span>
            </div>

            {/* Override Channel Group */}
            <div className="modal-form-group" ref={groupDropdownRef}>
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
                          aria-label="Clear search"
                        >
                          <span className="material-icons" aria-hidden="true">close</span>
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
                          aria-label="Cancel new group creation"
                        >
                          <span className="material-icons" aria-hidden="true">close</span>
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
            <div className="modal-form-group">
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
            <div className="modal-form-group">
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
              <span className="form-hint">
                Only syncs this group&apos;s already-imported streams whose names match this
                pattern, applied at sync time — distinct from the per-account &quot;Manage
                Filters&quot;, which filters at M3U import time across the whole account.
              </span>
            </div>

            {/* Channel Profile Assignment */}
            <div className="modal-form-group" ref={profileDropdownRef}>
              <label id="channel-profile-assignment-label">Channel Profile Assignment</label>
              <div className="multi-select-dropdown">
                <button
                  type="button"
                  ref={profileTriggerRef}
                  className="dropdown-trigger"
                  aria-haspopup="listbox"
                  aria-expanded={profileDropdownOpen}
                  aria-controls="channel-profile-listbox"
                  // F1 (a11y): the accessible NAME includes the current
                  // selection so screen-reader users hear what is selected, not
                  // just the field label.
                  aria-label={`Channel Profile Assignment: ${selectedProfileNames}`}
                  onClick={() => (profileDropdownOpen ? closeProfileListbox(false) : openProfileListbox())}
                >
                  <span className="dropdown-value">{selectedProfileNames}</span>
                  <span className="material-icons" aria-hidden="true">expand_more</span>
                </button>
                {profileDropdownOpen && (
                  <div className="dropdown-menu">
                    <div className="dropdown-actions">
                      <button type="button" onClick={() => setSelectedProfileIds(new Set(channelProfiles.map(p => p.id)))}>
                        Select All
                      </button>
                      <button type="button" onClick={() => setSelectedProfileIds(new Set())}>
                        Stop managing profiles
                      </button>
                    </div>
                    <div
                      id="channel-profile-listbox"
                      className="dropdown-options"
                      ref={profileListboxRef}
                      role="listbox"
                      aria-multiselectable="true"
                      aria-labelledby="channel-profile-assignment-label"
                      aria-activedescendant={
                        channelProfiles.length
                          ? `channel-profile-option-${channelProfiles[Math.min(activeProfileIndex, channelProfiles.length - 1)]?.id}`
                          : undefined
                      }
                      tabIndex={0}
                      onKeyDown={handleProfileListboxKeyDown}
                    >
                      {channelProfiles.map((profile, idx) => {
                        const selected = selectedProfileIds.has(profile.id);
                        return (
                          <div
                            key={profile.id}
                            id={`channel-profile-option-${profile.id}`}
                            role="option"
                            aria-selected={selected}
                            className={`dropdown-option${idx === activeProfileIndex ? ' active' : ''}`}
                            onClick={() => { setActiveProfileIndex(idx); handleToggleProfile(profile.id); }}
                          >
                            <input
                              type="checkbox"
                              checked={selected}
                              tabIndex={-1}
                              aria-hidden="true"
                              readOnly
                            />
                            <span>{profile.name}</span>
                          </div>
                        );
                      })}
                      {channelProfiles.length === 0 && (
                        <span className="dropdown-empty">No profiles available</span>
                      )}
                    </div>
                  </div>
                )}
              </div>
              {missingSelectedIds.length > 0 && (
                <span className="form-hint form-hint-warning" role="alert">
                  {missingSelectedIds.length} previously-selected profile(s) no longer exist and
                  will be dropped on save — reopen and choose current profiles if needed.
                </span>
              )}
              <span className="form-hint">
                Assigns Dispatcharr Channel Profiles (client-facing visibility) to channels
                synced from this group — a different entity than the per-account &quot;Manage
                Account Profiles&quot; screen, which sets M3U stream failover profiles.
                {' '}Selecting profiles makes ECM keep this group&apos;s channels in exactly
                those profiles. Leaving it empty (&quot;Stop managing profiles&quot;) means ECM
                stops managing this group&apos;s profiles and leaves existing memberships
                unchanged — it does NOT remove the channels from every profile. Channels whose
                profile membership was set by a Channel Pipeline rule are excluded from
                Auto-Sync profile management.
                {' '}This selection is GLOBAL for the channel group: saving it here applies it
                to this group across ALL M3U accounts, not just this one.
              </span>
            </div>

            {/* Channel Sort Order */}
            <div className="modal-form-group" ref={sortDropdownRef}>
              <label>Channel Sort Order</label>
              <div className="searchable-select-dropdown">
                <button
                  type="button"
                  className="dropdown-trigger"
                  onClick={() => setSortDropdownOpen(!sortDropdownOpen)}
                >
                  <span className="dropdown-value">{selectedSortOrderLabel}</span>
                  <span className="material-icons">expand_more</span>
                </button>
                {sortDropdownOpen && (
                  <div className="dropdown-menu">
                    <div className="dropdown-options">
                      {sortOrderOptions.map(option => (
                        <div
                          key={option.value}
                          className={`dropdown-option-item ${sortOrder === option.value ? 'selected' : ''}`}
                          onClick={() => {
                            setSortOrder(option.value);
                            setSortDropdownOpen(false);
                          }}
                        >
                          <span className={option.value === '' ? 'no-selection' : ''}>{option.label}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
              <label className="modal-checkbox-row">
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
            <div className="modal-form-group" ref={streamProfileDropdownRef}>
              <label>Stream Profile Assignment</label>
              <div className="searchable-select-dropdown">
                <button
                  type="button"
                  className="dropdown-trigger"
                  onClick={() => setStreamProfileDropdownOpen(!streamProfileDropdownOpen)}
                >
                  <span className="dropdown-value">
                    {selectedStreamProfile ? selectedStreamProfile.name : '-- None --'}
                  </span>
                  <span className="material-icons">expand_more</span>
                </button>
                {streamProfileDropdownOpen && (
                  <div className="dropdown-menu">
                    <div className="dropdown-options">
                      <div
                        className={`dropdown-option-item ${!streamProfileId ? 'selected' : ''}`}
                        onClick={() => {
                          setStreamProfileId('');
                          setStreamProfileDropdownOpen(false);
                        }}
                      >
                        <span className="no-selection">-- None --</span>
                      </div>
                      {streamProfiles.map(profile => (
                        <div
                          key={profile.id}
                          className={`dropdown-option-item ${streamProfileId === profile.id.toString() ? 'selected' : ''}`}
                          onClick={() => {
                            setStreamProfileId(profile.id.toString());
                            setStreamProfileDropdownOpen(false);
                          }}
                        >
                          <span>{profile.name}</span>
                        </div>
                      ))}
                      {streamProfiles.length === 0 && (
                        <div className="dropdown-empty">No stream profiles available</div>
                      )}
                    </div>
                  </div>
                )}
              </div>
              <span className="form-hint">
                Assigns the Dispatcharr stream (transcode) profile for channels synced from
                this group. The top-level &quot;Stream Profiles&quot; screen is a read-only
                catalog — this is where assignment actually happens.
              </span>
            </div>

            {/* Custom Logo */}
            <div className="modal-form-group" ref={logoDropdownRef}>
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
                          aria-label="Clear search"
                        >
                          <span className="material-icons" aria-hidden="true">close</span>
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

            {/* Saving here only stages the settings on the parent modal —
                no refresh happens until the operator hits Save & Refresh. */}
            <div className="modal-form-group">
              <span className="form-hint">
                Saved settings are applied when you Save &amp; Refresh in Manage Groups.
              </span>
            </div>
          </div>
        </div>

        <div className="modal-footer modal-footer-spread">
          <button
            type="button"
            className="modal-btn modal-btn-text"
            onClick={handleClearAll}
            disabled={!hasValues}
          >
            Clear All
          </button>
          <div className="footer-buttons">
            <button className="modal-btn modal-btn-secondary" onClick={onClose}>
              Cancel
            </button>
            <button
              className="modal-btn modal-btn-primary"
              onClick={handleSave}
              disabled={Boolean(regexError) || Boolean(filterError)}
            >
              Save Settings
            </button>
          </div>
        </div>
      </div>
    </ModalOverlay>
  );
});
