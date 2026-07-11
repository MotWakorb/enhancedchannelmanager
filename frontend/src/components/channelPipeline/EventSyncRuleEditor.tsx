/**
 * Event Sync rule editor (bead ti939.1.5 — Phase 1A, preview only).
 *
 * Quick path: pick a master group, pick secondary groups, keep the shipped
 * default patterns, preview. Advanced knobs (time window, attach threshold,
 * per-group pattern overrides) stay collapsed. Auto-sync status is shown
 * LIVE per group with guidance text when it's wrong — this phase never
 * toggles Dispatcharr settings (guidance only; a guided one-click toggle is
 * a Phase 2 bead).
 *
 * There is NO apply/attach control anywhere: saving stores the config on the
 * rule; the only action against live data is the zero-write preview.
 */
import { useEffect, useId, useMemo, useState } from 'react';
import type { ChannelPipelineRule, CreateRuleData } from '../../types/channelPipeline';
import type { EventSyncConfig, EventSyncPattern, EventSyncPreviewResponse } from '../../types/eventSync';
import type { M3UGroupSetting } from '../../types';
import { getChannelGroups, getProviderGroupSettings } from '../../services/api';
import { previewEventSync } from '../../services/channelPipelineApi';
import { CustomSelect } from '../CustomSelect';
import { EventSyncTestPatternsPanel } from './EventSyncTestPatternsPanel';
import type { LabeledEventSyncPattern } from './EventSyncTestPatternsPanel';
import { EventSyncPreviewPanel } from './EventSyncPreviewPanel';
import {
  SHIPPED_EVENT_SYNC_PATTERNS,
  DEFAULT_PATTERN_IDS,
  DEFAULT_TIME_WINDOW_MINUTES,
  MAX_TIME_WINDOW_MINUTES,
  EVENT_ATTACH_FLOOR,
  clampAttachThreshold,
  selectionIsBuiltinDefaults,
} from './eventSyncDefaults';
import './EventSyncRuleEditor.css';

export interface EventSyncRuleEditorProps {
  /** Existing event_sync rule to edit; omit to create a new one. */
  rule?: Partial<ChannelPipelineRule>;
  onSave: (data: CreateRuleData) => Promise<void> | void;
  onCancel: () => void;
  isLoading?: boolean;
}

interface GroupPatternDraft {
  title_pattern: string;
  time_pattern: string;
  date_pattern: string;
}

const EMPTY_GROUP_PATTERN: GroupPatternDraft = {
  title_pattern: '',
  time_pattern: '',
  date_pattern: '',
};

/** Initial shipped-pattern selection for an existing rule's config. */
function initialPatternIds(config: EventSyncConfig | null | undefined): string[] {
  if (!config || !config.patterns) return DEFAULT_PATTERN_IDS;
  const shippedIds = new Set(SHIPPED_EVENT_SYNC_PATTERNS.map(p => p.id));
  const ids = config.patterns
    .map(p => p.name)
    .filter((name): name is string => Boolean(name && shippedIds.has(name)));
  return ids.length > 0 ? ids : DEFAULT_PATTERN_IDS;
}

/** Custom shared patterns = saved patterns that aren't shipped ones. */
function initialCustomShared(config: EventSyncConfig | null | undefined): GroupPatternDraft {
  const shippedIds = new Set(SHIPPED_EVENT_SYNC_PATTERNS.map(p => p.id));
  const custom = config?.patterns?.find(p => !p.name || !shippedIds.has(p.name));
  return custom
    ? {
        title_pattern: custom.title_pattern || '',
        time_pattern: custom.time_pattern || '',
        date_pattern: custom.date_pattern || '',
      }
    : EMPTY_GROUP_PATTERN;
}

function initialGroupOverrides(
  config: EventSyncConfig | null | undefined
): Record<number, GroupPatternDraft> {
  const overrides: Record<number, GroupPatternDraft> = {};
  for (const [key, patterns] of Object.entries(config?.group_patterns ?? {})) {
    const groupId = parseInt(key, 10);
    const first = patterns[0];
    if (!Number.isNaN(groupId) && first) {
      overrides[groupId] = {
        title_pattern: first.title_pattern || '',
        time_pattern: first.time_pattern || '',
        date_pattern: first.date_pattern || '',
      };
    }
  }
  return overrides;
}

