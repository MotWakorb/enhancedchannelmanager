import { useState, useEffect, useMemo, useCallback } from 'react';
import type { EPGSource, DummyEPGCustomProperties } from '../types';
import type { CreateEPGSourceRequest } from '../services/api';
import './DummyEPGSourceModal.css';

// Common timezones for the dropdown
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

interface DummyEPGSourceModalProps {
  isOpen: boolean;
  source: EPGSource | null;  // null for new source
  onClose: () => void;
  onSave: (data: CreateEPGSourceRequest) => Promise<void>;
}

interface CollapsibleSectionProps {
  title: string;
  isOpen: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}

function CollapsibleSection({ title, isOpen, onToggle, children }: CollapsibleSectionProps) {
  return (
    <div className="collapsible-section">
      <button type="button" className="collapsible-header" onClick={onToggle}>
        <span className="material-icons">{isOpen ? 'expand_less' : 'expand_more'}</span>
        <span>{title}</span>
      </button>
      {isOpen && <div className="collapsible-content">{children}</div>}
    </div>
  );
}

export function DummyEPGSourceModal({ isOpen, source, onClose, onSave }: DummyEPGSourceModalProps) {
  // Basic Info
  const [name, setName] = useState('');
  const [isActive, setIsActive] = useState(true);

  // Pattern Configuration
  const [nameSource, setNameSource] = useState<'channel' | 'stream'>('channel');
  const [streamIndex, setStreamIndex] = useState<number>(1);
  const [titlePattern, setTitlePattern] = useState('');
  const [timePattern, setTimePattern] = useState('');
  const [datePattern, setDatePattern] = useState('');

  // Output Templates
  const [titleTemplate, setTitleTemplate] = useState('');
  const [descriptionTemplate, setDescriptionTemplate] = useState('');

  // Upcoming/Ended Templates
  const [upcomingTitleTemplate, setUpcomingTitleTemplate] = useState('');
  const [upcomingDescriptionTemplate, setUpcomingDescriptionTemplate] = useState('');
  const [endedTitleTemplate, setEndedTitleTemplate] = useState('');
  const [endedDescriptionTemplate, setEndedDescriptionTemplate] = useState('');

  // Fallback Templates
  const [fallbackTitleTemplate, setFallbackTitleTemplate] = useState('');
  const [fallbackDescriptionTemplate, setFallbackDescriptionTemplate] = useState('');

  // EPG Settings
  const [eventTimezone, setEventTimezone] = useState('US/Eastern');
  const [outputTimezone, setOutputTimezone] = useState('');
  const [programDuration, setProgramDuration] = useState(180);
  const [categories, setCategories] = useState('');
  const [channelLogoUrl, setChannelLogoUrl] = useState('');
  const [programPosterUrl, setProgramPosterUrl] = useState('');
  const [includeDateTag, setIncludeDateTag] = useState(false);
  const [includeLiveTag, setIncludeLiveTag] = useState(false);
  const [includeNewTag, setIncludeNewTag] = useState(false);

  // Test Configuration
  const [sampleChannelName, setSampleChannelName] = useState('');

  // UI State
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [titlePatternError, setTitlePatternError] = useState<string | null>(null);
  const [timePatternError, setTimePatternError] = useState<string | null>(null);
  const [datePatternError, setDatePatternError] = useState<string | null>(null);

  // Collapsible sections state
  const [upcomingEndedOpen, setUpcomingEndedOpen] = useState(false);
  const [fallbackOpen, setFallbackOpen] = useState(false);
  const [logoUrlsOpen, setLogoUrlsOpen] = useState(false);
  const [epgTagsOpen, setEpgTagsOpen] = useState(false);

  // Load source data when modal opens
  useEffect(() => {
    if (isOpen) {
      if (source) {
        const props = (source.custom_properties as DummyEPGCustomProperties) || {};
        setName(source.name);
        setIsActive(source.is_active);
        setNameSource(props.name_source || 'channel');
        setStreamIndex(props.stream_index || 1);
        setTitlePattern(props.title_pattern || '');
        setTimePattern(props.time_pattern || '');
        setDatePattern(props.date_pattern || '');
        setTitleTemplate(props.title_template || '');
        setDescriptionTemplate(props.description_template || '');
        setUpcomingTitleTemplate(props.upcoming_title_template || '');
        setUpcomingDescriptionTemplate(props.upcoming_description_template || '');
        setEndedTitleTemplate(props.ended_title_template || '');
        setEndedDescriptionTemplate(props.ended_description_template || '');
        setFallbackTitleTemplate(props.fallback_title_template || '');
        setFallbackDescriptionTemplate(props.fallback_description_template || '');
        setEventTimezone(props.event_timezone || 'US/Eastern');
        setOutputTimezone(props.output_timezone || '');
        setProgramDuration(props.program_duration || 180);
        setCategories(props.categories || '');
        setChannelLogoUrl(props.channel_logo_url || '');
        setProgramPosterUrl(props.program_poster_url || '');
        setIncludeDateTag(props.include_date_tag || false);
        setIncludeLiveTag(props.include_live_tag || false);
        setIncludeNewTag(props.include_new_tag || false);

        // Open collapsible sections if they have content
        setUpcomingEndedOpen(Boolean(props.upcoming_title_template || props.upcoming_description_template || props.ended_title_template || props.ended_description_template));
        setFallbackOpen(Boolean(props.fallback_title_template || props.fallback_description_template));
        setLogoUrlsOpen(Boolean(props.channel_logo_url || props.program_poster_url));
        setEpgTagsOpen(Boolean(props.include_date_tag || props.include_live_tag || props.include_new_tag));
      } else {
        // Reset to defaults for new source
        setName('');
        setIsActive(true);
        setNameSource('channel');
        setStreamIndex(1);
        setTitlePattern('');
        setTimePattern('');
        setDatePattern('');
        setTitleTemplate('');
        setDescriptionTemplate('');
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
        setChannelLogoUrl('');
        setProgramPosterUrl('');
        setIncludeDateTag(false);
        setIncludeLiveTag(false);
        setIncludeNewTag(false);
        setUpcomingEndedOpen(false);
        setFallbackOpen(false);
        setLogoUrlsOpen(false);
        setEpgTagsOpen(false);
      }
      setSampleChannelName('');
      setError(null);
      setTitlePatternError(null);
      setTimePatternError(null);
      setDatePatternError(null);
    }
  }, [isOpen, source]);

  // Validate regex pattern
  const validateRegex = useCallback((pattern: string, setError: (error: string | null) => void) => {
    if (!pattern) {
      setError(null);
      return true;
    }
    try {
      new RegExp(pattern);
      setError(null);
      return true;
    } catch {
      setError('Invalid regex pattern');
      return false;
    }
  }, []);

  // Extract named groups from regex match
  const extractGroups = useCallback((text: string, pattern: string): Record<string, string> | null => {
    if (!pattern || !text) return null;
    try {
      const regex = new RegExp(pattern);
      const match = text.match(regex);
      if (!match || !match.groups) return null;
      return match.groups;
    } catch {
      return null;
    }
  }, []);

  // Apply template with extracted groups
  const applyTemplate = useCallback((template: string, groups: Record<string, string>): string => {
    if (!template) return '';
    let result = template;
    for (const [key, value] of Object.entries(groups)) {
      // Replace {key} and {key_normalize}
      result = result.replace(new RegExp(`\\{${key}\\}`, 'g'), value);
      result = result.replace(new RegExp(`\\{${key}_normalize\\}`, 'g'), value.toLowerCase().replace(/[^a-z0-9]/g, ''));
    }
    return result;
  }, []);

  // Live preview computation
  const preview = useMemo(() => {
    if (!sampleChannelName || !titlePattern) {
      return { groups: null, title: '', description: '' };
    }

    const titleGroups = extractGroups(sampleChannelName, titlePattern);
    if (!titleGroups) {
      return { groups: null, title: fallbackTitleTemplate || sampleChannelName, description: fallbackDescriptionTemplate || '' };
    }

    // Merge with time groups if time pattern is provided
    let allGroups = { ...titleGroups };
    if (timePattern) {
      const timeGroups = extractGroups(sampleChannelName, timePattern);
      if (timeGroups) {
        allGroups = { ...allGroups, ...timeGroups };
      }
    }

    // Merge with date groups if date pattern is provided
    if (datePattern) {
      const dateGroups = extractGroups(sampleChannelName, datePattern);
      if (dateGroups) {
        allGroups = { ...allGroups, ...dateGroups };
      }
    }

    const title = titleTemplate ? applyTemplate(titleTemplate, allGroups) : '';
    const description = descriptionTemplate ? applyTemplate(descriptionTemplate, allGroups) : '';

    return { groups: allGroups, title, description };
  }, [sampleChannelName, titlePattern, timePattern, datePattern, titleTemplate, descriptionTemplate, fallbackTitleTemplate, fallbackDescriptionTemplate, extractGroups, applyTemplate]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    // Validation
    if (!name.trim()) {
      setError('Name is required');
      return;
    }

    if (!titlePattern.trim()) {
      setError('Title Pattern is required');
      return;
    }

    if (!validateRegex(titlePattern, setTitlePatternError)) {
      setError('Title Pattern has invalid regex');
      return;
    }

    if (timePattern && !validateRegex(timePattern, setTimePatternError)) {
      setError('Time Pattern has invalid regex');
      return;
    }

    if (datePattern && !validateRegex(datePattern, setDatePatternError)) {
      setError('Date Pattern has invalid regex');
      return;
    }

    setSaving(true);
    try {
      const customProperties: DummyEPGCustomProperties = {
        name_source: nameSource,
        title_pattern: titlePattern.trim(),
      };

      // Only include optional fields if they have values
      if (nameSource === 'stream' && streamIndex > 0) customProperties.stream_index = streamIndex;
      if (timePattern.trim()) customProperties.time_pattern = timePattern.trim();
      if (datePattern.trim()) customProperties.date_pattern = datePattern.trim();
      if (titleTemplate.trim()) customProperties.title_template = titleTemplate.trim();
      if (descriptionTemplate.trim()) customProperties.description_template = descriptionTemplate.trim();
      if (upcomingTitleTemplate.trim()) customProperties.upcoming_title_template = upcomingTitleTemplate.trim();
      if (upcomingDescriptionTemplate.trim()) customProperties.upcoming_description_template = upcomingDescriptionTemplate.trim();
      if (endedTitleTemplate.trim()) customProperties.ended_title_template = endedTitleTemplate.trim();
      if (endedDescriptionTemplate.trim()) customProperties.ended_description_template = endedDescriptionTemplate.trim();
      if (fallbackTitleTemplate.trim()) customProperties.fallback_title_template = fallbackTitleTemplate.trim();
      if (fallbackDescriptionTemplate.trim()) customProperties.fallback_description_template = fallbackDescriptionTemplate.trim();
      if (eventTimezone) customProperties.event_timezone = eventTimezone;
      if (outputTimezone) customProperties.output_timezone = outputTimezone;
      if (programDuration !== 180) customProperties.program_duration = programDuration;
      if (categories.trim()) customProperties.categories = categories.trim();
      if (channelLogoUrl.trim()) customProperties.channel_logo_url = channelLogoUrl.trim();
      if (programPosterUrl.trim()) customProperties.program_poster_url = programPosterUrl.trim();
      if (includeDateTag) customProperties.include_date_tag = true;
      if (includeLiveTag) customProperties.include_live_tag = true;
      if (includeNewTag) customProperties.include_new_tag = true;

      await onSave({
        name: name.trim(),
        source_type: 'dummy',
        is_active: isActive,
        custom_properties: customProperties,
      });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save dummy EPG source');
    } finally {
      setSaving(false);
    }
  };

  const handleClearAll = () => {
    setTitlePattern('');
    setTimePattern('');
    setDatePattern('');
    setTitleTemplate('');
    setDescriptionTemplate('');
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
    setChannelLogoUrl('');
    setProgramPosterUrl('');
    setIncludeDateTag(false);
    setIncludeLiveTag(false);
    setIncludeNewTag(false);
    setSampleChannelName('');
  };

  if (!isOpen) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content dummy-epg-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>{source ? 'Edit Dummy EPG Source' : 'Add Dummy EPG Source'}</h2>
          <button className="close-btn" onClick={onClose}>&times;</button>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            {/* Basic Info */}
            <div className="form-group">
              <label htmlFor="name">Name <span className="required">*</span></label>
              <input
                id="name"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="NCAA Football EPG"
                autoFocus
              />
            </div>

            <div className="form-group checkbox-group">
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={isActive}
                  onChange={(e) => setIsActive(e.target.checked)}
                />
                <span>Active</span>
              </label>
            </div>

            {/* Pattern Configuration Section */}
            <div className="section-divider">
              <span>Pattern Configuration</span>
            </div>

            <p className="section-description">
              Define regex patterns to extract information from channel titles or stream names. Use named capture groups like (?&lt;groupname&gt;pattern).
            </p>

            <div className="form-group">
              <label htmlFor="nameSource">Name Source <span className="required">*</span></label>
              <select
                id="nameSource"
                value={nameSource}
                onChange={(e) => setNameSource(e.target.value as 'channel' | 'stream')}
              >
                <option value="channel">Channel Name</option>
                <option value="stream">Stream Name</option>
              </select>
              <p className="form-hint">Choose whether to parse the channel name or a stream name assigned to the channel</p>
            </div>

            {nameSource === 'stream' && (
              <div className="form-group">
                <label htmlFor="streamIndex">Stream Index</label>
                <input
                  id="streamIndex"
                  type="number"
                  min="1"
                  value={streamIndex}
                  onChange={(e) => setStreamIndex(parseInt(e.target.value) || 1)}
                />
                <p className="form-hint">Which stream's name to use (1 = first stream)</p>
              </div>
            )}

            <div className="form-group">
              <label htmlFor="titlePattern">Title Pattern <span className="required">*</span></label>
              <input
                id="titlePattern"
                type="text"
                value={titlePattern}
                onChange={(e) => setTitlePattern(e.target.value)}
                onBlur={() => validateRegex(titlePattern, setTitlePatternError)}
                placeholder="(?<league>\w+) \d+: (?<team1>.*) VS (?<team2>.*)"
                className={titlePatternError ? 'error' : ''}
              />
              {titlePatternError && <span className="form-error">{titlePatternError}</span>}
              <p className="form-hint">Regex pattern to extract title information (e.g., team names, league). Example: (?&lt;league&gt;\w+) \d+: (?&lt;team1&gt;.*) VS (?&lt;team2&gt;.*)</p>
            </div>

            <div className="form-group">
              <label htmlFor="timePattern">Time Pattern (Optional)</label>
              <input
                id="timePattern"
                type="text"
                value={timePattern}
                onChange={(e) => setTimePattern(e.target.value)}
                onBlur={() => validateRegex(timePattern, setTimePatternError)}
                placeholder="@ (?<hour>\d+):(?<minute>\d+)(?<ampm>AM|PM)"
                className={timePatternError ? 'error' : ''}
              />
              {timePatternError && <span className="form-error">{timePatternError}</span>}
              <p className="form-hint">Extract time from channel titles. Required groups: 'hour' (1-12 or 0-23), 'minute' (0-59), 'ampm' (AM/PM - optional for 24-hour)</p>
            </div>

            <div className="form-group">
              <label htmlFor="datePattern">Date Pattern (Optional)</label>
              <input
                id="datePattern"
                type="text"
                value={datePattern}
                onChange={(e) => setDatePattern(e.target.value)}
                onBlur={() => validateRegex(datePattern, setDatePatternError)}
                placeholder="@ (?<month>\w+) (?<day>\d+)"
                className={datePatternError ? 'error' : ''}
              />
              {datePatternError && <span className="form-error">{datePatternError}</span>}
              <p className="form-hint">Extract date from channel titles. Groups: 'month' (name or number), 'day', 'year' (optional, defaults to current year)</p>
            </div>

            {/* Output Templates Section */}
            <div className="section-divider">
              <span>Output Templates (Optional)</span>
            </div>

            <p className="section-description">
              Use extracted groups from your patterns to format EPG titles and descriptions. Reference groups using &#123;groupname&#125; syntax. For cleaner URLs, use &#123;groupname_normalize&#125; to get alphanumeric-only lowercase versions.
            </p>

            <div className="form-group">
              <label htmlFor="titleTemplate">Title Template</label>
              <input
                id="titleTemplate"
                type="text"
                value={titleTemplate}
                onChange={(e) => setTitleTemplate(e.target.value)}
                placeholder="{league} - {team1} vs {team2}"
              />
              <p className="form-hint">Format the EPG title using extracted groups. Use &#123;starttime&#125; (12-hour: '10 PM'), &#123;starttime24&#125; (24-hour: '22:00'), &#123;endtime&#125;, &#123;date&#125;, &#123;month&#125;, &#123;day&#125;, or &#123;year&#125;</p>
            </div>

            <div className="form-group">
              <label htmlFor="descriptionTemplate">Description Template</label>
              <textarea
                id="descriptionTemplate"
                value={descriptionTemplate}
                onChange={(e) => setDescriptionTemplate(e.target.value)}
                placeholder="Watch {team1} take on {team2} in this exciting {league} matchup from {starttime} to {endtime}!"
                rows={3}
              />
              <p className="form-hint">Format the EPG description using extracted groups</p>
            </div>

            {/* Collapsible Sections */}
            <CollapsibleSection
              title="Upcoming/Ended Templates (Optional)"
              isOpen={upcomingEndedOpen}
              onToggle={() => setUpcomingEndedOpen(!upcomingEndedOpen)}
            >
              <p className="section-description">
                Customize how programs appear before and after the event. If left empty, will use the main title/description with "Upcoming:" or "Ended:" prefix.
              </p>

              <div className="form-group">
                <label htmlFor="upcomingTitleTemplate">Upcoming Title Template</label>
                <input
                  id="upcomingTitleTemplate"
                  type="text"
                  value={upcomingTitleTemplate}
                  onChange={(e) => setUpcomingTitleTemplate(e.target.value)}
                  placeholder="{team1} vs {team2} starting at {starttime}."
                />
              </div>

              <div className="form-group">
                <label htmlFor="upcomingDescriptionTemplate">Upcoming Description Template</label>
                <textarea
                  id="upcomingDescriptionTemplate"
                  value={upcomingDescriptionTemplate}
                  onChange={(e) => setUpcomingDescriptionTemplate(e.target.value)}
                  placeholder="Upcoming: Watch the {league} match up where the {team1} take on the {team2} from {starttime} to {endtime}!"
                  rows={2}
                />
              </div>

              <div className="form-group">
                <label htmlFor="endedTitleTemplate">Ended Title Template</label>
                <input
                  id="endedTitleTemplate"
                  type="text"
                  value={endedTitleTemplate}
                  onChange={(e) => setEndedTitleTemplate(e.target.value)}
                  placeholder="{team1} vs {team2} started at {starttime}."
                />
              </div>

              <div className="form-group">
                <label htmlFor="endedDescriptionTemplate">Ended Description Template</label>
                <textarea
                  id="endedDescriptionTemplate"
                  value={endedDescriptionTemplate}
                  onChange={(e) => setEndedDescriptionTemplate(e.target.value)}
                  placeholder="The {league} match between {team1} and {team2} ran from {starttime} to {endtime}."
                  rows={2}
                />
              </div>
            </CollapsibleSection>

            <CollapsibleSection
              title="Fallback Templates (Optional)"
              isOpen={fallbackOpen}
              onToggle={() => setFallbackOpen(!fallbackOpen)}
            >
              <p className="section-description">
                When patterns don't match the channel/stream name, use these custom fallback templates instead of the default placeholder messages. Leave empty to use the built-in humorous fallback descriptions.
              </p>

              <div className="form-group">
                <label htmlFor="fallbackTitleTemplate">Fallback Title Template</label>
                <input
                  id="fallbackTitleTemplate"
                  type="text"
                  value={fallbackTitleTemplate}
                  onChange={(e) => setFallbackTitleTemplate(e.target.value)}
                  placeholder="No EPG data available"
                />
                <p className="form-hint">Custom title when patterns don't match. If empty, uses the channel/stream name</p>
              </div>

              <div className="form-group">
                <label htmlFor="fallbackDescriptionTemplate">Fallback Description Template</label>
                <textarea
                  id="fallbackDescriptionTemplate"
                  value={fallbackDescriptionTemplate}
                  onChange={(e) => setFallbackDescriptionTemplate(e.target.value)}
                  placeholder="EPG information is currently unavailable for this channel."
                  rows={2}
                />
                <p className="form-hint">Custom description when patterns don't match. If empty, uses built-in placeholder messages</p>
              </div>
            </CollapsibleSection>

            {/* EPG Settings Section */}
            <div className="section-divider">
              <span>EPG Settings</span>
            </div>

            <div className="form-row">
              <div className="form-group">
                <label htmlFor="eventTimezone">Event Timezone</label>
                <select
                  id="eventTimezone"
                  value={eventTimezone}
                  onChange={(e) => setEventTimezone(e.target.value)}
                >
                  {TIMEZONES.filter(tz => tz.value !== '').map(tz => (
                    <option key={tz.value} value={tz.value}>{tz.label}</option>
                  ))}
                </select>
                <p className="form-hint">The timezone of event times in your channel titles. DST is handled automatically!</p>
              </div>

              <div className="form-group">
                <label htmlFor="outputTimezone">Output Timezone (Optional)</label>
                <select
                  id="outputTimezone"
                  value={outputTimezone}
                  onChange={(e) => setOutputTimezone(e.target.value)}
                >
                  <option value="">Same as event timezone</option>
                  {TIMEZONES.filter(tz => tz.value !== '').map(tz => (
                    <option key={tz.value} value={tz.value}>{tz.label}</option>
                  ))}
                </select>
                <p className="form-hint">Display times in a different timezone than the event timezone</p>
              </div>
            </div>

            <div className="form-row">
              <div className="form-group">
                <label htmlFor="programDuration">Program Duration (minutes)</label>
                <input
                  id="programDuration"
                  type="number"
                  min="1"
                  max="1440"
                  value={programDuration}
                  onChange={(e) => setProgramDuration(parseInt(e.target.value) || 180)}
                />
                <p className="form-hint">Default duration for each program</p>
              </div>

              <div className="form-group">
                <label htmlFor="categories">Categories (Optional)</label>
                <input
                  id="categories"
                  type="text"
                  value={categories}
                  onChange={(e) => setCategories(e.target.value)}
                  placeholder="Sports, Live, HD"
                />
                <p className="form-hint">EPG categories (comma-separated). Note: Only added to the main event, not upcoming/ended filler programs</p>
              </div>
            </div>

            <CollapsibleSection
              title="Logo/Poster URLs (Optional)"
              isOpen={logoUrlsOpen}
              onToggle={() => setLogoUrlsOpen(!logoUrlsOpen)}
            >
              <div className="form-group">
                <label htmlFor="channelLogoUrl">Channel Logo URL</label>
                <input
                  id="channelLogoUrl"
                  type="text"
                  value={channelLogoUrl}
                  onChange={(e) => setChannelLogoUrl(e.target.value)}
                  placeholder="https://example.com/logos/{league_normalize}/{team1_normalize}.png"
                />
                <p className="form-hint">Build a URL for the channel logo using regex groups. Use &#123;groupname_normalize&#125; for cleaner URLs (alphanumeric-only, lowercase). This will be used as the channel &lt;icon&gt; in the EPG output.</p>
              </div>

              <div className="form-group">
                <label htmlFor="programPosterUrl">Program Poster URL (Optional)</label>
                <input
                  id="programPosterUrl"
                  type="text"
                  value={programPosterUrl}
                  onChange={(e) => setProgramPosterUrl(e.target.value)}
                  placeholder="https://example.com/posters/{team1_normalize}-vs-{team2_normalize}.jpg"
                />
                <p className="form-hint">Build a URL for the program poster/icon using regex groups. This will be used as the program &lt;icon&gt; in the EPG output.</p>
              </div>
            </CollapsibleSection>

            <CollapsibleSection
              title="EPG Tags"
              isOpen={epgTagsOpen}
              onToggle={() => setEpgTagsOpen(!epgTagsOpen)}
            >
              <div className="form-group checkbox-group">
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={includeDateTag}
                    onChange={(e) => setIncludeDateTag(e.target.checked)}
                  />
                  <span>Include Date Tag</span>
                </label>
                <p className="form-hint">Include the &lt;date&gt; tag in EPG output with the program's start date (YYYY-MM-DD format). Added to all programs.</p>
              </div>

              <div className="form-group checkbox-group">
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={includeLiveTag}
                    onChange={(e) => setIncludeLiveTag(e.target.checked)}
                  />
                  <span>Include Live Tag</span>
                </label>
                <p className="form-hint">Mark programs as live content with the &lt;live /&gt; tag in EPG output. Note: Only added to the main event, not upcoming/ended filler programs.</p>
              </div>

              <div className="form-group checkbox-group">
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    checked={includeNewTag}
                    onChange={(e) => setIncludeNewTag(e.target.checked)}
                  />
                  <span>Include New Tag</span>
                </label>
                <p className="form-hint">Mark programs as new content with the &lt;new /&gt; tag in EPG output. Note: Only added to the main event, not upcoming/ended filler programs.</p>
              </div>
            </CollapsibleSection>

            {/* Test Your Configuration Section */}
            <div className="section-divider">
              <span>Test Your Configuration</span>
            </div>

            <p className="section-description">
              Test your patterns and templates with a sample channel name to preview the output.
            </p>

            <div className="form-group">
              <label htmlFor="sampleChannelName">Sample Channel Name</label>
              <input
                id="sampleChannelName"
                type="text"
                value={sampleChannelName}
                onChange={(e) => setSampleChannelName(e.target.value)}
                placeholder="League 01: Team 1 VS Team 2 @ Oct 17 8:00PM ET"
              />
              <p className="form-hint">Enter a sample channel name to test pattern matching and see the formatted output</p>
            </div>

            {sampleChannelName && (
              <div className="preview-section">
                <h4>Preview:</h4>
                {preview.groups ? (
                  <>
                    <div className="preview-groups">
                      <strong>Extracted Groups:</strong>
                      <code>{JSON.stringify(preview.groups, null, 2)}</code>
                    </div>
                    {preview.title && (
                      <div className="preview-item">
                        <strong>Title:</strong> {preview.title}
                      </div>
                    )}
                    {preview.description && (
                      <div className="preview-item">
                        <strong>Description:</strong> {preview.description}
                      </div>
                    )}
                  </>
                ) : (
                  <div className="preview-no-match">
                    <span className="material-icons">warning</span>
                    <span>Pattern did not match. {fallbackTitleTemplate ? `Using fallback: "${fallbackTitleTemplate}"` : 'Using channel name as title.'}</span>
                  </div>
                )}
              </div>
            )}

            {error && <div className="error-message">{error}</div>}
          </div>

          <div className="modal-footer">
            <button type="button" className="btn-text" onClick={handleClearAll}>
              Clear All
            </button>
            <div className="footer-buttons">
              <button type="button" className="btn-secondary" onClick={onClose} disabled={saving}>
                Cancel
              </button>
              <button type="submit" className="btn-primary" disabled={saving}>
                {saving ? 'Saving...' : source ? 'Save Changes' : 'Add Dummy EPG'}
              </button>
            </div>
          </div>
        </form>
      </div>
    </div>
  );
}
