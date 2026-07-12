/**
 * Event Sync rule editor (bead ti939.1.5 — Phase 1A, preview only).
 *
 * Quick path: pick a master group, pick secondary groups, keep the shipped
 * default patterns, preview. Advanced knobs (time window, attach threshold,
 * dummy EPG profile, per-group pattern overrides) stay collapsed. Auto-sync
 * status is shown LIVE per group with guidance text when it's wrong, plus a
 * guided one-click fix (ti939.3.4): the Fix button opens a confirmation
 * dialog and ONLY its confirm button calls the admin-gated toggle endpoint
 * — never save, never preview, never a side effect.
 *
 * There is NO apply/attach control anywhere: saving stores the config on the
 * rule; the only action against live data is the zero-write preview.
 *
 * API-authored configs round-trip (bead z4y4a): the full patterns /
 * group_patterns arrays pass through the editor state — an untouched save
 * emits the saved arrays verbatim, and pattern entries the UI cannot express
 * (a second+ custom shared pattern, a group's second+ override pattern) are
 * preserved unchanged behind the editable ones (with a read-only indicator),
 * never dropped or reordered. Built-ins are recognized by VERBATIM equality
 * and are never silently re-added to an all-custom selection.
 *
 * Group pickers default to ENABLED groups only (bead x82s3): the master
 * select and secondary multi-select both hide groups whose provider setting
 * (`M3UGroupSetting.enabled`) is not `true` — a group with no settings entry
 * at all counts as not-enabled. A "Show all groups" toggle (default off)
 * reveals the full list. Round-trip guard: a group already referenced by the
 * rule being edited (masterGroupId, or a checked secondary id) always
 * renders — with a "(disabled)" hint — even when the enabled-only filter
 * would otherwise hide it, and `buildConfig` never drops it.
 */
import { useEffect, useId, useMemo, useState } from 'react';
import type { ChannelPipelineRule, CreateRuleData } from '../../types/channelPipeline';
import type { EventSyncConfig, EventSyncPattern, EventSyncPreviewResponse } from '../../types/eventSync';
import type { DummyEPGProfile, M3UGroupSetting } from '../../types';
import {
  getChannelGroups,
  getDummyEPGProfiles,
  getProviderGroupSettings,
  toggleGroupAutoSync,
} from '../../services/api';
import { previewEventSync } from '../../services/channelPipelineApi';
import { CustomSelect } from '../CustomSelect';
import { EventSyncTestPatternsPanel } from './EventSyncTestPatternsPanel';
import type { LabeledEventSyncPattern } from './EventSyncTestPatternsPanel';
import { EventSyncPreviewPanel } from './EventSyncPreviewPanel';
import { EventSyncAutoSyncFixDialog } from './EventSyncAutoSyncFixDialog';
import type { AutoSyncFixTarget } from './EventSyncAutoSyncFixDialog';
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

/**
 * True when a saved pattern is VERBATIM one of the shipped patterns —
 * name AND all three regexes (bead z4y4a). Name-only matching would let a
 * hand-authored pattern that happens to reuse a shipped id be silently
 * replaced by the shipped regexes on resave.
 */
function isShippedVerbatim(pattern: EventSyncPattern): boolean {
  return SHIPPED_EVENT_SYNC_PATTERNS.some(
    shipped =>
      pattern.name === shipped.id &&
      pattern.title_pattern === shipped.pattern.title_pattern &&
      (pattern.time_pattern ?? null) === (shipped.pattern.time_pattern ?? null) &&
      (pattern.date_pattern ?? null) === (shipped.pattern.date_pattern ?? null)
  );
}

/**
 * Initial shipped-pattern selection for an existing rule's config.
 *
 * bead z4y4a: an all-custom saved `patterns` array yields an EMPTY
 * selection — the built-ins must NOT be silently re-added on resave. Only
 * a config with no `patterns` key at all (backend defaults apply) starts
 * from the default selection.
 */
function initialPatternIds(config: EventSyncConfig | null | undefined): string[] {
  if (!config || !config.patterns) return DEFAULT_PATTERN_IDS;
  return config.patterns
    .filter(isShippedVerbatim)
    .map(p => p.name)
    .filter((name): name is string => Boolean(name));
}

/**
 * The saved custom (non-shipped) shared patterns, split into what the UI
 * can edit and what it must preserve (bead z4y4a): the FIRST custom
 * pattern maps onto the editor's single custom-shared draft (its
 * API-authored name is kept for resave); every further custom pattern is
 * inexpressible in this editor and rides along verbatim, never dropped.
 */
