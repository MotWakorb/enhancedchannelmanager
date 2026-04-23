import { useState, useEffect, useMemo, useCallback, memo, useRef } from 'react';
import type { EPGSource, DummyEPGCustomProperties, DummyEPGPreviewResult, DummyEPGPreviewTraceStep } from '../types';
import type { CreateEPGSourceRequest } from '../services/api';
import { useAsyncOperation } from '../hooks/useAsyncOperation';
import { ModalOverlay } from './ModalOverlay';
import { render as renderTemplate, TemplateSyntaxError } from '../utils/templateEngine';
import * as api from '../services/api';
import type { LookupTableSummary } from '../services/api';
import { logger } from '../utils/logger';
import { TemplateHelp } from './TemplateHelp';
import './ModalBase.css';
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

// Labels for the rendered fields, in display order. Keys match the backend
// `rendered` dict; trace keys on the backend use the `_template` suffix.
const RENDERED_FIELDS: ReadonlyArray<{ renderedKey: keyof DummyEPGPreviewResult['rendered']; traceKey: string; label: string }> = [
  { renderedKey: 'title', traceKey: 'title_template', label: 'Title' },
  { renderedKey: 'description', traceKey: 'description_template', label: 'Description' },
  { renderedKey: 'upcoming_title', traceKey: 'upcoming_title_template', label: 'Upcoming Title' },
  { renderedKey: 'upcoming_description', traceKey: 'upcoming_description_template', label: 'Upcoming Description' },
  { renderedKey: 'ended_title', traceKey: 'ended_title_template', label: 'Ended Title' },
  { renderedKey: 'ended_description', traceKey: 'ended_description_template', label: 'Ended Description' },
  { renderedKey: 'fallback_title', traceKey: 'fallback_title_template', label: 'Fallback Title' },
  { renderedKey: 'fallback_description', traceKey: 'fallback_description_template', label: 'Fallback Description' },
  { renderedKey: 'channel_logo_url', traceKey: 'channel_logo_url_template', label: 'Channel Logo URL' },
  { renderedKey: 'program_poster_url', traceKey: 'program_poster_url_template', label: 'Program Poster URL' },
];

interface ServerPreviewViewProps {
  preview: DummyEPGPreviewResult;
  expandedTraces: Set<string>;
  onToggleTrace: (field: string) => void;
}

function TraceStepView({ step }: { step: DummyEPGPreviewTraceStep }) {
  if (step.kind === 'literal') {
    // Skip whitespace-only literals — they don't add understanding.
    if (!step.text.trim()) return null;
    return (
      <div className="trace-step trace-step-literal">
        <span className="trace-step-label">literal</span>
        <code>{step.text}</code>
      </div>
    );
  }
  if (step.kind === 'placeholder') {
    return (
      <div className="trace-step trace-step-placeholder">
        <div className="trace-step-header">
          <code className="trace-step-source">{step.raw}</code>
          <span className="trace-step-arrow">→</span>
          <code className="trace-step-final">{step.final_value || <em>(empty)</em>}</code>
        </div>
        {step.pipes.length > 0 && (
          <ol className="trace-pipe-list">
            <li className="trace-pipe-item trace-pipe-initial">
              <span className="trace-pipe-label">initial</span>
              <code>{step.initial_value || <em>(empty)</em>}</code>
            </li>
            {step.pipes.map((pipe, idx) => (
              <li key={idx} className="trace-pipe-item">
                <span className="trace-pipe-label">
                  {pipe.transform}
                  {pipe.arg !== null && pipe.arg !== undefined ? `:${pipe.arg}` : ''}
                </span>
                <code>{pipe.output || <em>(empty)</em>}</code>
                {pipe.transform === 'lookup' && (
                  <span className={`trace-pipe-hit ${pipe.matched ? 'is-match' : 'is-miss'}`}>
                    {pipe.matched ? 'hit' : 'miss'}
                  </span>
                )}
              </li>
            ))}
          </ol>
        )}
      </div>
    );
  }
  // Conditional
  return (
    <div className={`trace-step trace-step-conditional ${step.taken ? 'is-taken' : 'is-skipped'}`}>
      <div className="trace-step-header">
        <code className="trace-step-source">{`{if:${step.condition}}`}</code>
        <span className={`trace-cond-verdict ${step.taken ? 'is-taken' : 'is-skipped'}`}>
          {step.taken ? 'taken' : 'skipped'}
        </span>
        <span className="trace-cond-kind">{step.kind_detail}</span>
        {step.value && <code className="trace-cond-value">= {step.value}</code>}
      </div>
      {step.taken && step.body.length > 0 && (
        <div className="trace-cond-body">
          {step.body.map((child, idx) => <TraceStepView key={idx} step={child} />)}
        </div>
      )}
    </div>
  );
}

