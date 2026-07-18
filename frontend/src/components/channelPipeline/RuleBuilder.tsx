/**
 * Component for building and editing channel pipeline rules.
 *
 * Layout (bead m1s38.3): a grouped two-pane consuming the SHARED modal-layout
 * primitives from ModalBase.css (.modal-twopane / .modal-main / .modal-rail /
 * .modal-stepper / .modal-intent / .modal-why). It is NOT a Next/Back wizard —
 * conditions + actions are one "when X do Y" statement, so all phases stay
 * visible and a scroll-spy phase spine tracks the active section. Phases are
 * ordered logic-FIRST: 1 Logic (conditions -> actions), 2 Targeting (merge
 * scope, manual protection, normalization), 3 Output & Run (channel/stream
 * sort, orphan cleanup, run-behavior flags).
 *
 * The right rail carries a client-side intent sentence (plain-language, with
 * bold destructive/risky clauses), advisory analyzer chips (debounced, from the
 * advisory analyze-body endpoint — NEVER gates Save), and a live save hint.
 *
 * Dirty-guard (correctness-critical): the header × / overlay Escape route
 * through the registered `attemptClose`, and the dirty check is a FULL-FIELD
 * comparison via the shared configDirty comparator (catches deep edits to
 * existing conditions/actions and every option/sort/scope field), so no edit is
 * ever discarded silently.
 */
import { useState, useEffect, useId, useCallback, useMemo, useRef } from 'react';
import type { ReactNode } from 'react';
import type { ChannelPipelineRule, CreateRuleData, Condition, Action, ConditionType, ActionType } from '../../types/channelPipeline';
import type { RuleAnalyzerFinding } from '../../types/channelPipeline';
import { ConditionEditor } from './ConditionEditor';
import { ActionEditor } from './ActionEditor';
import { CustomSelect } from '../CustomSelect';
import { GroupMultiSelectDropdown } from '../GroupMultiSelectDropdown';
import { ModalOverlay } from '../ModalOverlay';
import { getNormalizationRules, getChannelGroups } from '../../services/api';
import { analyzeChannelPipelineRuleBody } from '../../services/channelPipelineApi';
import { stableStringify } from '../../utils/configDirty';
import './RuleBuilder.css';

export interface RuleBuilderProps {
  rule?: Partial<ChannelPipelineRule>;
  onSave: (data: CreateRuleData) => Promise<void> | void;
  onCancel: () => void;
  isLoading?: boolean;
  /**
   * Register a guarded-close callback with the parent (mirrors the Event Sync
   * editor). The parent's overlay Escape / header × invoke this so a dirty form
   * routes through the discard confirm instead of dropping edits silently.
   */
  onRegisterClose?: (attemptClose: (() => void) | null) => void;
}

interface ValidationErrors {
  name?: string;
  conditions?: string;
  actions?: string;
}

/** Debounce for the advisory analyzer call (home-lab: 250-500ms is plenty). */
const ANALYZE_DEBOUNCE_MS = 400;

/**
 * The three wizard steps (bead 09x38.10). The Standard editor is now a true
 * step-at-a-time wizard mirroring the Event Sync editor: one step renders at a
 * time, Back/Next drive `currentStep`, and the numbered pills reuse the shared
 * `.modal-stepper` strip. No Review step — unlike Event Sync (whose step 4
 * gates a Preview before any write), the Standard rule has no preview/write
 * gate, and the persistent rail already synthesizes the whole rule (intent /
 * analyzer / save hint) continuously, so a dedicated Review step would only
 * duplicate the rail.
 */
type WizardStep = 1 | 2 | 3;
const WIZARD_STEPS: { n: WizardStep; label: string }[] = [
  { n: 1, label: 'Logic' },
  { n: 2, label: 'Targeting' },
  { n: 3, label: 'Output & Run' },
];
const LAST_STEP: WizardStep = 3;

const ACTION_LABELS: Record<string, string> = {
  create_channel: 'create a channel',
  create_group: 'create a group',
  merge_streams: 'merge streams',
  skip: 'skip the stream',
  set_logo: 'set the logo',
  assign_epg: 'assign EPG',
  assign_channel_number: 'assign a channel number',
  set_channel_group: 'set the channel group',
  rename_channel: 'rename the channel',
};

function actionLabel(type: string): string {
  return ACTION_LABELS[type] || (type ? type.replace(/_/g, ' ') : 'an action');
}

const CHANNEL_SORT_LABELS: Record<string, string> = {
  stream_name: 'stream name',
  stream_name_natural: 'stream name (natural)',
  group_name: 'group name',
  quality: 'quality',
  stream_name_regex: 'a stream-name pattern',
  provider_order: 'provider order',
  channel_number: 'channel number',
};

const STREAM_SORT_LABELS: Record<string, string> = {
  smart_sort: 'smart sort',
  quality: 'quality',
  stream_name: 'stream name',
  stream_name_natural: 'stream name (natural)',
  provider_order: 'provider order',
};

function severityBadge(severity: RuleAnalyzerFinding['severity']): string {
  if (severity === 'error') return 'badge-error';
  if (severity === 'warning') return 'badge-warning';
  return 'badge-info';
}

// Whether a create_channel action's Channel Number resolves to "auto" (the
// engine's default sentinel). Full mirror of the backend's
// _get_rule_starting_number (channel_pipeline_engine.py): unset defaults to
// "auto"; an integer is a fixed start; the literal string "auto" is auto; a
// range string like "500-999" uses the start segment; and anything
// unparseable as an integer (empty string, malformed range, non-numeric
// text) raises ValueError there and falls back to auto — i.e. the renumber
// pass is skipped. This is a different question from ActionEditor's local
// parseStartingNumber (which only detects the "N-99999" range format for its
// own mode toggle).
function isAutoChannelNumber(spec: string | number | undefined): boolean {
  if (spec === undefined || spec === null) return true;
  if (typeof spec === 'number') return false;
  if (spec === 'auto') return true;
  // Range strings like "500-999" — the backend uses the start segment.
  const part = spec.includes('-') ? spec.split('-')[0] : spec;
  // Python int() accepts optional sign + digits with surrounding whitespace;
  // anything else is a ValueError -> treated as auto by the backend.
  return !/^\s*[+-]?\d+\s*$/.test(part);
}

