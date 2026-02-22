import { useState, useEffect, useCallback, memo, useRef } from 'react';
import type {
  DummyEPGProfile,
  DummyEPGProfileCreateRequest,
  DummyEPGPreviewResult,
  SubstitutionPair,
  PatternVariant,
  ChannelGroup,
} from '../types';
import * as api from '../services/api';
import { useAsyncOperation } from '../hooks/useAsyncOperation';
import { ModalOverlay } from './ModalOverlay';
import { SubstitutionPairsEditor } from './SubstitutionPairsEditor';
import { PatternBuilder } from './patternBuilder';
import { VariantTabs } from './patternBuilder/VariantTabs';
import './ModalBase.css';
import './DummyEPGProfileModal.css';

const TIMEZONES = [
  { value: '', label: '-- None --' },
  { value: 'US/Eastern', label: 'US/Eastern (ET)' },
  { value: 'US/Central', label: 'US/Central (CT)' },
  { value: 'US/Mountain', label: 'US/Mountain (MT)' },
  { value: 'US/Pacific', label: 'US/Pacific (PT)' },
  { value: 'US/Alaska', label: 'US/Alaska' },
  { value: 'US/Hawaii', label: 'US/Hawaii' },
  { value: 'America/New_York', label: 'America/New_York' },
  { value: 'America/Chicago', label: 'America/Chicago' },
  { value: 'America/Denver', label: 'America/Denver' },
  { value: 'America/Los_Angeles', label: 'America/Los_Angeles' },
  { value: 'America/Toronto', label: 'America/Toronto' },
  { value: 'America/Vancouver', label: 'America/Vancouver' },
  { value: 'Europe/London', label: 'Europe/London (GMT/BST)' },
  { value: 'Europe/Paris', label: 'Europe/Paris (CET)' },
  { value: 'Europe/Berlin', label: 'Europe/Berlin (CET)' },
  { value: 'Europe/Amsterdam', label: 'Europe/Amsterdam (CET)' },
  { value: 'Australia/Sydney', label: 'Australia/Sydney (AEST)' },
  { value: 'Australia/Melbourne', label: 'Australia/Melbourne (AEST)' },
  { value: 'UTC', label: 'UTC' },
];

function makeEmptyVariant(name: string = 'Default'): PatternVariant {
  return {
    name,
    title_pattern: null,
    time_pattern: null,
    date_pattern: null,
    title_template: null,
    description_template: null,
    channel_logo_url_template: null,
    program_poster_url_template: null,
    pattern_builder_examples: null,
    upcoming_title_template: null,
    upcoming_description_template: null,
    ended_title_template: null,
    ended_description_template: null,
    fallback_title_template: null,
    fallback_description_template: null,
  };
}

/** Migrate flat profile fields into a single "Default" variant. */
function migrateToVariant(profile: DummyEPGProfile): PatternVariant {
  return {
    name: 'Default',
    title_pattern: profile.title_pattern,
    time_pattern: profile.time_pattern,
    date_pattern: profile.date_pattern,
    title_template: profile.title_template,
    description_template: profile.description_template,
    channel_logo_url_template: profile.channel_logo_url_template,
    program_poster_url_template: profile.program_poster_url_template,
    pattern_builder_examples: profile.pattern_builder_examples,
    upcoming_title_template: null,
    upcoming_description_template: null,
    ended_title_template: null,
    ended_description_template: null,
    fallback_title_template: null,
    fallback_description_template: null,
  };
}