function ServerPreviewView({ preview, expandedTraces, onToggleTrace }: ServerPreviewViewProps) {
  const rendered = preview.rendered;
  const fieldsWithOutput = RENDERED_FIELDS.filter((f) => rendered[f.renderedKey]);
  return (
    <div className="server-preview">
      <div className="server-preview-header">
        <h4>Server Preview</h4>
        <span className={`server-preview-match-badge ${preview.matched ? 'is-matched' : 'is-fallback'}`}>
          {preview.matched ? 'matched' : 'fallback'}
        </span>
        {preview.matched_variant && (
          <span className="server-preview-variant">variant: <code>{preview.matched_variant}</code></span>
        )}
      </div>

      {preview.groups && Object.keys(preview.groups).length > 0 && (
        <div className="server-preview-groups">
          <strong>Extracted Groups</strong>
          <div className="server-preview-groups-grid">
            {Object.entries(preview.groups).map(([k, v]) => (
              <div key={k} className="server-preview-group-row">
                <code className="server-preview-group-key">{k}</code>
                <code className="server-preview-group-value">{String(v)}</code>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="server-preview-fields">
        {fieldsWithOutput.length === 0 ? (
          <p className="server-preview-empty">No templates produced output.</p>
        ) : (
          fieldsWithOutput.map(({ renderedKey, traceKey, label }) => {
            const trace = preview.traces?.[traceKey];
            const hasTrace = Array.isArray(trace) && trace.length > 0;
            const expanded = expandedTraces.has(traceKey);
            return (
              <div key={traceKey} className="server-preview-field">
                <div className="server-preview-field-header">
                  <span className="server-preview-field-label">{label}</span>
                  <code className="server-preview-field-value">{rendered[renderedKey]}</code>
                  {hasTrace && (
                    <button
                      type="button"
                      className="server-preview-trace-toggle"
                      onClick={() => onToggleTrace(traceKey)}
                    >
                      <span className="material-icons">{expanded ? 'expand_less' : 'expand_more'}</span>
                      {expanded ? 'Hide trace' : 'Show trace'}
                    </button>
                  )}
                </div>
                {hasTrace && expanded && (
                  <div className="server-preview-trace">
                    {trace.map((step, idx) => <TraceStepView key={idx} step={step} />)}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
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

export const DummyEPGSourceModal = memo(function DummyEPGSourceModal({ isOpen, source, onClose, onSave }: DummyEPGSourceModalProps) {
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
  const [serverPreview, setServerPreview] = useState<DummyEPGPreviewResult | null>(null);
  const [serverPreviewLoading, setServerPreviewLoading] = useState(false);
  const [serverPreviewError, setServerPreviewError] = useState<string | null>(null);
  const [templateHelpOpen, setTemplateHelpOpen] = useState(false);
  const [expandedTraces, setExpandedTraces] = useState<Set<string>>(() => new Set());

  // UI State
  const { loading: saving, error, execute, setError, clearError } = useAsyncOperation();
  const [titlePatternError, setTitlePatternError] = useState<string | null>(null);
  const [timePatternError, setTimePatternError] = useState<string | null>(null);
  const [datePatternError, setDatePatternError] = useState<string | null>(null);

  // Collapsible sections state
  const [upcomingEndedOpen, setUpcomingEndedOpen] = useState(false);
  const [fallbackOpen, setFallbackOpen] = useState(false);
  const [logoUrlsOpen, setLogoUrlsOpen] = useState(false);
  const [epgTagsOpen, setEpgTagsOpen] = useState(false);
  const [lookupsOpen, setLookupsOpen] = useState(false);

  // Lookup tables attached to this source (inline + global IDs)
  const [inlineLookups, setInlineLookups] = useState<Record<string, Record<string, string>>>({});
  const [globalLookupIds, setGlobalLookupIds] = useState<number[]>([]);
  const [globalLookupsList, setGlobalLookupsList] = useState<LookupTableSummary[]>([]);

  // Dropdown state
  const [nameSourceDropdownOpen, setNameSourceDropdownOpen] = useState(false);
  const [eventTimezoneDropdownOpen, setEventTimezoneDropdownOpen] = useState(false);
  const [outputTimezoneDropdownOpen, setOutputTimezoneDropdownOpen] = useState(false);
  const [eventTimezoneSearch, setEventTimezoneSearch] = useState('');
  const [outputTimezoneSearch, setOutputTimezoneSearch] = useState('');
  const nameSourceDropdownRef = useRef<HTMLDivElement>(null);
  const eventTimezoneDropdownRef = useRef<HTMLDivElement>(null);
  const outputTimezoneDropdownRef = useRef<HTMLDivElement>(null);

  // Close dropdowns when clicking outside
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
        setInlineLookups(props.inline_lookups || {});
        setGlobalLookupIds(props.global_lookup_ids || []);
        setLookupsOpen(Boolean(
          (props.inline_lookups && Object.keys(props.inline_lookups).length) ||
          (props.global_lookup_ids && props.global_lookup_ids.length)
        ));
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
        setInlineLookups({});
        setGlobalLookupIds([]);
        setLookupsOpen(false);
      }
      setSampleChannelName('');
      clearError();
      setTitlePatternError(null);
      setTimePatternError(null);
      setDatePatternError(null);
      setServerPreview(null);
      setServerPreviewError(null);
      setExpandedTraces(new Set());

      // Fetch the global lookup tables list whenever the modal opens so the
      // multi-select stays in sync if tables were added since the last open.
      api.listLookupTables()
        .then(setGlobalLookupsList)
        .catch((err) => logger.warn('Failed to load lookup tables for picker', err));
    }
  }, [isOpen, source, clearError]);

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

  // Merge inline tables with the globals selected for this source. Inline
  // entries override same-named globals so preview reflects the same merge
  // order the backend performs in _resolve_lookups.
  const effectiveLookups = useMemo(() => {
    const merged: Record<string, Record<string, string>> = {};
    const globalsById = new Map(globalLookupsList.map((t) => [t.id, t]));
    for (const id of globalLookupIds) {
      const g = globalsById.get(id);
      if (!g) continue;
      // Summary list doesn't include entries — preview won't resolve global
      // keys until the user clicks Save+reopen with a full fetch. Flag it.
      merged[g.name] = merged[g.name] || {};
    }
    for (const [name, entries] of Object.entries(inlineLookups)) {
      merged[name] = entries;
    }
    return merged;
  }, [globalLookupIds, globalLookupsList, inlineLookups]);

  // Apply template via the shared template engine. Falls back to the raw
  // template on syntax errors so the preview UI never blows up on a
  // half-typed expression.
  const applyTemplate = useCallback((template: string, groups: Record<string, string>): string => {
    if (!template) return '';
    try {
      return renderTemplate(template, groups, effectiveLookups);
    } catch (err) {
      if (err instanceof TemplateSyntaxError) return template;
      throw err;
    }
  }, [effectiveLookups]);

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
    clearError();

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

    await execute(async () => {
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
      // Drop empty/invalid inline tables so the payload stays compact.
      const cleanedInlineLookups: Record<string, Record<string, string>> = {};
      for (const [tableName, entries] of Object.entries(inlineLookups)) {
        const trimmedName = tableName.trim();
        if (!trimmedName || !entries) continue;
        const cleanedEntries: Record<string, string> = {};
        for (const [k, v] of Object.entries(entries)) {
          if (k.trim()) cleanedEntries[k.trim()] = v;
        }
        if (Object.keys(cleanedEntries).length > 0) {
          cleanedInlineLookups[trimmedName] = cleanedEntries;
        }
      }
      if (Object.keys(cleanedInlineLookups).length > 0) {
        customProperties.inline_lookups = cleanedInlineLookups;
      }
      if (globalLookupIds.length > 0) customProperties.global_lookup_ids = globalLookupIds;

      await onSave({
        name: name.trim(),
        source_type: 'dummy',
        is_active: isActive,
        custom_properties: customProperties,
      });
      onClose();
    });
  };

  const handleServerPreview = async () => {
    if (!sampleChannelName.trim()) {
      setServerPreviewError('Enter a sample channel name first.');
      return;
    }
    setServerPreviewLoading(true);
    setServerPreviewError(null);
    try {
      // Collect only the fields the preview endpoint expects. No need to
      // strip empties — the backend falls back gracefully.
      const result = await api.previewDummyEPG({
        sample_name: sampleChannelName,
        title_pattern: titlePattern || undefined,
        time_pattern: timePattern || undefined,
        date_pattern: datePattern || undefined,
        title_template: titleTemplate || undefined,
        description_template: descriptionTemplate || undefined,
        upcoming_title_template: upcomingTitleTemplate || undefined,
        upcoming_description_template: upcomingDescriptionTemplate || undefined,
        ended_title_template: endedTitleTemplate || undefined,
        ended_description_template: endedDescriptionTemplate || undefined,
        fallback_title_template: fallbackTitleTemplate || undefined,
        fallback_description_template: fallbackDescriptionTemplate || undefined,
        event_timezone: eventTimezone || undefined,
        output_timezone: outputTimezone || undefined,
        program_duration: programDuration,
        channel_logo_url_template: channelLogoUrl || undefined,
        program_poster_url_template: programPosterUrl || undefined,
        inline_lookups: Object.keys(inlineLookups).length ? inlineLookups : undefined,
        global_lookup_ids: globalLookupIds.length ? globalLookupIds : undefined,
        include_trace: true,
      });
      setServerPreview(result);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Server preview failed';
      setServerPreviewError(message);
      logger.error('Server preview failed', err);
    } finally {
      setServerPreviewLoading(false);
    }
  };

  const toggleTrace = useCallback((field: string) => {
    setExpandedTraces((prev) => {
      const next = new Set(prev);
      if (next.has(field)) next.delete(field);
      else next.add(field);
      return next;
    });
  }, []);

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
    <ModalOverlay onClose={onClose}>
      <div className="modal-container modal-lg dummy-epg-modal">
        <div className="modal-header">
          <h2>{source ? 'Edit Dummy EPG Source' : 'Add Dummy EPG Source'}</h2>
          <button className="modal-close-btn" onClick={onClose}>
            <span className="material-icons">close</span>
          </button>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            {/* Basic Info */}
            <div className="modal-form-group">
              <label htmlFor="name">Name <span className="modal-required">*</span></label>
              <input
                id="name"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="NCAA Football EPG"
                autoFocus
              />
            </div>

            <div className="modal-form-group">
              <label className="modal-checkbox-label">
                <input
                  type="checkbox"
                  checked={isActive}
                  onChange={(e) => setIsActive(e.target.checked)}
                />
                <span>Active</span>
              </label>
            </div>

            {/* Pattern Configuration Section */}
            <div className="modal-section-divider">
              <span>Pattern Configuration</span>
            </div>

            <p className="modal-section-description">
              Define regex patterns to extract information from channel titles or stream names. Use named capture groups like (?&lt;groupname&gt;pattern).
            </p>

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

            <div className="modal-form-group">
              <label htmlFor="titlePattern">Title Pattern <span className="modal-required">*</span></label>
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

            <div className="modal-form-group">
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

            <div className="modal-form-group">
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
            <div className="modal-section-divider">
              <span>Output Templates (Optional)</span>
            </div>

            <p className="modal-section-description">
              Use extracted groups from your patterns to format EPG titles and descriptions. Reference groups using &#123;groupname&#125; syntax. For cleaner URLs, use &#123;groupname_normalize&#125; to get alphanumeric-only lowercase versions.
            </p>

            <div className="modal-form-group">
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

            <div className="modal-form-group">
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
              <p className="modal-section-description">
                Customize how programs appear before and after the event. If left empty, will use the main title/description with "Upcoming:" or "Ended:" prefix.
              </p>

              <div className="modal-form-group">
                <label htmlFor="upcomingTitleTemplate">Upcoming Title Template</label>
                <input
                  id="upcomingTitleTemplate"
                  type="text"
                  value={upcomingTitleTemplate}
                  onChange={(e) => setUpcomingTitleTemplate(e.target.value)}
                  placeholder="{team1} vs {team2} starting at {starttime}."
                />
              </div>

              <div className="modal-form-group">
                <label htmlFor="upcomingDescriptionTemplate">Upcoming Description Template</label>
                <textarea
                  id="upcomingDescriptionTemplate"
                  value={upcomingDescriptionTemplate}
                  onChange={(e) => setUpcomingDescriptionTemplate(e.target.value)}
                  placeholder="Upcoming: Watch the {league} match up where the {team1} take on the {team2} from {starttime} to {endtime}!"
                  rows={2}
                />
              </div>

              <div className="modal-form-group">
                <label htmlFor="endedTitleTemplate">Ended Title Template</label>
                <input
                  id="endedTitleTemplate"
                  type="text"
                  value={endedTitleTemplate}
                  onChange={(e) => setEndedTitleTemplate(e.target.value)}
                  placeholder="{team1} vs {team2} started at {starttime}."
                />
              </div>

              <div className="modal-form-group">
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
              <p className="modal-section-description">
                When patterns don't match the channel/stream name, use these custom fallback templates instead of the default placeholder messages. Leave empty to use the built-in humorous fallback descriptions.
              </p>

              <div className="modal-form-group">
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

              <div className="modal-form-group">
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
                <p className="form-hint">The timezone of event times in your channel titles. DST is handled automatically!</p>
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
                <p className="form-hint">Display times in a different timezone than the event timezone</p>
              </div>
            </div>

            <div className="modal-form-row">
              <div className="modal-form-group">
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

              <div className="modal-form-group">
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
              <div className="modal-form-group">
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

              <div className="modal-form-group">
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
              <div className="modal-form-group">
                <label className="modal-checkbox-label">
                  <input
                    type="checkbox"
                    checked={includeDateTag}
                    onChange={(e) => setIncludeDateTag(e.target.checked)}
                  />
                  <span>Include Date Tag</span>
                </label>
                <p className="form-hint">Include the &lt;date&gt; tag in EPG output with the program's start date (YYYY-MM-DD format). Added to all programs.</p>
              </div>

              <div className="modal-form-group">
                <label className="modal-checkbox-label">
                  <input
                    type="checkbox"
                    checked={includeLiveTag}
                    onChange={(e) => setIncludeLiveTag(e.target.checked)}
                  />
                  <span>Include Live Tag</span>
                </label>
                <p className="form-hint">Mark programs as live content with the &lt;live /&gt; tag in EPG output. Note: Only added to the main event, not upcoming/ended filler programs.</p>
              </div>

              <div className="modal-form-group">
                <label className="modal-checkbox-label">
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

            <CollapsibleSection
              title="Lookup Tables"
              isOpen={lookupsOpen}
              onToggle={() => setLookupsOpen(!lookupsOpen)}
            >
              <p className="form-hint">
                Reference tables in templates via <code>{'{key|lookup:<name>}'}</code>.
                Unmatched keys pass through unchanged. Inline tables override globals of the same name.
              </p>

              {/* Global tables (from Settings > Lookup Tables) */}
              <div className="modal-form-group">
                <label>Global Tables</label>
                {globalLookupsList.length === 0 ? (
                  <p className="form-hint">
                    No global tables yet. Create them in Settings → Lookup Tables.
                  </p>
                ) : (
                  <div className="dummy-epg-lookup-global-list">
                    {globalLookupsList.map((table) => {
                      const checked = globalLookupIds.includes(table.id);
                      return (
                        <label key={table.id} className="dummy-epg-lookup-global-item">
                          <input
                            type="checkbox"
                            checked={checked}
                            onChange={(e) => {
                              setGlobalLookupIds((prev) =>
                                e.target.checked
                                  ? [...prev, table.id]
                                  : prev.filter((id) => id !== table.id)
                              );
                            }}
                          />
                          <span className="dummy-epg-lookup-global-name">{table.name}</span>
                          <span className="dummy-epg-lookup-global-count">{table.entry_count} entries</span>
                        </label>
                      );
                    })}
                  </div>
                )}
              </div>

              {/* Inline tables */}
              <div className="modal-form-group">
                <div className="dummy-epg-lookup-inline-header">
                  <label>Inline Tables (this source only)</label>
                  <button
                    type="button"
                    className="btn-secondary btn-small"
                    onClick={() => {
                      // Find the next available default name.
                      let n = 1;
                      while (`table${n}` in inlineLookups) n += 1;
                      setInlineLookups((prev) => ({ ...prev, [`table${n}`]: {} }));
                    }}
                  >
                    <span className="material-icons">add</span> Add Table
                  </button>
                </div>
                {Object.keys(inlineLookups).length === 0 ? (
                  <p className="form-hint">No inline tables. Add one to define entries used only by this source.</p>
                ) : (
                  <div className="dummy-epg-lookup-inline-list">
                    {Object.entries(inlineLookups).map(([tableName, entries]) => (
                      <div key={tableName} className="dummy-epg-lookup-inline-table">
                        <div className="dummy-epg-lookup-inline-name-row">
                          <input
                            type="text"
                            value={tableName}
                            onChange={(e) => {
                              const newName = e.target.value;
                              setInlineLookups((prev) => {
                                if (newName === tableName) return prev;
                                const next: Record<string, Record<string, string>> = {};
                                for (const [k, v] of Object.entries(prev)) {
                                  next[k === tableName ? newName : k] = v;
                                }
                                return next;
                              });
                            }}
                            placeholder="table name"
                            aria-label="Inline table name"
                          />
                          <button
                            type="button"
                            className="btn-icon btn-icon-danger"
                            onClick={() => {
                              setInlineLookups((prev) => {
                                const next = { ...prev };
                                delete next[tableName];
                                return next;
                              });
                            }}
                            aria-label={`Remove table ${tableName}`}
                            title="Remove table"
                          >
                            <span className="material-icons">close</span>
                          </button>
                        </div>
                        <div className="dummy-epg-lookup-inline-entries">
                          {Object.entries(entries).map(([key, value], idx) => (
                            <div className="dummy-epg-lookup-inline-entry" key={`${key}-${idx}`}>
                              <input
                                type="text"
                                value={key}
                                onChange={(e) => {
                                  const newKey = e.target.value;
                                  setInlineLookups((prev) => {
                                    const nextEntries: Record<string, string> = {};
                                    for (const [k, v] of Object.entries(prev[tableName] || {})) {
                                      nextEntries[k === key ? newKey : k] = v;
                                    }
                                    return { ...prev, [tableName]: nextEntries };
                                  });
                                }}
                                placeholder="key"
                                aria-label="key"
                              />
                              <span className="dummy-epg-lookup-arrow">→</span>
                              <input
                                type="text"
                                value={value}
                                onChange={(e) => {
                                  const newValue = e.target.value;
                                  setInlineLookups((prev) => ({
                                    ...prev,
                                    [tableName]: { ...(prev[tableName] || {}), [key]: newValue },
                                  }));
                                }}
                                placeholder="value"
                                aria-label="value"
                              />
                              <button
                                type="button"
                                className="btn-icon btn-icon-danger"
                                onClick={() => {
                                  setInlineLookups((prev) => {
                                    const next = { ...(prev[tableName] || {}) };
                                    delete next[key];
                                    return { ...prev, [tableName]: next };
                                  });
                                }}
                                aria-label={`Remove ${key}`}
                                title="Remove entry"
                              >
                                <span className="material-icons">close</span>
                              </button>
                            </div>
                          ))}
                          <button
                            type="button"
                            className="btn-secondary btn-small dummy-epg-lookup-add-entry"
                            onClick={() => {
                              setInlineLookups((prev) => {
                                const existing = prev[tableName] || {};
                                // Find a unique empty-key slot so repeat clicks don't collide.
                                let newKey = '';
                                let suffix = 0;
                                while (newKey in existing) {
                                  suffix += 1;
                                  newKey = ' '.repeat(suffix);
                                }
                                return { ...prev, [tableName]: { ...existing, [newKey]: '' } };
                              });
                            }}
                          >
                            <span className="material-icons">add</span> Add Row
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </CollapsibleSection>

            {/* Test Your Configuration Section */}
            <div className="modal-section-divider">
              <span>Test Your Configuration</span>
            </div>

            <p className="modal-section-description">
              Preview instantly in the browser or run through the full server pipeline to see pipe-by-pipe traces and resolve global lookup values.
            </p>

            <TemplateHelp isOpen={templateHelpOpen} onToggle={() => setTemplateHelpOpen((v) => !v)} />

            <div className="modal-form-group">
              <label htmlFor="sampleChannelName">Sample Channel Name</label>
              <input
                id="sampleChannelName"
                type="text"
                value={sampleChannelName}
                onChange={(e) => setSampleChannelName(e.target.value)}
                placeholder="League 01: Team 1 VS Team 2 @ Oct 17 8:00PM ET"
              />
              <p className="form-hint">Enter a sample channel name to test pattern matching and see the formatted output</p>
              <div className="dummy-epg-preview-actions">
                <button
                  type="button"
                  className="btn-secondary btn-small"
                  onClick={handleServerPreview}
                  disabled={serverPreviewLoading || !sampleChannelName.trim()}
                  title="Runs the full backend pipeline, resolves global lookups, and returns per-field traces."
                >
                  <span className="material-icons">play_circle</span>
                  {serverPreviewLoading ? 'Running...' : 'Run Server Preview'}
                </button>
                {serverPreviewError && <span className="dummy-epg-preview-error">{serverPreviewError}</span>}
              </div>
            </div>

            {sampleChannelName && (
              <div className="modal-preview-section">
                <h4>Live Preview (browser):</h4>
                {preview.groups ? (
                  <>
                    <div className="modal-preview-groups">
                      <strong>Extracted Groups:</strong>
                      <code>{JSON.stringify(preview.groups, null, 2)}</code>
                    </div>
                    {preview.title && (
                      <div className="modal-preview-item">
                        <strong>Title:</strong> {preview.title}
                      </div>
                    )}
                    {preview.description && (
                      <div className="modal-preview-item">
                        <strong>Description:</strong> {preview.description}
                      </div>
                    )}
                  </>
                ) : (
                  <div className="modal-preview-no-match">
                    <span className="material-icons">warning</span>
                    <span>Pattern did not match. {fallbackTitleTemplate ? `Using fallback: "${fallbackTitleTemplate}"` : 'Using channel name as title.'}</span>
                  </div>
                )}
              </div>
            )}

            {serverPreview && (
              <ServerPreviewView
                preview={serverPreview}
                expandedTraces={expandedTraces}
                onToggleTrace={toggleTrace}
              />
            )}

            {error && <div className="modal-error-banner">{error}</div>}
          </div>

          <div className="modal-footer">
            <button type="button" className="modal-btn modal-btn-secondary" onClick={handleClearAll}>
              Clear All
            </button>
            <button type="submit" className="modal-btn modal-btn-primary" disabled={saving}>
              {saving ? 'Saving...' : source ? 'Save Changes' : 'Add Dummy EPG'}
            </button>
          </div>
        </form>
      </div>
    </ModalOverlay>
  );
});