export function RuleBuilder({
  rule,
  onSave,
  onCancel,
  isLoading = false,
  onRegisterClose,
}: RuleBuilderProps) {
  const id = useId();
  const [name, setName] = useState(rule?.name || '');
  const [description, setDescription] = useState(rule?.description || '');
  const [priority, ] = useState(rule?.priority ?? 0);
  const [enabled, setEnabled] = useState(rule?.enabled ?? true);
  const [runOnRefresh, setRunOnRefresh] = useState(rule?.run_on_refresh ?? false);
  const [stopOnFirstMatch, setStopOnFirstMatch] = useState(rule?.stop_on_first_match ?? true);
  const [sortField, setSortField] = useState(rule?.sort_field ?? '');
  const [sortOrder, setSortOrder] = useState(rule?.sort_order || 'asc');
  const [probeOnSort, setProbeOnSort] = useState(rule?.probe_on_sort ?? false);
  const [sortRegex, setSortRegex] = useState(rule?.sort_regex || '');
  const [streamSortField, setStreamSortField] = useState(rule?.stream_sort_field ?? 'smart_sort');
  const [streamSortOrder, setStreamSortOrder] = useState(rule?.stream_sort_order || 'asc');
  const [qualityTieBreakOrder, setQualityTieBreakOrder] = useState<'asc' | 'desc'>(
    (rule?.quality_tie_break_order as 'asc' | 'desc') || 'desc'
  );
  const [qualityM3uTieBreakEnabled, setQualityM3uTieBreakEnabled] = useState(
    rule?.quality_m3u_tie_break_enabled ?? true
  );
  const [normalizationGroupIds, setNormalizationGroupIds] = useState<number[]>(rule?.normalization_group_ids ?? []);
  const [skipStruckStreams, setSkipStruckStreams] = useState(rule?.skip_struck_streams ?? false);
  const [orphanAction, setOrphanAction] = useState(rule?.orphan_action || 'delete');
  const [matchScopeTargetGroup, setMatchScopeTargetGroup] = useState(rule?.match_scope_target_group ?? true);
  const [matchScopeGroupId, setMatchScopeGroupId] = useState<number | null>(rule?.match_scope_group_id ?? null);
  const [allowManualChannelMerge, setAllowManualChannelMerge] = useState(rule?.allow_manual_channel_merge ?? false);
  const [foldMatchKey, setFoldMatchKey] = useState(rule?.fold_match_key ?? false);
  const [conditions, setConditions] = useState<Condition[]>(rule?.conditions || []);
  const [actions, setActions] = useState<Action[]>(rule?.actions || []);

  const [availableNormGroups, setAvailableNormGroups] = useState<{id: number; name: string; enabled: boolean}[]>([]);
  const [availableChannelGroups, setAvailableChannelGroups] = useState<{id: number; name: string}[]>([]);
  const [errors, setErrors] = useState<ValidationErrors>({});
  const [saving, setSaving] = useState(false);
  const [showCancelConfirm, setShowCancelConfirm] = useState(false);
  const [analyzerFindings, setAnalyzerFindings] = useState<RuleAnalyzerFinding[]>([]);

  // True step-at-a-time wizard (bead 09x38.10): exactly one step renders at a
  // time; Back/Next and the numbered pills drive `currentStep`. The persistent
  // rail (intent / analyzer / save hint) reads full form state and stays
  // visible across every step.
  const [currentStep, setCurrentStep] = useState<WizardStep>(1);

  // Per-step heading refs — focus the active step's heading on step change so
  // keyboard/AT users land in the newly revealed step. Skips the initial mount
  // so opening the editor does not steal focus from the name field.
  const logicHeadingRef = useRef<HTMLHeadingElement>(null);
  const targetingHeadingRef = useRef<HTMLHeadingElement>(null);
  const outputHeadingRef = useRef<HTMLHeadingElement>(null);
  const didInitStep = useRef(false);

  // Load available normalization groups
  useEffect(() => {
    getNormalizationRules().then(({ groups }) => {
      setAvailableNormGroups(groups.map(g => ({ id: g.id, name: g.name, enabled: g.enabled })));
    }).catch(() => {});
  }, []);

  // Load available channel groups for the merge-scope group selector (GH #298)
  useEffect(() => {
    getChannelGroups().then(groups => {
      setAvailableChannelGroups(groups.map(g => ({ id: g.id, name: g.name })));
    }).catch(() => {});
  }, []);

  // Focus the active step's heading when the step changes (not on first mount).
  useEffect(() => {
    if (!didInitStep.current) {
      didInitStep.current = true;
      return;
    }
    const headingRef =
      currentStep === 1
        ? logicHeadingRef
        : currentStep === 2
          ? targetingHeadingRef
          : outputHeadingRef;
    headingRef.current?.focus();
  }, [currentStep]);

  const handleReorderCondition = (fromIndex: number, newPosition: number) => {
    const toIndex = newPosition - 1;
    if (toIndex === fromIndex || toIndex < 0 || toIndex >= conditions.length) return;
    const newConditions = [...conditions];
    const [moved] = newConditions.splice(fromIndex, 1);
    newConditions.splice(toIndex, 0, moved);
    setConditions(newConditions);
  };

  const handleReorderAction = (fromIndex: number, newPosition: number) => {
    const toIndex = newPosition - 1;
    if (toIndex === fromIndex || toIndex < 0 || toIndex >= actions.length) return;
    const newActions = [...actions];
    const [moved] = newActions.splice(fromIndex, 1);
    newActions.splice(toIndex, 0, moved);
    setActions(newActions);
  };

  // The full rule config as currently authored. This is the single source of
  // truth for BOTH the dirty check and the advisory analyzer body, so the two
  // can never diverge from what Save persists.
  const buildConfig = useCallback((): CreateRuleData => ({
    name: name.trim(),
    description: description.trim() || undefined,
    enabled,
    priority,
    conditions,
    actions,
    run_on_refresh: runOnRefresh,
    stop_on_first_match: stopOnFirstMatch,
    sort_field: sortField || '',
    sort_order: sortOrder,
    probe_on_sort: probeOnSort,
    sort_regex: sortRegex || '',
    stream_sort_field: streamSortField || '',
    stream_sort_order: streamSortOrder,
    quality_tie_break_order: qualityTieBreakOrder,
    quality_m3u_tie_break_enabled: qualityM3uTieBreakEnabled,
    normalization_group_ids: normalizationGroupIds,
    skip_struck_streams: skipStruckStreams,
    orphan_action: orphanAction,
    match_scope_target_group: matchScopeTargetGroup,
    match_scope_group_id: matchScopeTargetGroup ? matchScopeGroupId : null,
    allow_manual_channel_merge: allowManualChannelMerge,
    fold_match_key: foldMatchKey,
  }), [
    name, description, enabled, priority, conditions, actions, runOnRefresh,
    stopOnFirstMatch, sortField, sortOrder, probeOnSort, sortRegex, streamSortField,
    streamSortOrder, qualityTieBreakOrder, qualityM3uTieBreakEnabled, normalizationGroupIds,
    skipStruckStreams, orphanAction, matchScopeTargetGroup, matchScopeGroupId,
    allowManualChannelMerge, foldMatchKey,
  ]);

  // FULL-FIELD dirty check (bead m1s38.3 data-loss fix). Replaces the old
  // count-only comparison — a deep edit to an existing condition/action, or a
  // change to any option/sort/scope/normalization/orphan field, now counts as
  // dirty. A reorder without content change counts as dirty too: for a
  // data-loss guard a false-positive confirm is the safe direction. Uses the
  // SHARED stableStringify so RuleBuilder and the Event Sync editor agree on
  // what "changed" means.
  const currentConfigString = useMemo(() => stableStringify(buildConfig()), [buildConfig]);
  const baselineRef = useRef<string | null>(null);
  if (baselineRef.current === null) {
    // First render — state equals the rule as loaded, so this snapshot is the
    // clean baseline to diff against.
    baselineRef.current = currentConfigString;
  }
  const [savedConfigString, setSavedConfigString] = useState<string | null>(null);
  const dirty = currentConfigString !== (savedConfigString ?? baselineRef.current);
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;

  // Advisory analyzer chips (bead m1s38.2 endpoint). Debounced; advisory ONLY —
  // findings never disable or gate Save. Superseded / unmounted calls are
  // dropped via the `active` guard.
  useEffect(() => {
    let active = true;
    const body = buildConfig();
    const handle = window.setTimeout(() => {
      analyzeChannelPipelineRuleBody(body)
        .then(res => {
          if (active) setAnalyzerFindings(res.rules?.[0]?.findings ?? []);
        })
        .catch(() => {
          if (active) setAnalyzerFindings([]);
        });
    }, ANALYZE_DEBOUNCE_MS);
    return () => {
      active = false;
      window.clearTimeout(handle);
    };
  }, [buildConfig]);

  const validate = useCallback((): ValidationErrors | null => {
    const newErrors: ValidationErrors = {};

    if (!name.trim()) {
      newErrors.name = 'Name is required';
    }

    if (conditions.length === 0) {
      newErrors.conditions = 'At least one condition is required';
    } else {
      // Validate each condition
      for (const condition of conditions) {
        if (needsValue(condition.type) && !condition.value && condition.value !== 0) {
          newErrors.conditions = 'Value is required for some conditions';
          break;
        }
      }
    }

    if (actions.length === 0) {
      newErrors.actions = 'At least one action is required';
    } else if (actions.some(a => !a.type)) {
      newErrors.actions = 'All actions must have a type selected';
    } else {
      // Validate individual action fields
      for (const [i, action] of actions.entries()) {
        if (action.type === 'create_channel' && !action.group_id) {
          const hasPriorCreateGroup = actions.slice(0, i).some(a => a.type === 'create_group');
          if (!hasPriorCreateGroup) {
            newErrors.actions = 'Create Channel requires a target group (or a prior Create Group action)';
            break;
          }
        }
      }
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0 ? null : newErrors;
  }, [name, conditions, actions]);

  // Advisory-only save hint shown in the rail. Deliberately NOT tied to the
  // Save button's disabled state (Save stays clickable and validates on click).
  // Wording avoids the "at least one ..." validation phrasing so the two never
  // collide in the DOM.
  const saveHint: string | null = !name.trim()
    ? 'Name the rule to save it.'
    : conditions.length === 0
      ? 'Add a condition to save it.'
      : actions.length === 0
        ? 'Add an action to save it.'
        : null;

  const handleSave = async () => {
    const validationErrors = validate();
    if (validationErrors) {
      // Per-step validation display routing (bead 09x38.10): every validation
      // error surfaces on Step 1 — the name (in the always-visible header) plus
      // the conditions and actions in the Logic step — so route there so the
      // error is visible from whichever step Save was pressed, then focus the
      // first offending field.
      setCurrentStep(1);
      if (validationErrors.name) {
        document.getElementById(`${id}-name`)?.focus();
      }
      return;
    }

    const configAtSave = currentConfigString;
    setSaving(true);
    try {
      await onSave(buildConfig());
      // Save succeeded — this config is now the clean baseline, so closing
      // afterward does not re-prompt the discard guard.
      setSavedConfigString(configAtSave);
    } finally {
      setSaving(false);
    }
  };

  // Guarded close (bead m1s38.3): confirm only when dirty; otherwise close at
  // once so a modal opened by mistake is not held hostage by a prompt.
  const attemptClose = useCallback(() => {
    if (dirtyRef.current) setShowCancelConfirm(true);
    else onCancel();
  }, [onCancel]);

  // Register the guard so the parent overlay's Escape / header × route through
  // the same confirm (previously only the footer Cancel was guarded — ×/Escape
  // discarded silently).
  useEffect(() => {
    onRegisterClose?.(attemptClose);
    return () => onRegisterClose?.(null);
  }, [onRegisterClose, attemptClose]);

  const handleAddCondition = () => {
    const newCondition: Condition = { type: 'stream_name_contains', connector: 'and' };
    setConditions([...conditions, newCondition]);
  };

  const handleToggleConnector = (index: number) => {
    const newConditions = [...conditions];
    const current = newConditions[index].connector || 'and';
    newConditions[index] = { ...newConditions[index], connector: current === 'and' ? 'or' : 'and' };
    setConditions(newConditions);
  };

  const handleUpdateCondition = (index: number, updated: Condition) => {
    const newConditions = [...conditions];
    newConditions[index] = updated;
    setConditions(newConditions);
  };

  const handleRemoveCondition = (index: number) => {
    setConditions(conditions.filter((_, i) => i !== index));
  };

  const handleAddAction = () => {
    const newAction: Action = { type: '' as ActionType };
    setActions([...actions, newAction]);
  };

  const handleUpdateAction = (index: number, updated: Action) => {
    const newActions = [...actions];
    newActions[index] = updated;
    setActions(newActions);
  };

  const handleRemoveAction = (index: number) => {
    setActions(actions.filter((_, i) => i !== index));
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey && e.target instanceof HTMLInputElement) {
      e.preventDefault();
      handleSave();
    }
  };

  // Plain step change (Back / Next / step pill). Never routes through the dirty
  // guard — only Cancel / Escape / × do (mirrors the Event Sync editor).
  const goToStep = (step: WizardStep) => setCurrentStep(step);

  // GH #298: an effective scope group resolves for create_channel when a
  // Create Channel action carries a target group, OR a Create Group action
  // exists (which sets the group used by a later Create Channel). When neither
  // holds (e.g. a Merge-Streams-only rule) and no explicit Scope group is set,
  // the merge lookup falls back to ALL groups — warn the operator about that.
  const ruleResolvesAScopeGroup =
    actions.some(a => a.type === 'create_channel' && a.group_id != null) ||
    actions.some(a => a.type === 'create_group');

  const orphansDeleted = orphanAction === 'delete' || orphanAction === 'delete_and_cleanup_groups';

  // Sort-vs-numbering gotcha (enhancedchannelmanager-bn0wa / GH #644):
  // sort_field only orders stream PROCESSING, and the rule-level renumber it
  // enables is silently skipped when the rule's Channel Number resolves to
  // Auto. The backend's _get_rule_starting_number returns on the FIRST
  // create_channel action it finds — later create_channel actions are never
  // consulted — so the hint must match: only the first create_channel action
  // decides. Point the operator at Sort Group (which always renumbers) or a
  // fixed number range instead of leaving them to discover the skip by trial
  // and error.
  const firstCreateChannel = actions.find(a => a.type === 'create_channel');
  const hasAutoNumberedCreateChannel =
    firstCreateChannel !== undefined && isAutoChannelNumber(firstCreateChannel.channel_number);
  const showSortNumberingHint = !!sortField && hasAutoNumberedCreateChannel;

  // Client-side, plain-language intent sentence. Bold clauses are the
  // destructive / risky choices (orphan delete, manual-channel merge, stop-on-
  // first-match) so the operator sees at a glance what the rule will do.
  const intent: ReactNode = useMemo(() => {
    if (conditions.length === 0 && actions.length === 0) {
      return (
        <span className="modal-intent-placeholder">
          Add a condition and an action to see what this rule will do.
        </span>
      );
    }
    const condClause =
      conditions.length === 0 ? (
        <>every stream</>
      ) : (
        <>
          a stream matches {conditions.length} condition{conditions.length === 1 ? '' : 's'}
        </>
      );
    const distinctActions = Array.from(new Set(actions.map(a => a.type).filter(Boolean))) as string[];
    const actClause =
      actions.length === 0 ? (
        <>do nothing yet</>
      ) : distinctActions.length === 0 ? (
        <>run {actions.length} action{actions.length === 1 ? '' : 's'}</>
      ) : (
        <>{distinctActions.map(actionLabel).join(', ')}</>
      );
    return (
      <>
        When {condClause}, {actClause}.
        {sortField && <> Renumbers channels by {CHANNEL_SORT_LABELS[sortField] || sortField}.</>}
        {streamSortField && (
          <> Orders streams by {STREAM_SORT_LABELS[streamSortField] || streamSortField}.</>
        )}
        {orphansDeleted ? (
          <> <strong>Orphaned channels are deleted.</strong></>
        ) : orphanAction === 'move_uncategorized' ? (
          <> Orphaned channels move to Uncategorized.</>
        ) : null}
        {allowManualChannelMerge && <> <strong>May merge streams into manual channels.</strong></>}
        {foldMatchKey && <> Merge matching ignores spacing &amp; case differences.</>}
        {stopOnFirstMatch && <> <strong>Stops after the first matching rule.</strong></>}
        {runOnRefresh && <> Runs automatically after each M3U refresh.</>}
      </>
    );
  }, [
    conditions, actions, sortField, streamSortField, orphansDeleted, orphanAction,
    allowManualChannelMerge, foldMatchKey, stopOnFirstMatch, runOnRefresh,
  ]);

  return (
    <div className="rule-builder" data-testid="rule-builder" onKeyDown={handleKeyDown}>
      {isLoading && (
        <div className="loading-overlay" data-testid="loading-indicator">
          <div className="loading-spinner"></div>
          <span>Loading...</span>
        </div>
      )}

      <div className="rule-builder-content">
        {/* Compact always-visible header: name*, enabled, description. */}
        <div className="rule-builder-headerbar">
          <div className="rule-builder-headerbar-row">
            <div className="form-field rule-builder-name-field">
              <label htmlFor={`${id}-name`}>Rule Name *</label>
              <input
                id={`${id}-name`}
                type="text"
                value={name}
                onChange={e => setName(e.target.value)}
                placeholder="Enter rule name"
                disabled={isLoading}
                aria-required="true"
                aria-describedby={errors.name ? `${id}-name-error` : undefined}
                aria-invalid={!!errors.name}
                aria-label="Rule name"
              />
              {errors.name && (
                <div id={`${id}-name-error`} className="field-error" role="alert">
                  {errors.name}
                </div>
              )}
            </div>
            <label className="checkbox-item rule-builder-enabled">
              <input
                type="checkbox"
                checked={enabled}
                onChange={e => setEnabled(e.target.checked)}
                disabled={isLoading}
                aria-label="Enabled"
              />
              <span>Enabled</span>
            </label>
          </div>
          <div className="form-field">
            <label htmlFor={`${id}-description`}>Description</label>
            <textarea
              id={`${id}-description`}
              value={description}
              onChange={e => setDescription(e.target.value)}
              placeholder="Optional description"
              disabled={isLoading}
              rows={2}
              aria-label="Description"
            />
          </div>
        </div>

        {/* True step wizard strip (bead 09x38.10) — pills drive the single
            rendered step; the rail is persistent across steps. Free non-linear
            nav: any pill is clickable at any time (mirrors the Event Sync
            editor). */}
        <nav className="modal-stepper" aria-label="Rule steps">
          {WIZARD_STEPS.map(({ n, label }) => (
            <button
              key={n}
              type="button"
              className="modal-stepper-item"
              aria-current={currentStep === n ? 'step' : undefined}
              onClick={() => goToStep(n)}
              data-testid={`rule-step-${n}`}
            >
              <span className="modal-stepper-num">{n}</span> {label}
            </button>
          ))}
        </nav>

        <div className="modal-twopane">
          <div className="modal-main">
            {/* ── Step 1: LOGIC (conditions -> actions) — the star ────────── */}
            <section
              hidden={currentStep !== 1}
              className="rule-phase"
              aria-labelledby={`${id}-logic-title`}
            >
              <h2
                className="rule-phase-title"
                id={`${id}-logic-title`}
                ref={logicHeadingRef}
                tabIndex={-1}
              >
                <span className="rule-phase-num">1</span> Logic
              </h2>

              <div className="rule-section">
                <div className="section-header">
                  <h3 className="section-title">Conditions</h3>
                  <span className="section-hint">Define when this rule should apply</span>
                </div>

                {errors.conditions && (
                  <div className="section-error" role="alert">{errors.conditions}</div>
                )}

                <div className="conditions-list">
                  {conditions.map((condition, index) => (
                    <div key={index}>
                      {index > 0 && (
                        <div className="condition-connector">
                          <button
                            type="button"
                            className={`connector-toggle ${(condition.connector || 'and') === 'or' ? 'connector-or' : ''}`}
                            onClick={() => handleToggleConnector(index)}
                            title="Click to toggle between AND/OR"
                          >
                            {(condition.connector || 'and').toUpperCase()}
                          </button>
                        </div>
                      )}
                      <ConditionEditor
                        condition={condition}
                        onChange={updated => handleUpdateCondition(index, updated)}
                        onRemove={() => handleRemoveCondition(index)}
                        showValidation={Object.keys(errors).length > 0}
                        showNegateOption
                        showCaseSensitiveOption
                        orderNumber={index + 1}
                        totalItems={conditions.length}
                        onReorder={newPos => handleReorderCondition(index, newPos)}
                      />
                    </div>
                  ))}
                </div>

                <div className="add-item-wrapper">
                  <button
                    type="button"
                    className="add-item-btn"
                    onClick={handleAddCondition}
                    aria-label="Add condition"
                  >
                    <span className="material-icons">add</span>
                    Add Condition
                  </button>
                </div>
              </div>

              <div className="rule-section">
                <div className="section-header">
                  <h3 className="section-title">Actions</h3>
                  <span className="section-hint">Define what happens when conditions match</span>
                </div>

                {errors.actions && (
                  <div className="section-error" role="alert">{errors.actions}</div>
                )}

                <div className="actions-list">
                  {actions.map((action, index) => (
                    <ActionEditor
                      key={index}
                      action={action}
                      onChange={updated => handleUpdateAction(index, updated)}
                      onRemove={() => handleRemoveAction(index)}
                      showValidation={Object.keys(errors).length > 0}
                      showPreview
                      previousActions={actions.slice(0, index)}
                      orderNumber={index + 1}
                      totalItems={actions.length}
                      onReorder={newPos => handleReorderAction(index, newPos)}
                    />
                  ))}
                </div>

                <div className="add-item-wrapper">
                  <button
                    type="button"
                    className="add-item-btn"
                    onClick={handleAddAction}
                    aria-label="Add action"
                  >
                    <span className="material-icons">add</span>
                    Add Action
                  </button>
                </div>
              </div>
            </section>

            {/* ── Step 2: TARGETING ───────────────────────────────────────── */}
            <section
              hidden={currentStep !== 2}
              className="rule-phase"
              aria-labelledby={`${id}-targeting-title`}
            >
              <h2
                className="rule-phase-title"
                id={`${id}-targeting-title`}
                ref={targetingHeadingRef}
                tabIndex={-1}
              >
                <span className="rule-phase-num">2</span> Targeting
              </h2>

              <div className="form-field">
                <label>Merge lookup scope</label>
                <span className="field-hint">
                  On (default): scope merge lookups to a single target group so a same-name channel in
                  another group is not merged into.
                </span>
                <details className="modal-why">
                  <summary>Why / when to use</summary>
                  <p className="rule-why-body">
                    On (default): when this rule merges into an existing channel, only look within a single
                    target group — so the rule creates a new channel in that group instead of merging into a
                    same-name channel in another group. Pick the group below, or leave it on Auto to use the
                    Create Channel action&apos;s target group. Off: search all channel groups for a matching
                    name (the original behavior).
                  </p>
                </details>
                <div className="checkbox-group">
                  <label className="checkbox-item">
                    <input
                      type="checkbox"
                      checked={matchScopeTargetGroup}
                      onChange={e => setMatchScopeTargetGroup(e.target.checked)}
                      disabled={isLoading}
                      aria-label="Scope merge lookups to a target group"
                    />
                    <span>Scope merge lookups to a target group</span>
                  </label>
                </div>
                {matchScopeTargetGroup && (
                  <div className="form-field" style={{ marginTop: '8px' }}>
                    <label>Scope group</label>
                    <CustomSelect
                      value={matchScopeGroupId != null ? matchScopeGroupId.toString() : ''}
                      onChange={val => setMatchScopeGroupId(val ? parseInt(val, 10) : null)}
                      options={[
                        { value: '', label: 'Auto — use the Create Channel action\'s target group' },
                        ...availableChannelGroups.map(group => ({
                          value: group.id.toString(),
                          label: group.name,
                        })),
                      ]}
                      disabled={isLoading}
                      searchable
                      searchPlaceholder="Search groups..."
                    />
                    {matchScopeGroupId == null && !ruleResolvesAScopeGroup && (
                      <span className="norm-hint">
                        <span className="material-icons norm-hint-icon">warning</span>
                        No scope group will resolve — this rule has no Create Channel target group, so merge
                        lookups will fall back to ALL channel groups. Pick a Scope group above to restrict
                        matching to one group.
                      </span>
                    )}
                  </div>
                )}
              </div>

              <div className="form-field">
                <label>Manual channel protection</label>
                <span className="field-hint">
                  Off (default): hand-built channels are protected — this rule will not merge streams into them.
                </span>
                <details className="modal-why">
                  <summary>Why / when to use</summary>
                  <p className="rule-why-body">
                    Off (default): channels you built by hand are protected — if this rule&apos;s merge target
                    matches a manual channel, the match is treated as not found (the blocked merge is recorded
                    in the execution log and journal) and the rule may create a new auto channel instead of
                    merging. On: this rule may merge streams into manual channels; each adoption is recorded in
                    the journal.
                  </p>
                </details>
                <div className="checkbox-group">
                  <label className="checkbox-item">
                    <input
                      type="checkbox"
                      checked={allowManualChannelMerge}
                      onChange={e => setAllowManualChannelMerge(e.target.checked)}
                      disabled={isLoading}
                      aria-label="Allow merging into manual channels"
                    />
                    <span>Allow merging into manual channels</span>
                  </label>
                </div>
              </div>

              <div className="form-field">
                <label>Spacing &amp; case in merge matching</label>
                <span className="field-hint">
                  Off (default): names that differ in spacing stay separate channels
                  (&quot;Eurosport 2&quot; vs &quot;Eurosport2&quot;).
                </span>
                <details className="modal-why">
                  <summary>Why / when to use</summary>
                  <p className="rule-why-body">
                    On: when this rule merges into existing channels (Create Channel with
                    &quot;If exists: merge&quot;), names are compared ignoring case and ALL spacing —
                    &quot;eurosport 2&quot;, &quot;Eurosport 2&quot; and &quot;Eurosport2&quot; become one channel
                    instead of several. This only changes how names are COMPARED; the visible channel
                    name keeps its original spelling and is never rewritten. Caveat: genuinely distinct
                    channels whose names differ only by spacing or case would also be merged — including
                    digits (&quot;Canal 5 2&quot; matches &quot;Canal 52&quot;) — leave
                    this off if your provider uses spacing to distinguish real channels.
                  </p>
                </details>
                <div className="checkbox-group">
                  <label className="checkbox-item">
                    <input
                      type="checkbox"
                      checked={foldMatchKey}
                      onChange={e => setFoldMatchKey(e.target.checked)}
                      disabled={isLoading}
                      aria-label="Ignore spacing and case differences when matching"
                    />
                    <span>Ignore spacing &amp; case differences when matching</span>
                  </label>
                </div>
              </div>

              <div className="form-field">
                <label>Normalization Groups</label>
                <span className="field-hint">Select which normalization rule groups to apply to channel names</span>
                {availableNormGroups.length === 0 ? (
                  <span className="norm-hint">
                    <span className="material-icons norm-hint-icon">info</span>
                    No normalization rule groups configured
                  </span>
                ) : (
                  <>
                    <div className="norm-group-actions">
                      <button type="button" className="text-button" disabled={isLoading}
                        onClick={() => setNormalizationGroupIds(availableNormGroups.filter(g => g.enabled).map(g => g.id))}>
                        Select all enabled
                      </button>
                      <button type="button" className="text-button" disabled={isLoading}
                        onClick={() => setNormalizationGroupIds([])}>
                        Clear all
                      </button>
                    </div>
                    <GroupMultiSelectDropdown
                      options={availableNormGroups.map(g => ({ id: g.id, name: g.name, inactive: !g.enabled }))}
                      selectedIds={normalizationGroupIds}
                      onChange={setNormalizationGroupIds}
                      label="Normalization Groups"
                      placeholder="No groups selected"
                      disabled={isLoading}
                      itemLabelSingular="group"
                      itemLabelPlural="groups"
                    />
                    {normalizationGroupIds.length === 0 && availableNormGroups.some(g => g.enabled) && (
                      <span className="norm-hint">
                        <span className="material-icons norm-hint-icon">info</span>
                        No normalization groups selected — names won't be normalized
                      </span>
                    )}
                  </>
                )}
              </div>
            </section>

            {/* ── Step 3: OUTPUT & RUN ────────────────────────────────────── */}
            <section
              hidden={currentStep !== 3}
              className="rule-phase"
              aria-labelledby={`${id}-output-title`}
            >
              <h2
                className="rule-phase-title"
                id={`${id}-output-title`}
                ref={outputHeadingRef}
                tabIndex={-1}
              >
                <span className="rule-phase-num">3</span> Output &amp; Run
              </h2>

              <div className="form-field">
                <label>Channel Sort</label>
                <span className="field-hint">Controls the order streams are processed; renumbers channels only when Create Channel uses a fixed number range</span>
                <div className="sort-config-row">
                  <CustomSelect
                    options={[
                      { value: '', label: 'No sorting (keep manual numbers)' },
                      { value: 'stream_name', label: 'Stream Name' },
                      { value: 'stream_name_natural', label: 'Stream Name (Natural)' },
                      { value: 'group_name', label: 'Group Name' },
                      { value: 'quality', label: 'Quality (Resolution)' },
                      { value: 'stream_name_regex', label: 'Stream Name (Regex)' },
                      { value: 'provider_order', label: 'Provider Order (M3U)' },
                      { value: 'channel_number', label: 'Channel Number' },
                    ]}
                    value={sortField}
                    onChange={setSortField}
                    placeholder="No sorting"
                  />
                  {sortField && (
                    <CustomSelect
                      options={[
                        { value: 'asc', label: 'Ascending' },
                        { value: 'desc', label: 'Descending' },
                      ]}
                      value={sortOrder}
                      onChange={(val) => setSortOrder(val as 'asc' | 'desc')}
                    />
                  )}
                </div>
                {sortField === 'stream_name_regex' && (
                  <div className="form-field" style={{ marginTop: '8px' }}>
                    <label>Sort Regex Pattern</label>
                    <input
                      type="text"
                      className="action-input"
                      value={sortRegex}
                      onChange={e => setSortRegex(e.target.value)}
                      placeholder="(\d{4}-\d{2}-\d{2})"
                      disabled={isLoading}
                    />
                    <p className="form-hint">
                      Enter a regex with a capture group. Streams are sorted by the first captured group.
                      Example: (\d{"{4}"}-\d{"{2}"}-\d{"{2}"}) captures dates like 2024-03-09
                    </p>
                  </div>
                )}
                {sortField === 'quality' && (
                  <div className="checkbox-group">
                    <label className="checkbox-option">
                      <input
                        type="checkbox"
                        checked={probeOnSort}
                        onChange={e => setProbeOnSort(e.target.checked)}
                        disabled={isLoading}
                        aria-label="Probe unprobed streams before sorting"
                      />
                      <span>Probe unprobed streams before sorting</span>
                    </label>
                    <p className="form-hint">
                      Gathers resolution data for streams that haven't been probed. Adds time to execution.
                    </p>
                  </div>
                )}
                {showSortNumberingHint && (
                  <span className="norm-hint" data-testid="sort-numbering-hint">
                    <span className="material-icons norm-hint-icon">info</span>
                    Sorting here controls stream processing order, not channel numbers — Channel Number is
                    Auto on a Create Channel action, so this won&apos;t renumber channels. Add a Sort Group
                    action, or set a number range on Create Channel, to actually reorder the numbers.
                  </span>
                )}
              </div>

              <div className="form-field">
                <label>Stream Sort</label>
                <span className="field-hint">Reorders streams within each channel (e.g. best quality first)</span>
                <div className="sort-config-row">
                  <CustomSelect
                    options={[
                      { value: 'smart_sort', label: 'Smart Sort (default)' },
                      { value: '', label: 'No sorting' },
                      { value: 'quality', label: 'Quality (Resolution)' },
                      { value: 'stream_name', label: 'Stream Name' },
                      { value: 'stream_name_natural', label: 'Stream Name (Natural)' },
                      { value: 'provider_order', label: 'Provider Order (M3U)' },
                    ]}
                    value={streamSortField}
                    onChange={setStreamSortField}
                    placeholder="No sorting"
                  />
                  {streamSortField && streamSortField !== 'smart_sort' && (
                    <CustomSelect
                      options={[
                        { value: 'asc', label: 'Ascending' },
                        { value: 'desc', label: 'Descending' },
                      ]}
                      value={streamSortOrder}
                      onChange={(val) => setStreamSortOrder(val as 'asc' | 'desc')}
                    />
                  )}
                </div>
                {streamSortField === 'quality' && (
                  <>
                    <div className="checkbox-group">
                      <label className="checkbox-option">
                        <input
                          type="checkbox"
                          checked={qualityM3uTieBreakEnabled}
                          onChange={e => setQualityM3uTieBreakEnabled(e.target.checked)}
                          disabled={isLoading}
                          aria-label="Break resolution ties using M3U priority"
                        />
                        <span>Break resolution ties using M3U priority</span>
                      </label>
                      <details className="modal-why">
                        <summary>Why / when to use</summary>
                        <p className="rule-why-body">
                          Off: equal-resolution streams stay in numeric stream-id order only. On: uses Save
                          Priorities below.
                        </p>
                      </details>
                    </div>
                    <div className="form-field" style={{ marginTop: '8px' }}>
                      <label>Equal resolution — tie-break direction</label>
                      <div className="sort-config-row">
                        <CustomSelect
                          options={[
                            { value: 'desc', label: 'Higher priority first' },
                            { value: 'asc', label: 'Lower priority first' },
                          ]}
                          value={qualityTieBreakOrder}
                          onChange={(val) => setQualityTieBreakOrder(val as 'asc' | 'desc')}
                          disabled={isLoading || !qualityM3uTieBreakEnabled}
                        />
                      </div>
                      <details className="modal-why">
                        <summary>Why / when to use</summary>
                        <p className="rule-why-body">
                          When two streams match resolution and tie-break is on, order by ECM M3U priorities
                          (Save Priorities). Same meaning as Provider Order stream sort.
                        </p>
                      </details>
                    </div>
                    <div className="checkbox-group">
                      <label className="checkbox-option">
                        <input
                          type="checkbox"
                          checked={probeOnSort}
                          onChange={e => setProbeOnSort(e.target.checked)}
                          disabled={isLoading}
                          aria-label="Probe unprobed streams before sorting"
                        />
                        <span>Probe unprobed streams before sorting</span>
                      </label>
                      <p className="form-hint">
                        Gathers resolution data for streams that haven't been probed. Adds time to execution.
                      </p>
                    </div>
                  </>
                )}
                {streamSortField === 'provider_order' && (
                  <p className="form-hint">
                    Uses priority values from M3U Manager (Save Priorities). Choose Descending so the highest priority number is first; Ascending puts the lowest priority first.
                  </p>
                )}
              </div>

              <div className="form-field">
                <label>Orphan Cleanup</label>
                <span className="field-hint">What to do with channels that no longer match this rule</span>
                <CustomSelect
                  options={[
                    { value: 'delete', label: 'Delete orphaned channels' },
                    { value: 'move_uncategorized', label: 'Move to Uncategorized' },
                    { value: 'delete_and_cleanup_groups', label: 'Delete channels + empty groups' },
                    { value: 'none', label: 'Do nothing (keep orphans)' },
                  ]}
                  value={orphanAction}
                  onChange={(val) => setOrphanAction(val as 'delete' | 'none' | 'move_uncategorized' | 'delete_and_cleanup_groups')}
                />
                {orphansDeleted && (
                  <span className="norm-hint">
                    <span className="material-icons norm-hint-icon">warning</span>
                    {orphanAction === 'delete_and_cleanup_groups'
                      ? 'Channels that no longer match this rule will be deleted, along with any groups left empty.'
                      : 'Channels that no longer match this rule will be deleted. This is the default — pick Move to Uncategorized or Do nothing to keep them.'}
                  </span>
                )}
              </div>

              {/* Run behavior — the former "Options" grab-bag, dissolved into
                  the purpose group it belongs to. */}
              <div className="form-field">
                <label>Run behavior</label>
                <span className="field-hint">Controls when and how this rule runs</span>
                <div className="checkbox-group horizontal">
                  <label className="checkbox-item">
                    <input
                      type="checkbox"
                      checked={runOnRefresh}
                      onChange={e => setRunOnRefresh(e.target.checked)}
                      disabled={isLoading}
                      aria-label="Run on M3U refresh"
                    />
                    <span>Run on M3U refresh</span>
                  </label>
                  <label className="checkbox-item">
                    <input
                      type="checkbox"
                      checked={stopOnFirstMatch}
                      onChange={e => setStopOnFirstMatch(e.target.checked)}
                      disabled={isLoading}
                      aria-label="Stop on first match"
                    />
                    <span>Stop on first match</span>
                  </label>
                  <label className="checkbox-item">
                    <input
                      type="checkbox"
                      checked={skipStruckStreams}
                      onChange={e => setSkipStruckStreams(e.target.checked)}
                      disabled={isLoading}
                      aria-label="Skip struck-out streams"
                    />
                    <span>Skip struck-out streams</span>
                  </label>
                </div>
              </div>
            </section>
          </div>

          {/* Persistent rail (bead m1s38.1 primitives): intent sentence, save
              hint, and advisory analyzer chips. Advisory ONLY — nothing here
              gates Save. Drops below the main column under ~820px. */}
          <aside className="modal-rail" aria-label="Rule intent and advisories">
            <div className="modal-intent" data-testid="rule-intent-card">
              <h3 className="modal-intent-title">What this rule will do</h3>
              <p className="modal-intent-text" data-testid="rule-intent">{intent}</p>
            </div>

            {saveHint && (
              <div className="rule-save-hint" role="status" data-testid="rule-save-hint">
                <span className="material-icons" aria-hidden="true">info</span>
                <span>{saveHint}</span>
              </div>
            )}

            <div className="rule-analyzer" data-testid="rule-analyzer" aria-live="polite">
              {analyzerFindings.length > 0 && (
                <>
                  <h3 className="modal-intent-title">Advisories</h3>
                  <ul className="rule-analyzer-chips">
                    {analyzerFindings.map((finding, index) => (
                      <li
                        key={`${finding.code}-${index}`}
                        className={`badge badge-sm ${severityBadge(finding.severity)} rule-analyzer-chip`}
                        title={finding.suggestion || finding.message}
                        data-testid="rule-analyzer-chip"
                      >
                        {finding.message}
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </div>
          </aside>
        </div>
      </div>

      {/* Footer (bead 09x38.10): Cancel (dirty-guarded) · Back (hidden on step
          1) · Save · Next (Next becomes the primary Save on the last step).
          Back/Next NEVER route through the dirty guard — only Cancel/Escape/×
          do. Save is exposed on EVERY step (not last-step-only as in the Event
          Sync wizard): the Standard rule has no Preview/write gate to reach
          first, and its step-2/3 fields all carry safe defaults, so a valid
          rule can be saved from any step. This is exactly the Event Sync
          editor's editing-an-existing-rule footer, applied uniformly. */}
      <div className="rule-builder-footer">
        <button
          type="button"
          className="btn btn-secondary"
          onClick={attemptClose}
          disabled={saving}
        >
          Cancel
        </button>
        <div className="rule-builder-footer-nav">
          {currentStep > 1 && (
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => goToStep((currentStep - 1) as WizardStep)}
              disabled={saving}
              data-testid="rule-wizard-back"
            >
              Back
            </button>
          )}
          {currentStep < LAST_STEP && (
            <button
              type="button"
              className="btn btn-secondary"
              onClick={handleSave}
              disabled={saving || isLoading}
              data-testid="rule-wizard-save-early"
            >
              {saving ? 'Saving...' : 'Save'}
            </button>
          )}
          {currentStep < LAST_STEP ? (
            <button
              type="button"
              className="btn btn-primary"
              onClick={() => goToStep((currentStep + 1) as WizardStep)}
              disabled={saving}
              data-testid="rule-wizard-next"
            >
              Next
            </button>
          ) : (
            <button
              type="button"
              className="btn btn-primary"
              onClick={handleSave}
              disabled={saving || isLoading}
            >
              {saving ? 'Saving...' : 'Save'}
            </button>
          )}
        </div>
      </div>

      {/* Discard confirmation (bead m1s38.3): role=alertdialog. Nested in a
          ModalOverlay so Escape dismisses THIS prompt (topmost) rather than the
          whole builder. */}
      {showCancelConfirm && (
        <ModalOverlay
          onClose={() => setShowCancelConfirm(false)}
          data-testid="rule-discard-dialog"
        >
          <div className="modal-container modal-sm" role="alertdialog" aria-modal="true">
            <div className="modal-header">
              <h2>Unsaved Changes</h2>
            </div>
            <div className="modal-body">
              <p style={{ margin: 0, color: 'var(--text-secondary)', fontSize: '14px', lineHeight: 1.5 }}>You have unsaved changes. Are you sure you want to discard them?</p>
            </div>
            <div className="modal-footer">
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => setShowCancelConfirm(false)}
                data-testid="rule-discard-keep"
              >
                Keep Editing
              </button>
              <button
                type="button"
                className="btn btn-danger"
                onClick={() => {
                  setShowCancelConfirm(false);
                  onCancel();
                }}
                data-testid="rule-discard-confirm"
              >
                Discard
              </button>
            </div>
          </div>
        </ModalOverlay>
      )}
    </div>
  );
}

// Helper function to check if a condition type needs a value
function needsValue(type: ConditionType): boolean {
  const noValueTypes: ConditionType[] = ['always', 'never', 'tvg_id_exists', 'logo_exists', 'has_channel', 'channel_has_streams', 'has_audio_tracks', 'normalized_name_exists', 'normalized_name_not_exists'];
  return !noValueTypes.includes(type);
}