export function EventSyncRuleEditor({
  rule,
  onSave,
  onCancel,
  isLoading = false,
}: EventSyncRuleEditorProps) {
  const id = useId();
  const config = rule?.event_sync_config ?? null;

  // Basic info
  const [name, setName] = useState(rule?.name || '');
  const [description, setDescription] = useState(rule?.description || '');
  const [enabled, setEnabled] = useState(rule?.enabled ?? true);

  // Scoping
  const [masterGroupId, setMasterGroupId] = useState<number | null>(
    config?.master_group_id ?? null
  );
  const [secondaryGroupIds, setSecondaryGroupIds] = useState<number[]>(
    config?.secondary_group_ids ?? []
  );
  const [secondarySearch, setSecondarySearch] = useState('');

  // Patterns
  const [selectedPatternIds, setSelectedPatternIds] = useState<string[]>(
    initialPatternIds(config)
  );
  const [customShared, setCustomShared] = useState<GroupPatternDraft>(
    initialCustomShared(config)
  );
  const [groupOverrides, setGroupOverrides] = useState<Record<number, GroupPatternDraft>>(
    initialGroupOverrides(config)
  );

  // Advanced knobs. Threshold is kept as text while typing and clamped to the
  // schema-legal [0.80, 1.0] range on blur/save — the backend hard-rejects
  // anything below the floor.
  const [timeWindowText, setTimeWindowText] = useState(
    String(config?.time_window_minutes ?? DEFAULT_TIME_WINDOW_MINUTES)
  );
  const [thresholdText, setThresholdText] = useState(
    (config?.attach_threshold ?? EVENT_ATTACH_FLOOR).toFixed(2)
  );

  // Reference data
  const [channelGroups, setChannelGroups] = useState<{ id: number; name: string }[]>([]);
  const [groupSettings, setGroupSettings] = useState<Record<number, M3UGroupSetting>>({});
  const [settingsLoaded, setSettingsLoaded] = useState(false);

  // Preview
  const [preview, setPreview] = useState<EventSyncPreviewResponse | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

  const [saveError, setSaveError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    getChannelGroups()
      .then(groups => setChannelGroups(groups.map(g => ({ id: g.id, name: g.name }))))
      .catch(() => {});
    getProviderGroupSettings()
      .then(settings => {
        setGroupSettings(settings);
        setSettingsLoaded(true);
      })
      .catch(() => {});
  }, []);

  const groupName = useMemo(() => {
    const byId = new Map(channelGroups.map(g => [g.id, g.name]));
    return (groupId: number) => byId.get(groupId) ?? `Group ${groupId}`;
  }, [channelGroups]);

  /** Live auto-sync status: true/false when known, null when not provider-backed. */
  const autoSyncStatus = (groupId: number): boolean | null => {
    const setting = groupSettings[groupId];
    return setting ? Boolean(setting.auto_channel_sync) : null;
  };

  const masterStatus = masterGroupId != null ? autoSyncStatus(masterGroupId) : null;

  const secondariesWithAutoSyncOn = secondaryGroupIds.filter(
    groupId => autoSyncStatus(groupId) === true
  );

  const filteredSecondaryOptions = useMemo(() => {
    const query = secondarySearch.trim().toLowerCase();
    return channelGroups
      .filter(g => g.id !== masterGroupId)
      .filter(g => !query || g.name.toLowerCase().includes(query))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [channelGroups, masterGroupId, secondarySearch]);

  const effectivePatterns: LabeledEventSyncPattern[] = useMemo(() => {
    const shipped = SHIPPED_EVENT_SYNC_PATTERNS
      .filter(p => selectedPatternIds.includes(p.id))
      .map(p => ({ label: p.label, pattern: p.pattern }));
    if (customShared.title_pattern.trim()) {
      shipped.push({
        label: 'Custom shared pattern',
        pattern: {
          name: 'custom-shared',
          title_pattern: customShared.title_pattern.trim(),
          ...(customShared.time_pattern.trim()
            ? { time_pattern: customShared.time_pattern.trim() }
            : {}),
          ...(customShared.date_pattern.trim()
            ? { date_pattern: customShared.date_pattern.trim() }
            : {}),
        },
      });
    }
    return shipped;
  }, [selectedPatternIds, customShared]);

  /** Groups in scope (master + secondaries) — for live samples + overrides. */
  const scopedGroups = useMemo(() => {
    const ids = masterGroupId != null ? [masterGroupId, ...secondaryGroupIds] : [...secondaryGroupIds];
    return ids.map(groupId => ({ id: groupId, name: groupName(groupId) }));
  }, [masterGroupId, secondaryGroupIds, groupName]);

  const validationError: string | null = (() => {
    if (masterGroupId == null) return 'Pick a master group first';
    if (secondaryGroupIds.length === 0) return 'Pick at least one secondary group';
    if (effectivePatterns.length === 0) {
      return 'Select at least one parse pattern (or add a custom one)';
    }
    return null;
  })();

  /** Build the event_sync_config from the current form state. */
  const buildConfig = (): EventSyncConfig | null => {
    if (masterGroupId == null || secondaryGroupIds.length === 0) return null;

    const built: EventSyncConfig = {
      master_group_id: masterGroupId,
      secondary_group_ids: [...secondaryGroupIds],
      time_window_minutes: Math.min(
        MAX_TIME_WINDOW_MINUTES,
        Math.max(1, parseInt(timeWindowText, 10) || DEFAULT_TIME_WINDOW_MINUTES)
      ),
      attach_threshold: clampAttachThreshold(parseFloat(thresholdText)),
      enabled: config?.enabled ?? true,
    };
    // ti939.2.1: the per-run attach cap has no editor control yet — preserve
    // an existing (API-set) value so a UI edit does not silently reset it to
    // the backend default.
    if (config?.max_attach_per_run != null) {
      built.max_attach_per_run = config.max_attach_per_run;
    }

    // Selection == exactly the built-ins and no custom pattern → omit the
    // `patterns` key so the backend's own defaults apply (future matcher
    // improvements flow through without editing saved rules).
    const hasCustomShared = Boolean(customShared.title_pattern.trim());
    if (!selectionIsBuiltinDefaults(selectedPatternIds) || hasCustomShared) {
      built.patterns = effectivePatterns.map(p => p.pattern);
    }

    const overrideEntries = Object.entries(groupOverrides).filter(
      ([groupId, draft]) =>
        draft.title_pattern.trim() &&
        (parseInt(groupId, 10) === masterGroupId ||
          secondaryGroupIds.includes(parseInt(groupId, 10)))
    );
    if (overrideEntries.length > 0) {
      built.group_patterns = Object.fromEntries(
        overrideEntries.map(([groupId, draft]) => [
          groupId,
          [
            {
              name: `custom-group-${groupId}`,
              title_pattern: draft.title_pattern.trim(),
              ...(draft.time_pattern.trim() ? { time_pattern: draft.time_pattern.trim() } : {}),
              ...(draft.date_pattern.trim() ? { date_pattern: draft.date_pattern.trim() } : {}),
            } satisfies EventSyncPattern,
          ],
        ])
      );
    }

    return built;
  };

  const handleRunPreview = async () => {
    const builtConfig = buildConfig();
    if (!builtConfig) return;
    setPreviewLoading(true);
    setPreviewError(null);
    try {
      const response = await previewEventSync({ event_sync_config: builtConfig });
      setPreview(response);
    } catch (err) {
      setPreviewError(err instanceof Error ? err.message : 'Preview failed');
      setPreview(null);
    } finally {
      setPreviewLoading(false);
    }
  };

  const handleSave = async () => {
    if (!name.trim()) {
      setSaveError('Name is required');
      document.getElementById(`${id}-name`)?.focus();
      return;
    }
    if (validationError) {
      setSaveError(validationError);
      return;
    }
    const builtConfig = buildConfig();
    if (!builtConfig) return;
    setSaving(true);
    setSaveError(null);
    try {
      await onSave({
        name: name.trim(),
        description: description.trim() || undefined,
        enabled,
        // Placeholder condition/action: the engine ignores both for the
        // event_sync kind, but the rule schema requires at least one of each
        // (same convention as the backend's own event_sync tests).
        conditions: rule?.conditions?.length ? rule.conditions : [{ type: 'always' }],
        actions: rule?.actions?.length ? rule.actions : [{ type: 'skip' }],
        event_sync_config: builtConfig,
      });
    } finally {
      setSaving(false);
    }
  };

  const toggleSecondary = (groupId: number) => {
    setSecondaryGroupIds(ids =>
      ids.includes(groupId) ? ids.filter(i => i !== groupId) : [...ids, groupId]
    );
  };

  const updateOverride = (groupId: number, field: keyof GroupPatternDraft, value: string) => {
    setGroupOverrides(overrides => ({
      ...overrides,
      [groupId]: { ...(overrides[groupId] ?? EMPTY_GROUP_PATTERN), [field]: value },
    }));
  };

  return (
    <div className="event-sync-editor" data-testid="event-sync-editor">
      <div className="event-sync-editor-content">
        <p className="form-hint event-sync-quick-path">
          Quick path: pick the master group, pick the secondary groups, keep
          the default patterns, then Preview. This phase is preview-only —
          nothing attaches until a later phase.
        </p>

        {/* Basic Info */}
        <section className="event-sync-section-block">
          <h3 className="event-sync-section-title">Basic Information</h3>
          <div className="form-group">
            <label htmlFor={`${id}-name`}>Rule Name *</label>
            <input
              id={`${id}-name`}
              type="text"
              value={name}
              onChange={e => setName(e.target.value)}
              placeholder="Enter rule name"
              disabled={isLoading}
              aria-required="true"
            />
          </div>
          <div className="form-group">
            <label htmlFor={`${id}-description`}>Description</label>
            <textarea
              id={`${id}-description`}
              value={description}
              onChange={e => setDescription(e.target.value)}
              placeholder="Optional description"
              rows={2}
              disabled={isLoading}
            />
          </div>
          <label className="checkbox-option">
            <input
              type="checkbox"
              checked={enabled}
              onChange={e => setEnabled(e.target.checked)}
              disabled={isLoading}
            />
            <span>Enabled</span>
          </label>
        </section>

        {/* Master group */}
        <section className="event-sync-section-block">
          <h3 className="event-sync-section-title">Master group</h3>
          <span className="form-hint">
            The ONE group whose channels Dispatcharr owns — auto-sync must be
            ON for it. Secondary streams attach to these channels.
          </span>
          <CustomSelect
            value={masterGroupId != null ? masterGroupId.toString() : ''}
            onChange={value => {
              const groupId = value ? parseInt(value, 10) : null;
              setMasterGroupId(groupId);
              if (groupId != null) {
                setSecondaryGroupIds(ids => ids.filter(i => i !== groupId));
              }
            }}
            options={channelGroups
              .slice()
              .sort((a, b) => a.name.localeCompare(b.name))
              .map(g => {
                const status = autoSyncStatus(g.id);
                const statusLabel =
                  status === true ? 'auto-sync ON' : status === false ? 'auto-sync OFF' : 'no provider settings';
                return { value: g.id.toString(), label: `${g.name} — ${statusLabel}` };
              })}
            placeholder="Select master group..."
            searchable
            searchPlaceholder="Search groups..."
            disabled={isLoading}
          />
          {masterGroupId != null && settingsLoaded && masterStatus !== true && (
            <div className="warning-message" role="alert" data-testid="master-autosync-warning">
              <span className="material-icons">warning</span>
              <span>
                {masterStatus === false ? (
                  <>
                    Auto-sync is <strong>OFF</strong> for this group, so
                    Dispatcharr creates no master channels and the preview will
                    match nothing. Enable <code>auto_channel_sync</code> for
                    this group in M3U Manager → account → Groups. ECM never
                    toggles this setting for you.
                  </>
                ) : (
                  <>
                    This group has no provider group settings — it may not be
                    provider-backed. Pick the provider event group Dispatcharr
                    auto-syncs channels from.
                  </>
                )}
              </span>
            </div>
          )}
          {masterGroupId != null && masterStatus === true && (
            <span className="event-sync-status-ok">
              <span className="material-icons" aria-hidden="true">check_circle</span>
              Auto-sync is ON — Dispatcharr owns this group&apos;s channels.
            </span>
          )}
        </section>

        {/* Secondary groups */}
        <section className="event-sync-section-block">
          <h3 className="event-sync-section-title">Secondary groups</h3>
          <span className="form-hint">
            Pure stream sources from other providers — auto-sync should be OFF
            for each (otherwise Dispatcharr keeps creating duplicate channels
            from them).
          </span>
          <input
            type="text"
            className="event-sync-secondary-search"
            placeholder="Filter groups..."
            value={secondarySearch}
            onChange={e => setSecondarySearch(e.target.value)}
            aria-label="Filter secondary groups"
          />
          <div className="event-sync-secondary-list checkbox-group" role="group" aria-label="Secondary groups">
            {filteredSecondaryOptions.length === 0 ? (
              <span className="form-hint">No groups match the filter</span>
            ) : (
              filteredSecondaryOptions.map(group => {
                const status = autoSyncStatus(group.id);
                return (
                  <label key={group.id} className="checkbox-option">
                    <input
                      type="checkbox"
                      checked={secondaryGroupIds.includes(group.id)}
                      onChange={() => toggleSecondary(group.id)}
                      disabled={isLoading}
                    />
                    <span>
                      {group.name}
                      {status === true && (
                        <span className="event-sync-inline-warn">
                          <span className="material-icons" aria-hidden="true">warning</span>
                          auto-sync ON
                        </span>
                      )}
                    </span>
                  </label>
                );
              })
            )}
          </div>
          {secondariesWithAutoSyncOn.length > 0 && (
            <div className="warning-message" role="alert" data-testid="secondary-autosync-warning">
              <span className="material-icons">warning</span>
              <span>
                Auto-sync is <strong>ON</strong> for{' '}
                {secondariesWithAutoSyncOn.map(groupName).join(', ')} —
                Dispatcharr will keep creating duplicate channels from{' '}
                {secondariesWithAutoSyncOn.length === 1 ? 'this group' : 'these groups'}.
                Disable <code>auto_channel_sync</code> for{' '}
                {secondariesWithAutoSyncOn.length === 1 ? 'it' : 'them'} in M3U
                Manager → account → Groups. ECM never toggles this setting for
                you.
              </span>
            </div>
          )}
        </section>

        {/* Parse patterns */}
        <section className="event-sync-section-block">
          <h3 className="event-sync-section-title">Parse patterns</h3>
          <span className="form-hint">
            Shipped patterns cover the common shapes — most rules never need a
            custom regex. Patterns are tried in order; the first one that
            extracts a complete title + date + time wins.
          </span>
          <div className="checkbox-group event-sync-pattern-list">
            {SHIPPED_EVENT_SYNC_PATTERNS.map(shipped => (
              <label key={shipped.id} className="checkbox-option event-sync-pattern-option">
                <input
                  type="checkbox"
                  checked={selectedPatternIds.includes(shipped.id)}
                  onChange={() =>
                    setSelectedPatternIds(ids =>
                      ids.includes(shipped.id)
                        ? ids.filter(i => i !== shipped.id)
                        : [...ids, shipped.id]
                    )
                  }
                  disabled={isLoading}
                />
                <span>
                  <span className="event-sync-pattern-label">{shipped.label}</span>
                  <span className="event-sync-pattern-desc">{shipped.description}</span>
                  <span className="event-sync-pattern-example">e.g. {shipped.example}</span>
                </span>
              </label>
            ))}
          </div>

          <details className="event-sync-details">
            <summary>Custom shared pattern (regex fallback)</summary>
            <div className="event-sync-details-body">
              <span className="form-hint">
                Python-style named groups: <code>(?P&lt;title&gt;...)</code>,{' '}
                <code>(?P&lt;hour&gt;...)</code>/<code>(?P&lt;minute&gt;...)</code>/
                <code>(?P&lt;ampm&gt;...)</code>,{' '}
                <code>(?P&lt;day&gt;...)</code>/<code>(?P&lt;month&gt;...)</code>/
                <code>(?P&lt;year&gt;...)</code>. Verify with the Test Patterns
                panel below.
              </span>
              <div className="form-group">
                <label htmlFor={`${id}-custom-title`}>Title pattern</label>
                <input
                  id={`${id}-custom-title`}
                  type="text"
                  value={customShared.title_pattern}
                  onChange={e => setCustomShared(c => ({ ...c, title_pattern: e.target.value }))}
                  placeholder="^(?P<title>.+?)\s*@"
                  disabled={isLoading}
                />
              </div>
              <div className="form-group">
                <label htmlFor={`${id}-custom-time`}>Time pattern</label>
                <input
                  id={`${id}-custom-time`}
                  type="text"
                  value={customShared.time_pattern}
                  onChange={e => setCustomShared(c => ({ ...c, time_pattern: e.target.value }))}
                  placeholder="(?P<hour>\d{1,2}):(?P<minute>\d{2})"
                  disabled={isLoading}
                />
              </div>
              <div className="form-group">
                <label htmlFor={`${id}-custom-date`}>Date pattern</label>
                <input
                  id={`${id}-custom-date`}
                  type="text"
                  value={customShared.date_pattern}
                  onChange={e => setCustomShared(c => ({ ...c, date_pattern: e.target.value }))}
                  placeholder="(?P<day>\d{1,2})\s+(?P<month>[A-Za-z]{3,9})"
                  disabled={isLoading}
                />
              </div>
            </div>
          </details>
        </section>

        {/* Test Patterns */}
        <section className="event-sync-section-block">
          <details className="event-sync-details" open>
            <summary>Test patterns against sample names</summary>
            <div className="event-sync-details-body">
              <EventSyncTestPatternsPanel
                patterns={effectivePatterns}
                groupOptions={scopedGroups}
              />
            </div>
          </details>
        </section>

        {/* Advanced */}
        <section className="event-sync-section-block">
          <details className="event-sync-details">
            <summary>Advanced</summary>
            <div className="event-sync-details-body">
              <div className="form-group">
                <label htmlFor={`${id}-time-window`}>Time window (minutes)</label>
                <input
                  id={`${id}-time-window`}
                  type="number"
                  min={1}
                  max={MAX_TIME_WINDOW_MINUTES}
                  value={timeWindowText}
                  onChange={e => setTimeWindowText(e.target.value)}
                  disabled={isLoading}
                />
                <span className="form-hint">
                  Parsed start times must be within ± this window to become
                  candidate pairs (default {DEFAULT_TIME_WINDOW_MINUTES}, max{' '}
                  {MAX_TIME_WINDOW_MINUTES}).
                </span>
              </div>
              <div className="form-group">
                <label htmlFor={`${id}-threshold`}>Attach threshold</label>
                <input
                  id={`${id}-threshold`}
                  type="number"
                  min={EVENT_ATTACH_FLOOR}
                  max={1}
                  step={0.01}
                  value={thresholdText}
                  onChange={e => setThresholdText(e.target.value)}
                  onBlur={() =>
                    setThresholdText(clampAttachThreshold(parseFloat(thresholdText)).toFixed(2))
                  }
                  disabled={isLoading}
                />
                <span className="form-hint">
                  Auto-attach score floor on the parsed-title score. Hard
                  minimum {EVENT_ATTACH_FLOOR.toFixed(2)} — it can be raised,
                  never lowered (precision over recall).
                </span>
              </div>

              <div className="form-group">
                <label>Per-group pattern overrides</label>
                <span className="form-hint">
                  A group with an override uses ONLY its own patterns; other
                  groups keep the shared selection above.
                </span>
                {scopedGroups.length === 0 ? (
                  <span className="form-hint">Pick groups first.</span>
                ) : (
                  scopedGroups.map(group => {
                    const draft = groupOverrides[group.id] ?? EMPTY_GROUP_PATTERN;
                    return (
                      <details key={group.id} className="event-sync-details event-sync-override">
                        <summary>
                          {group.name}
                          {draft.title_pattern.trim() ? ' — override set' : ''}
                        </summary>
                        <div className="event-sync-details-body">
                          <div className="form-group">
                            <label htmlFor={`${id}-ov-${group.id}-title`}>Title pattern</label>
                            <input
                              id={`${id}-ov-${group.id}-title`}
                              type="text"
                              value={draft.title_pattern}
                              onChange={e => updateOverride(group.id, 'title_pattern', e.target.value)}
                              placeholder="Leave empty for no override"
                              disabled={isLoading}
                            />
                          </div>
                          <div className="form-group">
                            <label htmlFor={`${id}-ov-${group.id}-time`}>Time pattern</label>
                            <input
                              id={`${id}-ov-${group.id}-time`}
                              type="text"
                              value={draft.time_pattern}
                              onChange={e => updateOverride(group.id, 'time_pattern', e.target.value)}
                              disabled={isLoading}
                            />
                          </div>
                          <div className="form-group">
                            <label htmlFor={`${id}-ov-${group.id}-date`}>Date pattern</label>
                            <input
                              id={`${id}-ov-${group.id}-date`}
                              type="text"
                              value={draft.date_pattern}
                              onChange={e => updateOverride(group.id, 'date_pattern', e.target.value)}
                              disabled={isLoading}
                            />
                          </div>
                        </div>
                      </details>
                    );
                  })
                )}
              </div>
            </div>
          </details>
        </section>

        {/* Preview */}
        <section className="event-sync-section-block">
          <h3 className="event-sync-section-title">Preview</h3>
          <EventSyncPreviewPanel
            preview={preview}
            loading={previewLoading}
            error={previewError}
            onRunPreview={handleRunPreview}
            disabledReason={validationError}
          />
        </section>
      </div>

      {/* Footer */}
      <div className="event-sync-editor-footer">
        {saveError && (
          <span className="event-sync-save-error" role="alert">{saveError}</span>
        )}
        <button
          type="button"
          className="btn-secondary"
          onClick={onCancel}
          disabled={saving}
        >
          Cancel
        </button>
        <button
          type="button"
          className="btn-primary"
          onClick={handleSave}
          disabled={saving || isLoading}
        >
          {saving ? 'Saving...' : 'Save'}
        </button>
      </div>
    </div>
  );
}