function extractGroupNames(pattern: string | null): string[] {
  if (!pattern) return [];
  const names: string[] = [];
  const re = /\(\?<(\w+)>/g;
  let m;
  while ((m = re.exec(pattern)) !== null) {
    if (!names.includes(m[1])) names.push(m[1]);
  }
  return names;
}

interface CollapsibleSectionProps {
  title: string;
  isOpen: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}

const CollapsibleSection = memo(function CollapsibleSection({ title, isOpen, onToggle, children }: CollapsibleSectionProps) {
  return (
    <div className="modal-collapsible">
      <button type="button" className="modal-collapsible-header" onClick={onToggle}>
        <span className="material-icons">{isOpen ? 'expand_less' : 'expand_more'}</span>
        <span>{title}</span>
      </button>
      {isOpen && <div className="modal-collapsible-content">{children}</div>}
    </div>
  );
});

interface DummyEPGProfileModalProps {
  isOpen: boolean;
  profile: DummyEPGProfile | null;
  onClose: () => void;
  onSave: () => void;
  importData?: Partial<DummyEPGProfile> | null;
}

export const DummyEPGProfileModal = memo(function DummyEPGProfileModal({
  isOpen,
  profile,
  onClose,
  onSave,
  importData,
}: DummyEPGProfileModalProps) {
  // Basic Info
  const [name, setName] = useState('');
  const [enabled, setEnabled] = useState(true);

  // Channel Groups
  const [channelGroups, setChannelGroups] = useState<ChannelGroup[]>([]);
  const [channelGroupIds, setChannelGroupIds] = useState<number[]>([]);
  const [groupsLoading, setGroupsLoading] = useState(false);
  const [groupSearchTerm, setGroupSearchTerm] = useState('');

  // Substitution Pairs (profile-level)
  const [substitutionPairs, setSubstitutionPairs] = useState<SubstitutionPair[]>([]);

  // Name source
  const [nameSource, setNameSource] = useState<'channel' | 'stream'>('channel');
  const [streamIndex, setStreamIndex] = useState(1);

  // Pattern variants
  const [variants, setVariants] = useState<PatternVariant[]>([makeEmptyVariant()]);
  const [activeVariantIndex, setActiveVariantIndex] = useState(0);

  // Profile-level templates (defaults for upcoming/ended/fallback)
  const [upcomingTitleTemplate, setUpcomingTitleTemplate] = useState('');
  const [upcomingDescriptionTemplate, setUpcomingDescriptionTemplate] = useState('');
  const [endedTitleTemplate, setEndedTitleTemplate] = useState('');
  const [endedDescriptionTemplate, setEndedDescriptionTemplate] = useState('');
  const [fallbackTitleTemplate, setFallbackTitleTemplate] = useState('');
  const [fallbackDescriptionTemplate, setFallbackDescriptionTemplate] = useState('');

  // EPG Settings
  const [eventTimezone, setEventTimezone] = useState('US/Eastern');
  const [outputTimezone, setOutputTimezone] = useState('');
  const [programDuration, setProgramDuration] = useState(180);
  const [categories, setCategories] = useState('');
  const [tvgIdTemplate, setTvgIdTemplate] = useState('ecm-{channel_number}');
  const [includeDateTag, setIncludeDateTag] = useState(false);
  const [includeLiveTag, setIncludeLiveTag] = useState(false);
  const [includeNewTag, setIncludeNewTag] = useState(false);

  // Batch test
  const [batchInput, setBatchInput] = useState('');
  const [batchResults, setBatchResults] = useState<DummyEPGPreviewResult[]>([]);
  const [batchLoading, setBatchLoading] = useState(false);
  const [expandedBatchRows, setExpandedBatchRows] = useState<Set<number>>(new Set());

  // UI State
  const { loading: saving, error, execute, setError, clearError } = useAsyncOperation();

  // Collapsible sections
  const [subsOpen, setSubsOpen] = useState(false);
  const [upcomingEndedOpen, setUpcomingEndedOpen] = useState(false);
  const [fallbackOpen, setFallbackOpen] = useState(false);
  const [epgTagsOpen, setEpgTagsOpen] = useState(false);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [variantOverridesOpen, setVariantOverridesOpen] = useState(false);

  // Timezone dropdowns
  const [eventTimezoneDropdownOpen, setEventTimezoneDropdownOpen] = useState(false);
  const [outputTimezoneDropdownOpen, setOutputTimezoneDropdownOpen] = useState(false);
  const [eventTimezoneSearch, setEventTimezoneSearch] = useState('');
  const [outputTimezoneSearch, setOutputTimezoneSearch] = useState('');
  const eventTimezoneDropdownRef = useRef<HTMLDivElement>(null);
  const outputTimezoneDropdownRef = useRef<HTMLDivElement>(null);

  // Name source dropdown
  const [nameSourceDropdownOpen, setNameSourceDropdownOpen] = useState(false);
  const nameSourceDropdownRef = useRef<HTMLDivElement>(null);

  // Close dropdowns on outside click
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (nameSourceDropdownRef.current && !nameSourceDropdownRef.current.contains(event.target as Node)) {
        setNameSourceDropdownOpen(false);
      }
      if (eventTimezoneDropdownRef.current && !eventTimezoneDropdownRef.current.contains(event.target as Node)) {
        setEventTimezoneDropdownOpen(false);
      }
      if (outputTimezoneDropdownRef.current && !outputTimezoneDropdownRef.current.contains(event.target as Node)) {
        setOutputTimezoneDropdownOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  // Load channel groups when modal opens
  useEffect(() => {
    if (isOpen) {
      setGroupsLoading(true);
      api.getChannelGroups()
        .then(groups => setChannelGroups(groups))
        .catch(() => setChannelGroups([]))
        .finally(() => setGroupsLoading(false));
    }
  }, [isOpen]);

  // Load profile data when modal opens
  useEffect(() => {
    if (isOpen) {
      if (profile) {
        setName(profile.name);
        setEnabled(profile.enabled);
        setChannelGroupIds(profile.channel_group_ids || []);
        setSubstitutionPairs(profile.substitution_pairs || []);
        setNameSource(profile.name_source);
        setStreamIndex(profile.stream_index);

        // Load variants
        if (profile.pattern_variants && profile.pattern_variants.length > 0) {
          setVariants(profile.pattern_variants);
        } else {
          // Migrate flat fields to single variant
          setVariants([migrateToVariant(profile)]);
        }
        setActiveVariantIndex(0);

        // Profile-level templates
        setUpcomingTitleTemplate(profile.upcoming_title_template || '');
        setUpcomingDescriptionTemplate(profile.upcoming_description_template || '');
        setEndedTitleTemplate(profile.ended_title_template || '');
        setEndedDescriptionTemplate(profile.ended_description_template || '');
        setFallbackTitleTemplate(profile.fallback_title_template || '');
        setFallbackDescriptionTemplate(profile.fallback_description_template || '');

        setEventTimezone(profile.event_timezone || 'US/Eastern');
        setOutputTimezone(profile.output_timezone || '');
        setProgramDuration(profile.program_duration || 180);
        setCategories(profile.categories || '');
        setTvgIdTemplate(profile.tvg_id_template || 'ecm-{channel_number}');
        setIncludeDateTag(profile.include_date_tag || false);
        setIncludeLiveTag(profile.include_live_tag || false);
        setIncludeNewTag(profile.include_new_tag || false);

        setSubsOpen((profile.substitution_pairs || []).length > 0);
        setUpcomingEndedOpen(Boolean(profile.upcoming_title_template || profile.upcoming_description_template || profile.ended_title_template || profile.ended_description_template));
        setFallbackOpen(Boolean(profile.fallback_title_template || profile.fallback_description_template));
        setEpgTagsOpen(Boolean(profile.include_date_tag || profile.include_live_tag || profile.include_new_tag));
        setAdvancedOpen(Boolean(profile.tvg_id_template && profile.tvg_id_template !== 'ecm-{channel_number}'));
      } else if (importData) {
        // Import mode: pre-fill from Dispatcharr source data
        const d = importData;
        setName(d.name || '');
        setEnabled(true);
        setChannelGroupIds(d.channel_group_ids || []);
        setSubstitutionPairs(d.substitution_pairs || []);
        setNameSource(d.name_source || 'channel');
        setStreamIndex(d.stream_index || 1);

        // Build a Default variant from the imported flat fields
        const importedVariant = {
          ...makeEmptyVariant(),
          title_pattern: d.title_pattern ?? null,
          time_pattern: d.time_pattern ?? null,
          date_pattern: d.date_pattern ?? null,
          title_template: d.title_template ?? null,
          description_template: d.description_template ?? null,
          channel_logo_url_template: d.channel_logo_url_template ?? null,
          program_poster_url_template: d.program_poster_url_template ?? null,
        };
        setVariants([importedVariant]);
        setActiveVariantIndex(0);

        setUpcomingTitleTemplate(d.upcoming_title_template || '');
        setUpcomingDescriptionTemplate(d.upcoming_description_template || '');
        setEndedTitleTemplate(d.ended_title_template || '');
        setEndedDescriptionTemplate(d.ended_description_template || '');
        setFallbackTitleTemplate(d.fallback_title_template || '');
        setFallbackDescriptionTemplate(d.fallback_description_template || '');
        setEventTimezone(d.event_timezone || 'US/Eastern');
        setOutputTimezone(d.output_timezone || '');
        setProgramDuration(d.program_duration || 180);
        setCategories(d.categories || '');
        setTvgIdTemplate(d.tvg_id_template || 'ecm-{channel_number}');
        setIncludeDateTag(d.include_date_tag || false);
        setIncludeLiveTag(d.include_live_tag || false);
        setIncludeNewTag(d.include_new_tag || false);

        // Open sections that have data
        setSubsOpen((d.substitution_pairs || []).length > 0);
        setUpcomingEndedOpen(Boolean(d.upcoming_title_template || d.upcoming_description_template || d.ended_title_template || d.ended_description_template));
        setFallbackOpen(Boolean(d.fallback_title_template || d.fallback_description_template));
        setEpgTagsOpen(Boolean(d.include_date_tag || d.include_live_tag || d.include_new_tag));
        setAdvancedOpen(false);
      } else {
        setName('');
        setEnabled(true);
        setChannelGroupIds([]);
        setSubstitutionPairs([]);
        setNameSource('channel');
        setStreamIndex(1);
        setVariants([makeEmptyVariant()]);
        setActiveVariantIndex(0);
        setUpcomingTitleTemplate('');
        setUpcomingDescriptionTemplate('');
        setEndedTitleTemplate('');
        setEndedDescriptionTemplate('');
        setFallbackTitleTemplate('');
        setFallbackDescriptionTemplate('');
        setEventTimezone('US/Eastern');
        setOutputTimezone('');
        setProgramDuration(180);
        setCategories('');
        setTvgIdTemplate('ecm-{channel_number}');
        setIncludeDateTag(false);
        setIncludeLiveTag(false);
        setIncludeNewTag(false);
        setSubsOpen(false);
        setUpcomingEndedOpen(false);
        setFallbackOpen(false);
        setEpgTagsOpen(false);
        setAdvancedOpen(false);
      }
      setBatchInput('');
      setBatchResults([]);
      setExpandedBatchRows(new Set());
      setVariantOverridesOpen(false);
      setGroupSearchTerm('');
      clearError();
    }
  }, [isOpen, profile, importData, clearError]);

  // Active variant helpers
  const activeVariant = variants[activeVariantIndex] || makeEmptyVariant();

  const updateActiveVariant = useCallback((updates: Partial<PatternVariant>) => {
    setVariants(prev => prev.map((v, i) => i === activeVariantIndex ? { ...v, ...updates } : v));
  }, [activeVariantIndex]);

  // Variant tab handlers
  const handleAddVariant = useCallback(() => {
    const newVariant = makeEmptyVariant(`Variant ${variants.length + 1}`);
    setVariants(prev => [...prev, newVariant]);
    setActiveVariantIndex(variants.length);
  }, [variants.length]);

  const handleRenameVariant = useCallback((index: number, newName: string) => {
    setVariants(prev => prev.map((v, i) => i === index ? { ...v, name: newName } : v));
  }, []);

  const handleDeleteVariant = useCallback((index: number) => {
    if (variants.length <= 1) return;
    setVariants(prev => prev.filter((_, i) => i !== index));
    if (activeVariantIndex >= index && activeVariantIndex > 0) {
      setActiveVariantIndex(activeVariantIndex - 1);
    }
  }, [variants.length, activeVariantIndex]);

  // Batch test
  const handleBatchTest = useCallback(async () => {
    const names = batchInput.split('\n').map(s => s.trim()).filter(Boolean);
    if (!names.length) return;
    setBatchLoading(true);
    try {
      const v = variants[0]; // Use first variant's flat fields for backward compat
      const results = await api.previewDummyEPGBatch({
        sample_names: names,
        substitution_pairs: substitutionPairs,
        title_pattern: v?.title_pattern || undefined,
        time_pattern: v?.time_pattern || undefined,
        date_pattern: v?.date_pattern || undefined,
        title_template: v?.title_template || undefined,
        description_template: v?.description_template || undefined,
        upcoming_title_template: upcomingTitleTemplate || undefined,
        upcoming_description_template: upcomingDescriptionTemplate || undefined,
        ended_title_template: endedTitleTemplate || undefined,
        ended_description_template: endedDescriptionTemplate || undefined,
        fallback_title_template: fallbackTitleTemplate || undefined,
        fallback_description_template: fallbackDescriptionTemplate || undefined,
        event_timezone: eventTimezone,
        output_timezone: outputTimezone || undefined,
        program_duration: programDuration,
        channel_logo_url_template: v?.channel_logo_url_template || undefined,
        program_poster_url_template: v?.program_poster_url_template || undefined,
        pattern_variants: variants.length > 1 || (variants[0]?.title_pattern)
          ? variants.map(vr => ({
              ...vr,
              title_pattern: vr.title_pattern || undefined,
              time_pattern: vr.time_pattern || undefined,
              date_pattern: vr.date_pattern || undefined,
            } as PatternVariant))
          : undefined,
      });
      setBatchResults(results);
      setExpandedBatchRows(new Set());
    } catch {
      setBatchResults([]);
      setExpandedBatchRows(new Set());
    } finally {
      setBatchLoading(false);
    }
  }, [batchInput, variants, substitutionPairs, upcomingTitleTemplate, upcomingDescriptionTemplate,
      endedTitleTemplate, endedDescriptionTemplate, fallbackTitleTemplate, fallbackDescriptionTemplate,
      eventTimezone, outputTimezone, programDuration]);

  const validateRegex = useCallback((pattern: string | null) => {
    if (!pattern) return true;
    try {
      new RegExp(pattern);
      return true;
    } catch {
      return false;
    }
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    clearError();

    if (!name.trim()) {
      setError('Name is required');
      return;
    }

    // Validate all variant patterns
    for (const v of variants) {
      if (!v.title_pattern?.trim()) {
        setError(`Variant "${v.name}" needs a Title Pattern`);
        return;
      }
      if (!validateRegex(v.title_pattern)) {
        setError(`Variant "${v.name}" has an invalid Title Pattern regex`);
        return;
      }
      if (v.time_pattern && !validateRegex(v.time_pattern)) {
        setError(`Variant "${v.name}" has an invalid Time Pattern regex`);
        return;
      }
      if (v.date_pattern && !validateRegex(v.date_pattern)) {
        setError(`Variant "${v.name}" has an invalid Date Pattern regex`);
        return;
      }
    }

    await execute(async () => {
      // Build data with backward compat: flat fields from variant[0]
      const v0 = variants[0];
      const data: DummyEPGProfileCreateRequest = {
        name: name.trim(),
        enabled,
        name_source: nameSource,
        stream_index: streamIndex,
        title_pattern: v0.title_pattern?.trim() || undefined,
        time_pattern: v0.time_pattern?.trim() || undefined,
        date_pattern: v0.date_pattern?.trim() || undefined,
        substitution_pairs: substitutionPairs,
        title_template: v0.title_template?.trim() || undefined,
        description_template: v0.description_template?.trim() || undefined,
        upcoming_title_template: upcomingTitleTemplate.trim() || undefined,
        upcoming_description_template: upcomingDescriptionTemplate.trim() || undefined,
        ended_title_template: endedTitleTemplate.trim() || undefined,
        ended_description_template: endedDescriptionTemplate.trim() || undefined,
        fallback_title_template: fallbackTitleTemplate.trim() || undefined,
        fallback_description_template: fallbackDescriptionTemplate.trim() || undefined,
        event_timezone: eventTimezone,
        output_timezone: outputTimezone || undefined,
        program_duration: programDuration,
        categories: categories.trim() || undefined,
        channel_logo_url_template: v0.channel_logo_url_template?.trim() || undefined,
        program_poster_url_template: v0.program_poster_url_template?.trim() || undefined,
        tvg_id_template: tvgIdTemplate.trim() || 'ecm-{channel_number}',
        include_date_tag: includeDateTag,
        include_live_tag: includeLiveTag,
        include_new_tag: includeNewTag,
        pattern_builder_examples: v0.pattern_builder_examples || undefined,
        pattern_variants: variants,
        channel_group_ids: channelGroupIds,
      };

      if (profile) {
        await api.updateDummyEPGProfile(profile.id, data);
      } else {
        await api.createDummyEPGProfile(data);
      }
      onSave();
      onClose();
    });
  };

  if (!isOpen) return null;

  return (
    <ModalOverlay onClose={onClose}>
      <div className="modal-container modal-xl dummy-epg-profile-modal">
        <div className="modal-header">
          <h2>{profile ? 'Edit Profile' : importData ? 'Import Dummy EPG Profile' : 'New Dummy EPG Profile'}</h2>
          <button className="modal-close-btn" onClick={onClose}>
            <span className="material-icons">close</span>
          </button>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            {/* Basic Info */}
            <div className="modal-form-group">
              <label htmlFor="depName">Name <span className="modal-required">*</span></label>
              <input
                id="depName"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="My Sports EPG"
                autoFocus
              />
            </div>

            <div className="modal-form-group">
              <label className="modal-checkbox-label">
                <input
                  type="checkbox"
                  checked={enabled}
                  onChange={(e) => setEnabled(e.target.checked)}
                />
                <span>Enabled</span>
              </label>
            </div>

            {/* Channel Groups */}
            <div className="modal-section-divider">
              <span>Channel Groups</span>
            </div>
            <p className="modal-section-description">
              Select the channel groups to generate EPG data for. All channels in the selected groups will be included.
            </p>

            {groupsLoading ? (
              <div className="dep-groups-loading">Loading groups...</div>
            ) : channelGroups.length === 0 ? (
              <div className="dep-groups-empty">No channel groups found in Dispatcharr</div>
            ) : (
              <div className="dep-group-selector">
                <div className="dep-group-search">
                  <span className="material-icons">search</span>
                  <input
                    type="text"
                    placeholder="Filter groups..."
                    value={groupSearchTerm}
                    onChange={(e) => setGroupSearchTerm(e.target.value)}
                  />
                  {groupSearchTerm && (
                    <button type="button" className="clear-search" onClick={() => setGroupSearchTerm('')}>
                      <span className="material-icons">close</span>
                    </button>
                  )}
                </div>
                <div className="dep-group-actions-row">
                  <span className="dep-group-count">{channelGroupIds.length} group{channelGroupIds.length !== 1 ? 's' : ''} selected</span>
                  {channelGroupIds.length > 0 && (
                    <button type="button" className="dep-group-clear-btn" onClick={() => setChannelGroupIds([])}>
                      Clear all
                    </button>
                  )}
                </div>
                <div className="dep-group-list">
                  {channelGroups
                    .filter(g => g.channel_count > 0)
                    .filter(g => !groupSearchTerm || g.name.toLowerCase().includes(groupSearchTerm.toLowerCase()))
                    .map(group => {
                      const isSelected = channelGroupIds.includes(group.id);
                      return (
                        <label
                          key={group.id}
                          className={`dep-group-item ${isSelected ? 'selected' : ''}`}
                        >
                          <input
                            type="checkbox"
                            checked={isSelected}
                            onChange={() => {
                              setChannelGroupIds(prev =>
                                isSelected
                                  ? prev.filter(id => id !== group.id)
                                  : [...prev, group.id]
                              );
                            }}
                          />
                          <span className="dep-group-name">{group.name}</span>
                          <span className="dep-group-ch-count">{group.channel_count} ch</span>
                        </label>
                      );
                    })}
                </div>
              </div>
            )}

            {/* Substitution Pairs */}
            <CollapsibleSection
              title="Substitution Pairs"
              isOpen={subsOpen}
              onToggle={() => setSubsOpen(!subsOpen)}
            >
              <div className="dep-collapsible-inner">
                <p className="modal-section-description">
                  Ordered find/replace rules applied to the name before pattern matching. Pairs are applied top-to-bottom.
                </p>
                <SubstitutionPairsEditor pairs={substitutionPairs} onChange={setSubstitutionPairs} />
              </div>
            </CollapsibleSection>

            {/* Name Source */}
            <div className="modal-section-divider">
              <span>Name Source</span>
            </div>

            <div className="modal-form-group">
              <label>Name Source <span className="modal-required">*</span></label>
              <div className="searchable-select-dropdown" ref={nameSourceDropdownRef}>
                <button
                  type="button"
                  className="dropdown-trigger"
                  onClick={() => setNameSourceDropdownOpen(!nameSourceDropdownOpen)}
                >
                  <span className="dropdown-value">
                    {nameSource === 'channel' ? 'Channel Name' : 'Stream Name'}
                  </span>
                  <span className="material-icons">expand_more</span>
                </button>
                {nameSourceDropdownOpen && (
                  <div className="dropdown-menu">
                    <div className="dropdown-options">
                      <div
                        className={`dropdown-option-item${nameSource === 'channel' ? ' selected' : ''}`}
                        onClick={() => { setNameSource('channel'); setNameSourceDropdownOpen(false); }}
                      >
                        Channel Name
                      </div>
                      <div
                        className={`dropdown-option-item${nameSource === 'stream' ? ' selected' : ''}`}
                        onClick={() => { setNameSource('stream'); setNameSourceDropdownOpen(false); }}
                      >
                        Stream Name
                      </div>
                    </div>
                  </div>
                )}
              </div>
              <p className="form-hint">Choose whether to parse the channel name or a stream name assigned to the channel</p>
            </div>

            {nameSource === 'stream' && (
              <div className="modal-form-group">
                <label htmlFor="depStreamIndex">Stream Index</label>
                <input
                  id="depStreamIndex"
                  type="number"
                  min="1"
                  value={streamIndex}
                  onChange={(e) => setStreamIndex(parseInt(e.target.value) || 1)}
                />
                <p className="form-hint">Which stream's name to use (1 = first stream)</p>
              </div>
            )}

            {/* Variant Tabs */}
            <div className="modal-section-divider">
              <span>Pattern Variants</span>
            </div>
            <p className="modal-section-description">
              Define multiple pattern variants. The engine tries each variant in order and uses the first match.
            </p>

            <VariantTabs
              variants={variants}
              activeIndex={activeVariantIndex}
              onSelect={setActiveVariantIndex}
              onAdd={handleAddVariant}
              onRename={handleRenameVariant}
              onDelete={handleDeleteVariant}
            />

            {/* Per-Variant Pattern Builder */}
            <PatternBuilder
              key={`pb-${activeVariantIndex}`}
              titlePattern={activeVariant.title_pattern || ''}
              timePattern={activeVariant.time_pattern || ''}
              datePattern={activeVariant.date_pattern || ''}
              onTitlePatternChange={(p) => updateActiveVariant({ title_pattern: p || null })}
              onTimePatternChange={(p) => updateActiveVariant({ time_pattern: p || null })}
              onDatePatternChange={(p) => updateActiveVariant({ date_pattern: p || null })}
              builderExamples={activeVariant.pattern_builder_examples}
              onBuilderExamplesChange={(json) => updateActiveVariant({ pattern_builder_examples: json })}
            />

            {/* Available Variables */}
            {(() => {
              const userGroups = extractGroupNames(activeVariant.title_pattern);
              const hasTime = Boolean(activeVariant.time_pattern);
              const hasDate = Boolean(activeVariant.date_pattern);
              const showPanel = userGroups.length > 0 || hasTime || hasDate;
              return showPanel ? (
                <div className="dep-available-vars">
                  <div className="dep-available-vars-title">Available Variables — {activeVariant.name}</div>
                  {userGroups.length > 0 && (
                    <div className="dep-var-section">
                      <span className="dep-var-section-label">Pattern</span>
                      {userGroups.map(g => <code key={g} className="dep-var-chip">{`{${g}}`}</code>)}
                    </div>
                  )}
                  {hasTime && (
                    <div className="dep-var-section">
                      <span className="dep-var-section-label">Time</span>
                      {['starttime', 'starttime24', 'endtime', 'endtime24'].map(v => <code key={v} className="dep-var-chip">{`{${v}}`}</code>)}
                    </div>
                  )}
                  {hasDate && (
                    <div className="dep-var-section">
                      <span className="dep-var-section-label">Date</span>
                      {['date', 'month', 'day', 'year'].map(v => <code key={v} className="dep-var-chip">{`{${v}}`}</code>)}
                    </div>
                  )}
                  <div className="dep-var-section">
                    <span className="dep-var-section-label">Built-in</span>
                    <code className="dep-var-chip">{'{original_name}'}</code>
                    <code className="dep-var-chip">{'{substituted_name}'}</code>
                  </div>
                  {userGroups.length > 0 && (
                    <div className="dep-var-hint">Tip: use <code className="dep-var-chip">{'{groupname_normalize}'}</code> variants for clean URLs</div>
                  )}
                </div>
              ) : null;
            })()}

            {/* Per-Variant Output Templates */}
            <div className="modal-section-divider">
              <span>Output Templates — {activeVariant.name}</span>
            </div>

            <p className="modal-section-description">
              Use extracted groups to format EPG titles and descriptions. Reference groups using &#123;groupname&#125; syntax. For clean URLs, use &#123;groupname_normalize&#125;.
            </p>

            <div className="modal-form-group">
              <label htmlFor="depTitleTemplate">Title Template</label>
              <input
                id="depTitleTemplate"
                type="text"
                value={activeVariant.title_template || ''}
                onChange={(e) => updateActiveVariant({ title_template: e.target.value || null })}
                placeholder="{league} - {team1} vs {team2}"
              />
              <p className="form-hint">Use &#123;starttime&#125;, &#123;starttime24&#125;, &#123;endtime&#125;, &#123;date&#125;, &#123;month&#125;, &#123;day&#125;, or &#123;year&#125;</p>
            </div>

            <div className="modal-form-group">
              <label htmlFor="depDescriptionTemplate">Description Template</label>
              <textarea
                id="depDescriptionTemplate"
                value={activeVariant.description_template || ''}
                onChange={(e) => updateActiveVariant({ description_template: e.target.value || null })}
                placeholder="Watch {team1} take on {team2} in this exciting {league} matchup!"
                rows={3}
              />
            </div>

            {/* Per-Variant Logo/Poster URLs */}
            <div className="modal-form-group">
              <label htmlFor="depChannelLogoUrl">Channel Logo URL</label>
              <input
                id="depChannelLogoUrl"
                type="text"
                value={activeVariant.channel_logo_url_template || ''}
                onChange={(e) => updateActiveVariant({ channel_logo_url_template: e.target.value || null })}
                placeholder="https://example.com/logos/{league_normalize}/{team1_normalize}.png"
              />
              <p className="form-hint">Use &#123;groupname_normalize&#125; for clean URLs</p>
            </div>
            <div className="modal-form-group">
              <label htmlFor="depProgramPosterUrl">Program Poster URL (Optional)</label>
              <input
                id="depProgramPosterUrl"
                type="text"
                value={activeVariant.program_poster_url_template || ''}
                onChange={(e) => updateActiveVariant({ program_poster_url_template: e.target.value || null })}
                placeholder="https://example.com/posters/{team1_normalize}-vs-{team2_normalize}.jpg"
              />
            </div>

            {/* Per-Variant Template Overrides (optional — collapse by default) */}
            <CollapsibleSection
              title={`Template Overrides — ${activeVariant.name} (Optional)`}
              isOpen={variantOverridesOpen}
              onToggle={() => setVariantOverridesOpen(!variantOverridesOpen)}
            >
              <div className="dep-collapsible-inner">
                <p className="modal-section-description">
                  Override the profile-level upcoming/ended/fallback templates for this variant. Leave blank to use profile defaults.
                </p>
                <div className="modal-form-group">
                  <label>Upcoming Title Override</label>
                  <input
                    type="text"
                    value={activeVariant.upcoming_title_template || ''}
                    onChange={(e) => updateActiveVariant({ upcoming_title_template: e.target.value || null })}
                    placeholder="Use profile default"
                  />
                </div>
                <div className="modal-form-group">
                  <label>Upcoming Description Override</label>
                  <textarea
                    value={activeVariant.upcoming_description_template || ''}
                    onChange={(e) => updateActiveVariant({ upcoming_description_template: e.target.value || null })}
                    placeholder="Use profile default"
                    rows={2}
                  />
                </div>
                <div className="modal-form-group">
                  <label>Ended Title Override</label>
                  <input
                    type="text"
                    value={activeVariant.ended_title_template || ''}
                    onChange={(e) => updateActiveVariant({ ended_title_template: e.target.value || null })}
                    placeholder="Use profile default"
                  />
                </div>
                <div className="modal-form-group">
                  <label>Ended Description Override</label>
                  <textarea
                    value={activeVariant.ended_description_template || ''}
                    onChange={(e) => updateActiveVariant({ ended_description_template: e.target.value || null })}
                    placeholder="Use profile default"
                    rows={2}
                  />
                </div>
                <div className="modal-form-group">
                  <label>Fallback Title Override</label>
                  <input
                    type="text"
                    value={activeVariant.fallback_title_template || ''}
                    onChange={(e) => updateActiveVariant({ fallback_title_template: e.target.value || null })}
                    placeholder="Use profile default"
                  />
                </div>
                <div className="modal-form-group">
                  <label>Fallback Description Override</label>
                  <textarea
                    value={activeVariant.fallback_description_template || ''}
                    onChange={(e) => updateActiveVariant({ fallback_description_template: e.target.value || null })}
                    placeholder="Use profile default"
                    rows={2}
                  />
                </div>
              </div>
            </CollapsibleSection>

            {/* Profile-level: Upcoming/Ended Templates */}
            <CollapsibleSection
              title="Upcoming/Ended Templates — Profile Defaults (Optional)"
              isOpen={upcomingEndedOpen}
              onToggle={() => setUpcomingEndedOpen(!upcomingEndedOpen)}
            >
              <div className="dep-collapsible-inner">
                <p className="modal-section-description">
                  Customize how programs appear before and after the event. Each variant uses these unless it has its own override.
                </p>
                <div className="modal-form-group">
                  <label htmlFor="depUpcomingTitle">Upcoming Title Template</label>
                  <input
                    id="depUpcomingTitle"
                    type="text"
                    value={upcomingTitleTemplate}
                    onChange={(e) => setUpcomingTitleTemplate(e.target.value)}
                    placeholder="{team1} vs {team2} starting at {starttime}"
                  />
                </div>
                <div className="modal-form-group">
                  <label htmlFor="depUpcomingDesc">Upcoming Description Template</label>
                  <textarea
                    id="depUpcomingDesc"
                    value={upcomingDescriptionTemplate}
                    onChange={(e) => setUpcomingDescriptionTemplate(e.target.value)}
                    placeholder="Upcoming: {team1} take on {team2} from {starttime} to {endtime}!"
                    rows={2}
                  />
                </div>
                <div className="modal-form-group">
                  <label htmlFor="depEndedTitle">Ended Title Template</label>
                  <input
                    id="depEndedTitle"
                    type="text"
                    value={endedTitleTemplate}
                    onChange={(e) => setEndedTitleTemplate(e.target.value)}
                    placeholder="{team1} vs {team2} started at {starttime}"
                  />
                </div>
                <div className="modal-form-group">
                  <label htmlFor="depEndedDesc">Ended Description Template</label>
                  <textarea
                    id="depEndedDesc"
                    value={endedDescriptionTemplate}
                    onChange={(e) => setEndedDescriptionTemplate(e.target.value)}
                    placeholder="The {league} match between {team1} and {team2} ran from {starttime} to {endtime}."
                    rows={2}
                  />
                </div>
              </div>
            </CollapsibleSection>

            {/* Profile-level: Fallback Templates */}
            <CollapsibleSection
              title="Fallback Templates — Profile Defaults (Optional)"
              isOpen={fallbackOpen}
              onToggle={() => setFallbackOpen(!fallbackOpen)}
            >
              <div className="dep-collapsible-inner">
                <p className="modal-section-description">
                  Used when no variant matches the channel/stream name.
                </p>
                <div className="modal-form-group">
                  <label htmlFor="depFallbackTitle">Fallback Title Template</label>
                  <input
                    id="depFallbackTitle"
                    type="text"
                    value={fallbackTitleTemplate}
                    onChange={(e) => setFallbackTitleTemplate(e.target.value)}
                    placeholder="No EPG data available"
                  />
                </div>
                <div className="modal-form-group">
                  <label htmlFor="depFallbackDesc">Fallback Description Template</label>
                  <textarea
                    id="depFallbackDesc"
                    value={fallbackDescriptionTemplate}
                    onChange={(e) => setFallbackDescriptionTemplate(e.target.value)}
                    placeholder="EPG information is currently unavailable for this channel."
                    rows={2}
                  />
                </div>
              </div>
            </CollapsibleSection>

            {/* EPG Settings */}
            <div className="modal-section-divider">
              <span>EPG Settings</span>
            </div>

            <div className="modal-form-row">
              <div className="modal-form-group">
                <label>Event Timezone</label>
                <div className="searchable-select-dropdown" ref={eventTimezoneDropdownRef}>
                  <button
                    type="button"
                    className="dropdown-trigger"
                    onClick={() => { setEventTimezoneDropdownOpen(!eventTimezoneDropdownOpen); setEventTimezoneSearch(''); }}
                  >
                    <span className="dropdown-value">
                      {TIMEZONES.find(tz => tz.value === eventTimezone)?.label || eventTimezone}
                    </span>
                    <span className="material-icons">expand_more</span>
                  </button>
                  {eventTimezoneDropdownOpen && (
                    <div className="dropdown-menu">
                      <div className="dropdown-search">
                        <span className="material-icons">search</span>
                        <input
                          type="text"
                          placeholder="Search timezones..."
                          value={eventTimezoneSearch}
                          onChange={(e) => setEventTimezoneSearch(e.target.value)}
                          autoFocus
                        />
                        {eventTimezoneSearch && (
                          <button type="button" className="clear-search" onClick={() => setEventTimezoneSearch('')}>
                            <span className="material-icons">close</span>
                          </button>
                        )}
                      </div>
                      <div className="dropdown-options">
                        {TIMEZONES.filter(tz => tz.value !== '' && tz.label.toLowerCase().includes(eventTimezoneSearch.toLowerCase())).map(tz => (
                          <div
                            key={tz.value}
                            className={`dropdown-option-item${eventTimezone === tz.value ? ' selected' : ''}`}
                            onClick={() => { setEventTimezone(tz.value); setEventTimezoneDropdownOpen(false); }}
                          >
                            {tz.label}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
                <p className="form-hint">Timezone of event times in channel titles</p>
              </div>

              <div className="modal-form-group">
                <label>Output Timezone (Optional)</label>
                <div className="searchable-select-dropdown" ref={outputTimezoneDropdownRef}>
                  <button
                    type="button"
                    className="dropdown-trigger"
                    onClick={() => { setOutputTimezoneDropdownOpen(!outputTimezoneDropdownOpen); setOutputTimezoneSearch(''); }}
                  >
                    <span className="dropdown-value">
                      {outputTimezone ? (TIMEZONES.find(tz => tz.value === outputTimezone)?.label || outputTimezone) : 'Same as event timezone'}
                    </span>
                    <span className="material-icons">expand_more</span>
                  </button>
                  {outputTimezoneDropdownOpen && (
                    <div className="dropdown-menu">
                      <div className="dropdown-search">
                        <span className="material-icons">search</span>
                        <input
                          type="text"
                          placeholder="Search timezones..."
                          value={outputTimezoneSearch}
                          onChange={(e) => setOutputTimezoneSearch(e.target.value)}
                          autoFocus
                        />
                        {outputTimezoneSearch && (
                          <button type="button" className="clear-search" onClick={() => setOutputTimezoneSearch('')}>
                            <span className="material-icons">close</span>
                          </button>
                        )}
                      </div>
                      <div className="dropdown-options">
                        {(!outputTimezoneSearch || 'same as event timezone'.includes(outputTimezoneSearch.toLowerCase())) && (
                          <div
                            className={`dropdown-option-item${outputTimezone === '' ? ' selected' : ''}`}
                            onClick={() => { setOutputTimezone(''); setOutputTimezoneDropdownOpen(false); }}
                          >
                            Same as event timezone
                          </div>
                        )}
                        {TIMEZONES.filter(tz => tz.value !== '' && tz.label.toLowerCase().includes(outputTimezoneSearch.toLowerCase())).map(tz => (
                          <div
                            key={tz.value}
                            className={`dropdown-option-item${outputTimezone === tz.value ? ' selected' : ''}`}
                            onClick={() => { setOutputTimezone(tz.value); setOutputTimezoneDropdownOpen(false); }}
                          >
                            {tz.label}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
                <p className="form-hint">Display times in a different timezone</p>
              </div>
            </div>

            <div className="modal-form-row">
              <div className="modal-form-group">
                <label htmlFor="depProgramDuration">Program Duration (minutes)</label>
                <input
                  id="depProgramDuration"
                  type="number"
                  min="1"
                  max="1440"
                  value={programDuration}
                  onChange={(e) => setProgramDuration(parseInt(e.target.value) || 180)}
                />
                <p className="form-hint">Default duration for each program</p>
              </div>

              <div className="modal-form-group">
                <label htmlFor="depCategories">Categories (Optional)</label>
                <input
                  id="depCategories"
                  type="text"
                  value={categories}
                  onChange={(e) => setCategories(e.target.value)}
                  placeholder="Sports, Live, HD"
                />
                <p className="form-hint">Comma-separated EPG categories</p>
              </div>
            </div>

            {/* EPG Tags */}
            <CollapsibleSection
              title="EPG Tags"
              isOpen={epgTagsOpen}
              onToggle={() => setEpgTagsOpen(!epgTagsOpen)}
            >
              <div className="dep-collapsible-inner">
                <div className="modal-form-group">
                  <label className="modal-checkbox-label">
                    <input type="checkbox" checked={includeDateTag} onChange={(e) => setIncludeDateTag(e.target.checked)} />
                    <span>Include Date Tag</span>
                  </label>
                  <p className="form-hint">Add &lt;date&gt; tag with program start date</p>
                </div>
                <div className="modal-form-group">
                  <label className="modal-checkbox-label">
                    <input type="checkbox" checked={includeLiveTag} onChange={(e) => setIncludeLiveTag(e.target.checked)} />
                    <span>Include Live Tag</span>
                  </label>
                  <p className="form-hint">Mark programs as live content (main event only)</p>
                </div>
                <div className="modal-form-group">
                  <label className="modal-checkbox-label">
                    <input type="checkbox" checked={includeNewTag} onChange={(e) => setIncludeNewTag(e.target.checked)} />
                    <span>Include New Tag</span>
                  </label>
                  <p className="form-hint">Mark programs as new content (main event only)</p>
                </div>
              </div>
            </CollapsibleSection>

            {/* Advanced Settings */}
            <CollapsibleSection
              title="Advanced Settings"
              isOpen={advancedOpen}
              onToggle={() => setAdvancedOpen(!advancedOpen)}
            >
              <div className="dep-collapsible-inner">
                <div className="modal-form-group">
                  <label htmlFor="depTvgIdTemplate">TVG ID Template</label>
                  <input
                    id="depTvgIdTemplate"
                    type="text"
                    value={tvgIdTemplate}
                    onChange={(e) => setTvgIdTemplate(e.target.value)}
                    placeholder="ecm-{channel_number}"
                  />
                  <p className="form-hint">Template for tvg-id in XMLTV output. Use &#123;channel_number&#125; for the channel number. This must match the tvg-id used in Dispatcharr for channel matching.</p>
                </div>
              </div>
            </CollapsibleSection>

            {/* Batch Test */}
            <div className="modal-section-divider">
              <span>Batch Test</span>
            </div>

            <p className="modal-section-description">
              Paste multiple channel/stream names (one per line) to test which variant matches each.
            </p>

            <div className="modal-form-group">
              <label htmlFor="depBatchInput">Sample Names</label>
              <textarea
                id="depBatchInput"
                value={batchInput}
                onChange={(e) => setBatchInput(e.target.value)}
                placeholder={"ESPN+ 17 : Ohio vs Notre Dame @ Feb 20 8:00PM ET\nPPV: UFC 300 Main Card\nNFL 12 : Cowboys VS Eagles @ Oct 17 1:00PM"}
                rows={4}
                style={{ fontFamily: "'JetBrains Mono', 'Fira Code', monospace", fontSize: '0.8rem' }}
              />
            </div>

            <button
              type="button"
              className="modal-btn modal-btn-secondary"
              onClick={handleBatchTest}
              disabled={batchLoading || !batchInput.trim()}
              style={{ marginBottom: '0.75rem' }}
            >
              {batchLoading ? 'Testing...' : 'Test All'}
            </button>

            {batchResults.length > 0 && (
              <div className="dep-batch-results">
                <div className="dep-batch-header">
                  <span>Name</span>
                  <span>Variant</span>
                  <span>Title Output</span>
                  <span>Status</span>
                </div>
                {batchResults.map((r, i) => {
                  const isExpanded = expandedBatchRows.has(i);
                  const toggleRow = () => {
                    setExpandedBatchRows(prev => {
                      const next = new Set(prev);
                      if (next.has(i)) next.delete(i); else next.add(i);
                      return next;
                    });
                  };
                  // Collect detail rows to show (skip empty values)
                  const detailFields: Array<{ label: string; value: string }> = [];
                  if (r.matched && r.rendered) {
                    if (r.rendered.title) detailFields.push({ label: 'Title', value: r.rendered.title });
                    if (r.rendered.description) detailFields.push({ label: 'Description', value: r.rendered.description });
                    if (r.rendered.channel_logo_url) detailFields.push({ label: 'Channel Logo URL', value: r.rendered.channel_logo_url });
                    if (r.rendered.program_poster_url) detailFields.push({ label: 'Program Poster URL', value: r.rendered.program_poster_url });
                    if (r.rendered.upcoming_title) detailFields.push({ label: 'Upcoming Title', value: r.rendered.upcoming_title });
                    if (r.rendered.upcoming_description) detailFields.push({ label: 'Upcoming Desc', value: r.rendered.upcoming_description });
                    if (r.rendered.ended_title) detailFields.push({ label: 'Ended Title', value: r.rendered.ended_title });
                    if (r.rendered.ended_description) detailFields.push({ label: 'Ended Desc', value: r.rendered.ended_description });
                  } else if (r.rendered) {
                    if (r.rendered.fallback_title) detailFields.push({ label: 'Fallback Title', value: r.rendered.fallback_title });
                    if (r.rendered.fallback_description) detailFields.push({ label: 'Fallback Desc', value: r.rendered.fallback_description });
                  }
                  const hasGroups = r.groups && Object.keys(r.groups).length > 0;
                  const hasTimeVars = r.time_variables && Object.keys(r.time_variables).length > 0;

                  return (
                    <div key={i} className={`dep-batch-row-wrap ${r.matched ? 'dep-batch-match' : 'dep-batch-no-match'}`}>
                      <div className="dep-batch-summary" onClick={toggleRow}>
                        <span className="dep-batch-name" title={r.original_name}>
                          {r.original_name.length > 40 ? r.original_name.slice(0, 40) + '...' : r.original_name}
                        </span>
                        <span className="dep-batch-variant">
                          {r.matched_variant || '—'}
                        </span>
                        <span className="dep-batch-title">
                          {r.matched ? (r.rendered?.title || '—') : (r.rendered?.fallback_title || '—')}
                        </span>
                        <span className="dep-batch-status">
                          <span className={`material-icons ${r.matched ? 'dep-batch-icon-match' : 'dep-batch-icon-fail'}`}>
                            {r.matched ? 'check_circle' : 'cancel'}
                          </span>
                        </span>
                      </div>
                      {isExpanded && (
                        <div className="dep-batch-detail">
                          {(hasGroups || hasTimeVars) && (
                            <div className="dep-batch-detail-row">
                              <span className="dep-batch-detail-label">Variables</span>
                              <div className="dep-batch-groups">
                                {hasGroups && Object.entries(r.groups!).map(([k, v]) => (
                                  <span key={k} className="dep-batch-group-chip"><strong>{`{${k}}`}</strong> = &quot;{v}&quot;</span>
                                ))}
                                {hasTimeVars && Object.entries(r.time_variables!).map(([k, v]) => (
                                  <span key={k} className="dep-batch-group-chip"><strong>{`{${k}}`}</strong> = &quot;{v}&quot;</span>
                                ))}
                              </div>
                            </div>
                          )}
                          {detailFields.map(({ label, value }) => (
                            <div key={label} className="dep-batch-detail-row">
                              <span className="dep-batch-detail-label">{label}</span>
                              <span className="dep-batch-detail-value">{value}</span>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}

            {error && <div className="modal-error-banner">{error}</div>}
          </div>

          <div className="modal-footer modal-footer-spread">
            <button type="button" className="modal-btn modal-btn-secondary" onClick={onClose}>
              Cancel
            </button>
            <button type="submit" className="modal-btn modal-btn-primary" disabled={saving}>
              {saving ? 'Saving...' : profile ? 'Save Changes' : 'Create Profile'}
            </button>
          </div>
        </form>
      </div>
    </ModalOverlay>
  );
});