function initialCustomSharedState(config: EventSyncConfig | null | undefined): {
  draft: GroupPatternDraft;
  name: string | undefined;
  existed: boolean;
  extras: EventSyncPattern[];
} {
  const customs = (config?.patterns ?? []).filter(p => !isShippedVerbatim(p));
  const first = customs[0];
  return {
    draft: first
      ? {
          title_pattern: first.title_pattern || '',
          time_pattern: first.time_pattern || '',
          date_pattern: first.date_pattern || '',
        }
      : EMPTY_GROUP_PATTERN,
    name: first?.name,
    existed: Boolean(first),
    extras: customs.slice(1),
  };
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

function sameDraft(a: GroupPatternDraft, b: GroupPatternDraft): boolean {
  return (
    a.title_pattern === b.title_pattern &&
    a.time_pattern === b.time_pattern &&
    a.date_pattern === b.date_pattern
  );
}

/** Selection equality is set-like: toggling a box off and back on again is
 * not an edit. */
function sameIds(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every(id => b.includes(id));
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
  // bead x82s3: both group pickers default to enabled-only groups (hundreds
  // of groups on a real instance is noisy); this reveals the full list for
  // edge cases (temporarily-disabled group, a group with no provider
  // settings at all). Default OFF.
  const [showAllGroups, setShowAllGroups] = useState(false);

  // Patterns. `initial` keeps the untouched-open values so save can detect a
  // pristine patterns section and round-trip the saved arrays VERBATIM
  // (bead z4y4a); `customSharedMeta` carries what the UI cannot edit — the
  // first custom pattern's API-authored name and every further custom
  // pattern (preserved read-only, never dropped).
  const [initial] = useState(() => ({
    patternIds: initialPatternIds(config),
    customShared: initialCustomSharedState(config),
    groupOverrides: initialGroupOverrides(config),
  }));
  const customSharedMeta = initial.customShared;
  const [selectedPatternIds, setSelectedPatternIds] = useState<string[]>(
    initial.patternIds
  );
  const [customShared, setCustomShared] = useState<GroupPatternDraft>(
    customSharedMeta.draft
  );
  const [groupOverrides, setGroupOverrides] = useState<Record<number, GroupPatternDraft>>(
    initial.groupOverrides
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
  // Phase 2 opt-in (ti939.3.1): unattended auto-run on the refresh
  // watermark. Default OFF — the backend treats an absent key as false.
  const [autoRun, setAutoRun] = useState(config?.auto_run ?? false);
  // bead 6xxmp: also match the master group's own streams (a second provider
  // sharing the master group's name) to the master channels.
  const [includeMasterGroupStreams, setIncludeMasterGroupStreams] = useState(
    config?.include_master_group_streams ?? false
  );
  // bead assume-current-date: fill the current date for dateless listings.
  const [assumeCurrentDate, setAssumeCurrentDate] = useState(
    config?.assume_current_date ?? false
  );
  // bead parse-from-stream: read master identity from the attached stream.
  const [parseMasterFromStream, setParseMasterFromStream] = useState(
    config?.parse_master_from_stream ?? false
  );
  // Phase 2 (ti939.3.3): optional dummy EPG profile auto-assigned to master
  // channels on every run. null = feature off (key omitted on save).
  const [dummyEpgProfileId, setDummyEpgProfileId] = useState<number | null>(
    config?.dummy_epg_profile_id ?? null
  );
  const [dummyProfiles, setDummyProfiles] = useState<DummyEPGProfile[]>([]);

  // Reference data
  const [channelGroups, setChannelGroups] = useState<{ id: number; name: string }[]>([]);
  const [groupSettings, setGroupSettings] = useState<Record<number, M3UGroupSetting>>({});
  const [settingsLoaded, setSettingsLoaded] = useState(false);

  // Guided setup (ti939.3.4): one-click confirmed auto_channel_sync fix.
  // The toggle API is ONLY called from the confirmation dialog's confirm
  // button — never from save, never from preview.
  const [pendingFix, setPendingFix] = useState<AutoSyncFixTarget | null>(null);
  const [fixBusy, setFixBusy] = useState(false);
  const [fixError, setFixError] = useState<string | null>(null);

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
    getDummyEPGProfiles()
      .then(setDummyProfiles)
      .catch(() => {});
  }, []);

  /** Guided fix (ti939.3.4): the CONFIRMED toggle, then refetch the live
   * group settings so the pre-flight warnings clear. */
  const handleConfirmFix = async () => {
    if (!pendingFix) return;
    setFixBusy(true);
    setFixError(null);
    try {
      await toggleGroupAutoSync(pendingFix.accountId, {
        channel_group_id: pendingFix.groupId,
        auto_channel_sync: pendingFix.enable,
        confirm: true,
      });
      const settings = await getProviderGroupSettings();
      setGroupSettings(settings);
      setPendingFix(null);
    } catch (err) {
      setFixError(err instanceof Error ? err.message : 'Toggle failed');
    } finally {
      setFixBusy(false);
    }
  };

  /** Fix-button target for a provider-backed group, or null when the group
   * has no provider settings (nothing to toggle). */
  const fixTargetFor = (groupId: number, enable: boolean): AutoSyncFixTarget | null => {
    const setting = groupSettings[groupId];
    if (!setting) return null;
    return {
      groupId,
      groupName: groupName(groupId),
      accountId: setting.m3u_account_id,
      accountName: setting.m3u_account_name,
      enable,
    };
  };

  const openFixDialog = (target: AutoSyncFixTarget) => {
    setFixError(null);
    setPendingFix(target);
  };

  const groupName = useMemo(() => {
    const byId = new Map(channelGroups.map(g => [g.id, g.name]));
    return (groupId: number) => byId.get(groupId) ?? `Group ${groupId}`;
  }, [channelGroups]);

  /** Live auto-sync status: true/false when known, null when not provider-backed. */
  const autoSyncStatus = (groupId: number): boolean | null => {
    const setting = groupSettings[groupId];
    return setting ? Boolean(setting.auto_channel_sync) : null;
  };

  /** bead x82s3: 'enabled' = the group's provider (M3U) setting is enabled.
   * A group with no groupSettings entry at all is treated as NOT enabled
   * (hidden by default unless already selected, or Show all groups is on). */
  const isGroupEnabled = (groupId: number): boolean =>
    groupSettings[groupId]?.enabled === true;

  const masterStatus = masterGroupId != null ? autoSyncStatus(masterGroupId) : null;

  const secondariesWithAutoSyncOn = secondaryGroupIds.filter(
    groupId => autoSyncStatus(groupId) === true
  );

  /** Master group options: enabled groups by default, plus Show all groups
   * escape hatch, plus (round-trip guard, bead z4y4a-style) the CURRENTLY
   * selected master group even when it's disabled/hidden — an edited rule
   * must never lose its saved master group just because it's filtered out
   * of the list. Checks `groupSettings` directly (not via the `isGroupEnabled`
   * / `autoSyncStatus` helpers) so the dependency array stays exhaustive
   * without re-deriving on every render. */
  const masterGroupOptions = useMemo(() => {
    return channelGroups
      .filter(g => showAllGroups || groupSettings[g.id]?.enabled === true || g.id === masterGroupId)
      .slice()
      .sort((a, b) => a.name.localeCompare(b.name))
      .map(g => {
        const setting = groupSettings[g.id];
        const statusLabel =
          setting === undefined
            ? 'no provider settings'
            : setting.auto_channel_sync
              ? 'auto-sync ON'
              : 'auto-sync OFF';
        const disabledHint = setting?.enabled === true ? '' : ' (disabled)';
        return { value: g.id.toString(), label: `${g.name} — ${statusLabel}${disabledHint}` };
      });
  }, [channelGroups, groupSettings, showAllGroups, masterGroupId]);

  /** Secondary group options: enabled groups by default (composed with the
   * name filter), plus Show all groups, plus the round-trip guard for any
   * secondary group already checked on this rule. */
  const filteredSecondaryOptions = useMemo(() => {
    const query = secondarySearch.trim().toLowerCase();
    return channelGroups
      .filter(g => g.id !== masterGroupId)
      .filter(g => showAllGroups || groupSettings[g.id]?.enabled === true || secondaryGroupIds.includes(g.id))
      .filter(g => !query || g.name.toLowerCase().includes(query))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [channelGroups, masterGroupId, secondarySearch, showAllGroups, groupSettings, secondaryGroupIds]);

  /** The custom-shared draft as a pattern object, or null when empty. The
   * API-authored name of the first custom pattern is preserved (z4y4a);
   * only a UI-created custom gets the 'custom-shared' name. */
  const buildCustomSharedPattern = (): EventSyncPattern | null => {
    if (!customShared.title_pattern.trim()) return null;
    const name = customSharedMeta.existed
      ? customSharedMeta.name
      : 'custom-shared';
    return {
      ...(name !== undefined ? { name } : {}),
      title_pattern: customShared.title_pattern.trim(),
      ...(customShared.time_pattern.trim()
        ? { time_pattern: customShared.time_pattern.trim() }
        : {}),
      ...(customShared.date_pattern.trim()
        ? { date_pattern: customShared.date_pattern.trim() }
        : {}),
    };
  };

  const effectivePatterns: LabeledEventSyncPattern[] = useMemo(() => {
    const patterns = SHIPPED_EVENT_SYNC_PATTERNS
      .filter(p => selectedPatternIds.includes(p.id))
      .map(p => ({ label: p.label, pattern: p.pattern }));
    if (customShared.title_pattern.trim()) {
      const name = customSharedMeta.existed
        ? customSharedMeta.name
        : 'custom-shared';
      patterns.push({
        label: 'Custom shared pattern',
        pattern: {
          ...(name !== undefined ? { name } : {}),
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
    // Preserved API-authored extras participate in testing/preview too —
    // they ARE part of the rule's effective pattern list.
    customSharedMeta.extras.forEach((pattern, i) => {
      patterns.push({
        label: pattern.name
          ? `API pattern: ${pattern.name}`
          : `API pattern ${i + 2}`,
        pattern,
      });
    });
    return patterns;
  }, [selectedPatternIds, customShared, customSharedMeta]);

  /** Groups in scope (master + secondaries) — for live samples + overrides. */
  const scopedGroups = useMemo(() => {
    const ids = masterGroupId != null ? [masterGroupId, ...secondaryGroupIds] : [...secondaryGroupIds];
    return ids.map(groupId => ({ id: groupId, name: groupName(groupId) }));
  }, [masterGroupId, secondaryGroupIds, groupName]);

  const validationError: string | null = (() => {
    if (masterGroupId == null) return 'Pick a master group first';
    // bead 3ux85: no separate secondary is required when the master group is
    // itself the stream source (include_master_group_streams) — the
    // same-named cross-provider case.
    if (secondaryGroupIds.length === 0 && !includeMasterGroupStreams) {
      return 'Pick at least one secondary group (or enable '
        + '"Also attach the master group’s own streams" under Advanced)';
    }
    if (effectivePatterns.length === 0) {
      return 'Select at least one parse pattern (or add a custom one)';
    }
    return null;
  })();

  /** Build the event_sync_config from the current form state. */
  const buildConfig = (): EventSyncConfig | null => {
    // bead 3ux85: a secondary group is required UNLESS the master group is
    // itself the stream source (include_master_group_streams) — the pure
    // same-named cross-provider case, where Dispatcharr collapses both
    // providers into one channel group so there is no separate secondary.
    if (masterGroupId == null) return null;
    if (secondaryGroupIds.length === 0 && !includeMasterGroupStreams) return null;

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
    // ti939.3.1: emit auto_run when checked, and preserve an explicit stored
    // value (the backend validator fills the key on save, so round-trips
    // keep it). A legacy config without the key stays without it while the
    // box is unchecked — absent means false on the backend.
    if (autoRun || config?.auto_run != null) {
      built.auto_run = autoRun;
    }
    // ti939.3.3: emit the dummy EPG profile reference when selected; a
    // cleared selection omits the key (absent means OFF on the backend —
    // the key is never emitted as null).
    if (dummyEpgProfileId != null) {
      built.dummy_epg_profile_id = dummyEpgProfileId;
    }
    // bead 6xxmp: emit the master self-attach flag when checked; preserve an
    // explicit stored value (the backend validator fills it on save). Absent
    // means false on the backend, so an unchecked legacy config stays absent.
    if (includeMasterGroupStreams || config?.include_master_group_streams != null) {
      built.include_master_group_streams = includeMasterGroupStreams;
    }
    // bead assume-current-date: emit when checked; preserve an explicit
    // stored value (absent means false on the backend).
    if (assumeCurrentDate || config?.assume_current_date != null) {
      built.assume_current_date = assumeCurrentDate;
    }
    // bead parse-from-stream: emit when checked; preserve an explicit stored
    // value (absent means false on the backend).
    if (parseMasterFromStream || config?.parse_master_from_stream != null) {
      built.parse_master_from_stream = parseMasterFromStream;
    }

    // --- Shared patterns (bead z4y4a: full round-trip) -------------------
    // Untouched patterns section + a saved `patterns` array → pass the
    // saved array through VERBATIM: an API-authored config the UI cannot
    // fully express (several customs, custom ordering, names) must survive
    // open → save byte-identically. Only an actual edit rebuilds the array
    // — and even then the inexpressible extras are appended unchanged, in
    // their saved order, never dropped.
    const hasCustomShared = Boolean(customShared.title_pattern.trim());
    const sharedPristine =
      sameIds(selectedPatternIds, initial.patternIds) &&
      sameDraft(customShared, customSharedMeta.draft);
    if (config?.patterns && sharedPristine) {
      built.patterns = config.patterns;
    } else if (
      !selectionIsBuiltinDefaults(selectedPatternIds) ||
      hasCustomShared ||
      customSharedMeta.extras.length > 0
    ) {
      // Selection == exactly the built-ins with no custom and no extras →
      // this branch is skipped and the `patterns` key stays omitted so the
      // backend's own defaults apply (future matcher improvements flow
      // through without editing saved rules).
      const customPattern = buildCustomSharedPattern();
      built.patterns = [
        ...SHIPPED_EVENT_SYNC_PATTERNS
          .filter(p => selectedPatternIds.includes(p.id))
          .map(p => p.pattern),
        ...(customPattern ? [customPattern] : []),
        ...customSharedMeta.extras,
      ];
    }

    // --- Per-group overrides (same round-trip discipline) ----------------
    // The schema rejects group_patterns keys outside the rule's scope, so
    // overrides for de-scoped groups are still filtered out; everything
    // else round-trips: an untouched group keeps its saved list verbatim,
    // an edited group keeps its saved name and its inexpressible extra
    // patterns (patterns[1..]) unchanged behind the edited first pattern.
    const scopedIds = new Set<number>([
      ...(masterGroupId != null ? [masterGroupId] : []),
      ...secondaryGroupIds,
    ]);
    const groupPatternsOut: Record<string, EventSyncPattern[]> = {};
    const savedGroupPatterns = config?.group_patterns ?? {};
    for (const [key, savedList] of Object.entries(savedGroupPatterns)) {
      const groupId = parseInt(key, 10);
      if (Number.isNaN(groupId) || !scopedIds.has(groupId)) continue;
      const draft = groupOverrides[groupId] ?? EMPTY_GROUP_PATTERN;
      const initialDraft = initial.groupOverrides[groupId] ?? EMPTY_GROUP_PATTERN;
      if (sameDraft(draft, initialDraft)) {
        groupPatternsOut[key] = savedList;
        continue;
      }
      const extras = savedList.slice(1);
      const savedName = savedList[0]?.name;
      const edited: EventSyncPattern[] = draft.title_pattern.trim()
        ? [{
            ...(savedName !== undefined ? { name: savedName } : {}),
            title_pattern: draft.title_pattern.trim(),
            ...(draft.time_pattern.trim() ? { time_pattern: draft.time_pattern.trim() } : {}),
            ...(draft.date_pattern.trim() ? { date_pattern: draft.date_pattern.trim() } : {}),
          }]
        : [];
      const list = [...edited, ...extras];
      if (list.length > 0) groupPatternsOut[key] = list;
    }
    for (const [key, draft] of Object.entries(groupOverrides)) {
      const groupId = parseInt(key, 10);
      if (String(groupId) in savedGroupPatterns) continue; // handled above
      if (!scopedIds.has(groupId) || !draft.title_pattern.trim()) continue;
      groupPatternsOut[String(groupId)] = [
        {
          name: `custom-group-${groupId}`,
          title_pattern: draft.title_pattern.trim(),
          ...(draft.time_pattern.trim() ? { time_pattern: draft.time_pattern.trim() } : {}),
          ...(draft.date_pattern.trim() ? { date_pattern: draft.date_pattern.trim() } : {}),
        } satisfies EventSyncPattern,
      ];
    }
    if (Object.keys(groupPatternsOut).length > 0) {
      built.group_patterns = groupPatternsOut;
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
          the default patterns, then Preview. Preview never writes; a manual
          pipeline Run attaches matched streams to master channels — capped
          per run, journaled, and reversible via execution rollback. Event
          Sync runs unattended only if you explicitly enable auto-run under
          Advanced (off by default).
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

        {/* Group visibility (bead x82s3): shared toggle for both the master
            and secondary group pickers below. */}
        <section className="event-sync-section-block">
          <label className="checkbox-option">
            <input
              type="checkbox"
              checked={showAllGroups}
              onChange={e => setShowAllGroups(e.target.checked)}
              disabled={isLoading}
              data-testid="event-sync-show-all-groups"
            />
            <span>Show all groups</span>
          </label>
          <span className="form-hint">
            The master and secondary pickers below list only groups enabled
            on their M3U/provider account by default. Turn this on to see
            every group too — useful for a temporarily-disabled group, or one
            with no provider settings at all.
          </span>
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
            options={masterGroupOptions}
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
                    this group in M3U Manager → account → Groups, or use the
                    Fix button — ECM changes this setting only through that
                    explicitly confirmed fix, never as a side effect of saving
                    or running.
                    {(() => {
                      const target = fixTargetFor(masterGroupId, true);
                      return target ? (
                        <button
                          type="button"
                          className="btn-secondary event-sync-fix-btn"
                          data-testid="master-autosync-fix"
                          onClick={() => openFixDialog(target)}
                          disabled={isLoading}
                        >
                          Fix: turn auto-sync ON…
                        </button>
                      ) : null;
                    })()}
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
                const disabled = !isGroupEnabled(group.id);
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
                      {disabled && (
                        <span className="event-sync-disabled-hint">(disabled)</span>
                      )}
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
                Manager → account → Groups, or use the Fix buttons — ECM
                changes this setting only through that explicitly confirmed
                fix, never as a side effect of saving or running.
                {secondariesWithAutoSyncOn.map(groupId => {
                  const target = fixTargetFor(groupId, false);
                  return target ? (
                    <button
                      key={groupId}
                      type="button"
                      className="btn-secondary event-sync-fix-btn"
                      data-testid={`secondary-autosync-fix-${groupId}`}
                      onClick={() => openFixDialog(target)}
                      disabled={isLoading}
                    >
                      Fix: turn auto-sync OFF for {groupName(groupId)}…
                    </button>
                  ) : null;
                })}
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
              {customSharedMeta.extras.length > 0 && (
                <span
                  className="form-hint"
                  data-testid="custom-shared-extras"
                >
                  {customSharedMeta.extras.length} additional API-authored
                  shared pattern{customSharedMeta.extras.length === 1 ? '' : 's'}{' '}
                  ({customSharedMeta.extras
                    .map((p, i) => p.name || `#${i + 2}`)
                    .join(', ')}) {customSharedMeta.extras.length === 1 ? 'is' : 'are'}{' '}
                  preserved as saved — this editor can edit only the first
                  custom pattern; the rest are applied after it and never
                  dropped on save.
                </span>
              )}
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
                <label className="checkbox-option">
                  <input
                    type="checkbox"
                    checked={autoRun}
                    onChange={e => setAutoRun(e.target.checked)}
                    disabled={isLoading}
                    data-testid="event-sync-auto-run"
                  />
                  <span>Run automatically after each M3U refresh (auto-run)</span>
                </label>
                <span className="form-hint">
                  Off by default — enable it only after you trust this
                  rule&apos;s manual runs. When on, the rule runs unattended
                  after every M3U refresh with the same journaling, per-run
                  attach cap, and run summary line as a manual run. Attach-cap
                  overages and failed pre-flight checks (e.g. master
                  auto-sync turned OFF) raise warning notifications. A
                  tripped auto-creation circuit breaker pauses auto-runs
                  until you reset it; manual runs stay available. A run
                  landing right after a refresh can precede Dispatcharr
                  creating a brand-new event&apos;s master channel — that
                  stream attaches on the next run.
                </span>
              </div>

              <div className="form-group">
                <label className="checkbox-option">
                  <input
                    type="checkbox"
                    checked={includeMasterGroupStreams}
                    onChange={e => setIncludeMasterGroupStreams(e.target.checked)}
                    disabled={isLoading}
                    data-testid="event-sync-include-master-group-streams"
                  />
                  <span>Also attach the master group&apos;s own streams</span>
                </label>
                <span className="form-hint">
                  Off by default. Turn this on when a second provider&apos;s
                  streams live in the <em>same-named</em> channel group as the
                  master (Dispatcharr requires channel-group names to be
                  unique, so both providers share <strong>one</strong> group —
                  it cannot be picked twice). When on, the master group&apos;s
                  streams are matched to the master channels too; streams
                  already attached (the auto-synced provider&apos;s own) are
                  skipped, so only the unsynced provider&apos;s streams attach.
                  <strong> With this on you can leave the secondary list empty</strong>
                  {' '}— the master group is the stream source.
                </span>
              </div>

              <div className="form-group">
                <label className="checkbox-option">
                  <input
                    type="checkbox"
                    checked={parseMasterFromStream}
                    onChange={e => setParseMasterFromStream(e.target.checked)}
                    disabled={isLoading}
                    data-testid="event-sync-parse-master-from-stream"
                  />
                  <span>Read master event time from the attached stream</span>
                </label>
                <span className="form-hint">
                  Off by default. Event Sync normally reads a master
                  channel&apos;s date/time from its <em>name</em>. Turn this on
                  to read it from the master channel&apos;s <strong>first
                  attached stream</strong> instead — so you can name the master
                  channels however you like while the event time still comes
                  from the underlying auto-synced stream. (If a master channel
                  has no attached stream, it is skipped.)
                </span>
              </div>

              <div className="form-group">
                <label className="checkbox-option">
                  <input
                    type="checkbox"
                    checked={assumeCurrentDate}
                    onChange={e => setAssumeCurrentDate(e.target.checked)}
                    disabled={isLoading}
                    data-testid="event-sync-assume-current-date"
                  />
                  <span>Assume today&apos;s date for dateless listings</span>
                </label>
                <span className="form-hint">
                  Off by default. Some providers list a live schedule with a
                  time but <em>no date</em> (e.g. &quot;FURY vs HALL 6PM&quot;).
                  Normally those can&apos;t be matched (the time could be any
                  day). Turn this on to place such listings on the{' '}
                  <strong>current date</strong> so they match same-day events.
                  Risk: a listing that is really for another day (e.g. a replay
                  at the same time tomorrow) can mis-match — the ±time window
                  still applies, but same-time-of-day collisions can slip
                  through. Leave off unless the group only ever lists today.
                </span>
              </div>

              <div className="form-group">
                <label>Dummy EPG profile (optional)</label>
                <CustomSelect
                  value={dummyEpgProfileId != null ? dummyEpgProfileId.toString() : ''}
                  onChange={value =>
                    setDummyEpgProfileId(value ? parseInt(value, 10) : null)
                  }
                  options={[
                    { value: '', label: 'None — no automatic guide data' },
                    ...dummyProfiles.map(p => ({
                      value: p.id.toString(),
                      label: p.enabled ? p.name : `${p.name} (disabled)`,
                    })),
                  ]}
                  placeholder="None — no automatic guide data"
                  disabled={isLoading}
                />
                <span className="form-hint">
                  Assigns this dummy EPG profile to the master group&apos;s
                  event channels on every run (manual and auto-run), so new
                  events get guide data automatically. Channels the
                  profile&apos;s XMLTV does not cover yet are retried after an
                  automatic regenerate + refresh in the same run. Existing
                  guide data from other sources is never overwritten. Tip: the
                  profile&apos;s title/time patterns can reuse this rule&apos;s
                  parse patterns — the master provider&apos;s naming is the
                  same in both places.
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
                    const savedExtras = (
                      config?.group_patterns?.[String(group.id)] ?? []
                    ).slice(1);
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
                          {savedExtras.length > 0 && (
                            <span
                              className="form-hint"
                              data-testid={`group-override-extras-${group.id}`}
                            >
                              {savedExtras.length} additional API-authored
                              pattern{savedExtras.length === 1 ? '' : 's'} for
                              this group (
                              {savedExtras
                                .map((p, i) => p.name || `#${i + 2}`)
                                .join(', ')}
                              ) {savedExtras.length === 1 ? 'is' : 'are'}{' '}
                              preserved as saved — this editor edits only the
                              group&apos;s first pattern; the rest are applied
                              after it and never dropped on save.
                            </span>
                          )}
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

      {/* Guided setup (ti939.3.4): the toggle API is reachable ONLY through
          this dialog's confirm button. */}
      {pendingFix && (
        <EventSyncAutoSyncFixDialog
          target={pendingFix}
          busy={fixBusy}
          error={fixError}
          onCancel={() => setPendingFix(null)}
          onConfirm={handleConfirmFix}
        />
      )}
    </div>
  );
}
