/**
 * Component for editing individual actions in channel pipeline rules.
 */
import { useState, useId, useEffect } from 'react';
import type { Action, ActionType, IfExistsBehavior } from '../../types/channelPipeline';
import { getChannelGroups, getEPGSources, getStreamProfiles, getChannelProfiles } from '../../services/api';
import type { EPGSource, StreamProfile, ChannelProfile } from '../../types';
import { CustomSelect } from '../CustomSelect';
import { GroupMultiSelectDropdown } from '../GroupMultiSelectDropdown';
import {
  parseWholeChannelNumberInput,
  wholeChannelNumberInputError,
} from '../../utils/channelNumber';
import { useReportedFieldError } from './actionFieldValidity';
import './ActionEditor.css';

interface ChannelGroup {
  id: number;
  name: string;
}

// Template variables available for use
const TEMPLATE_VARIABLES = [
  { name: '{stream_name}', description: 'Original stream name', example: 'ESPN HD' },
  { name: '{stream_group}', description: 'Stream group name', example: 'Sports' },
  { name: '{tvg_id}', description: 'TVG-ID if present', example: 'ESPN.us' },
  { name: '{tvg_name}', description: 'TVG name if present', example: 'ESPN' },
  { name: '{quality}', description: 'Quality string', example: '1080p' },
  { name: '{quality_raw}', description: 'Raw quality number', example: '1080' },
  { name: '{provider}', description: 'M3U provider name', example: 'Provider A' },
  { name: '{provider_id}', description: 'M3U provider ID', example: '1' },
  { name: '{provider_channel_number}', description: 'Channel number supplied by the M3U provider', example: '101' },
  { name: '{normalized_name}', description: 'Normalized name', example: 'ESPN' },
];

// Count capture groups in a regex pattern (including named groups),
// mirroring how the backend counts `compiled.groups`. Appending an empty
// alternative makes the regex match the empty string, so exec('') always
// succeeds and the match-array length reveals the group count.
// Returns null when the pattern is not valid JS regex.
function countCaptureGroups(pattern: string): number | null {
  try {
    const match = new RegExp(pattern + '|').exec('');
    return match ? match.length - 1 : null;
  } catch {
    return null;
  }
}

// Backend parity check for name-transform replacements: the pipeline
// converts $N to Python \N and ERRORS at execution time when N exceeds the
// pattern's capture-group count ($0 and leading-zero refs become octal
// escapes — control characters). JS's .replace() renders out-of-range $N as
// literal text, which used to make the preview look right while the backend
// failed on every matching stream (enhancedchannelmanager-yom3k).
// Returns an error message matching the backend's save-time validation, or
// null when all references are valid (or the pattern itself is invalid —
// that is reported separately).
function getGroupRefError(pattern: string, replacement: string): string | null {
  const groups = countCaptureGroups(pattern);
  if (groups === null) return null;
  for (const match of replacement.matchAll(/\$(\d+)/g)) {
    const digits = match[1];
    if (digits.startsWith('0')) {
      return `Replacement references $${digits}, which is not a valid group reference — groups are numbered from $1`;
    }
    const n = parseInt(digits, 10);
    if (n > groups) {
      return `Replacement references group ${n} but pattern defines ${groups} capture group${groups === 1 ? '' : 's'}`;
    }
  }
  return null;
}

// Parse starting number from backend range format (e.g., "100-99999" -> 100)
function parseStartingNumber(spec: string | number | undefined): number | null {
  if (spec === undefined || spec === null) return null;
  const s = String(spec);
  const match = s.match(/^(\d+)-\d+$/);
  if (match) return parseInt(match[1], 10);
  return null;
}

/**
 * The whole number a starting-number entry resolves to, or `null` when the
 * entry is empty or carries something the action cannot honour (beads
 * `enhancedchannelmanager-ay3iq`, `enhancedchannelmanager-j3pyx`).
 *
 * Both starting-number fields in this editor seed a SEQUENTIAL run of whole
 * numbers, so both are held to the narrower renumber-start rule rather than to
 * the canonical channel-number contract, which admits a tenth:
 *
 *   - Create Channel's "Starting from..." writes the entry into a `min-max`
 *     range, and the executor reads a range as two WHOLE numbers.
 *     `validate_channel_number_spec` in `backend/channel_pipeline_schema.py`
 *     refuses a range naming a tenth for exactly that reason.
 *   - Sort Group's "Starting Channel Number" renumbers a group by adding one at
 *     a time from the start.
 *
 * Both used to read their field with `parseInt`, so a typed `1.5` became `1`
 * with nothing saying the value had changed. For Create Channel that also meant
 * the backend's fractional-range refusal could never be reached from the UI,
 * because the truncation ran first and handed the backend a whole range.
 *
 * The entry is REFUSED rather than altered: an entry this returns `null` for
 * stores no start at all, so the action can never carry a number the operator
 * did not type. `wholeChannelNumberInputError` supplies the sentence shown
 * under the field, and it is the same sentence every renumber-start field in
 * the app uses.
 */
function startingNumberValue(text: string): number | null {
  const parsed = parseWholeChannelNumberInput(text);
  return parsed.ok ? parsed.value : null;
}

// Source fields available for set_variable regex modes
const SOURCE_FIELD_OPTIONS = [
  { value: 'stream_name', label: 'Stream Name' },
  { value: 'stream_group', label: 'Group Title' },
  { value: 'tvg_name', label: 'TVG Name' },
  { value: 'tvg_id', label: 'TVG ID' },
  { value: 'quality', label: 'Quality' },
  { value: 'provider', label: 'Provider' },
];

const VARIABLE_MODE_OPTIONS = [
  { value: 'regex_extract', label: 'Regex Extract' },
  { value: 'regex_replace', label: 'Regex Replace' },
  { value: 'literal', label: 'Literal / Template' },
];

// Action type definitions with metadata
const ACTION_TYPES: {
  type: ActionType;
  label: string;
  description: string;
  category: 'creation' | 'assignment' | 'management' | 'variables' | 'control';
  hasNameTemplate?: boolean;
  hasIfExists?: boolean;
  hasTarget?: boolean;
  hasValue?: boolean;
  hasMessage?: boolean;
  hasEpgId?: boolean;
  hasChannelNumbering?: boolean;
  hasNameTransform?: boolean;
  hasVariableConfig?: boolean;
  hasPriority?: boolean;
  hasProfileId?: boolean;
  hasChannelProfileId?: boolean;
  hasProfileRemovalTarget?: boolean;
  hasSortGroupConfig?: boolean;
}[] = [
  // Creation actions
  { type: 'create_channel', label: 'Create Channel', description: 'Create a new channel for the stream', category: 'creation', hasNameTemplate: true, hasIfExists: true, hasChannelNumbering: true, hasNameTransform: true },
  { type: 'create_group', label: 'Create Group', description: 'Create a new channel group', category: 'creation', hasNameTemplate: true, hasIfExists: true, hasNameTransform: true },
  { type: 'merge_streams', label: 'Merge Streams', description: 'Merge stream into existing channel', category: 'creation', hasTarget: true },
  // Assignment actions
  { type: 'assign_logo', label: 'Assign Logo', description: 'Assign a logo to the channel', category: 'assignment', hasValue: true },
  { type: 'assign_tvg_id', label: 'Assign TVG-ID', description: 'Set the TVG-ID for the channel', category: 'assignment', hasValue: true },
  { type: 'assign_epg', label: 'Assign EPG', description: 'Assign EPG data source', category: 'assignment', hasEpgId: true },
  { type: 'assign_profile', label: 'Assign Profile', description: 'Assign a stream profile', category: 'assignment', hasProfileId: true },
  { type: 'assign_channel_profile', label: 'Set Channel Profile', description: 'Enable the selected channel profiles and remove the channel from all others (exclusive membership)', category: 'assignment', hasChannelProfileId: true },
  { type: 'unassign_profile', label: 'Remove Stream Profile', description: 'Remove a selected stream profile or explicitly clear any assigned profile', category: 'assignment', hasProfileId: true, hasProfileRemovalTarget: true },
  { type: 'unassign_channel_profile', label: 'Remove Channel Profile', description: 'Remove the channel from selected profiles or explicitly from all profiles', category: 'assignment', hasChannelProfileId: true, hasProfileRemovalTarget: true },
  { type: 'set_channel_number', label: 'Set Channel Number', description: 'Set the channel number', category: 'assignment', hasValue: true },
  // Variables
  { type: 'set_variable', label: 'Set Variable', description: 'Define a reusable variable from stream data', category: 'variables', hasVariableConfig: true },
  // Management actions
  { type: 'remove_from_channel', label: 'Remove From Channel', description: 'Remove this stream from its current channel', category: 'management' },
  { type: 'set_stream_priority', label: 'Set Stream Priority', description: 'Move stream to lowest or highest priority in its channel', category: 'management', hasPriority: true },
  { type: 'probe_streams', label: 'Probe Streams', description: 'Queue streams for probing after pipeline completes', category: 'management' },
  { type: 'sort_group', label: 'Sort Group', description: "Alphabetically sort and renumber a group's channels (runs once per group after processing)", category: 'management', hasSortGroupConfig: true },
  // Control actions
  { type: 'skip', label: 'Skip', description: 'Skip this stream (do not process)', category: 'control' },
  { type: 'stop_processing', label: 'Stop Processing', description: 'Stop processing further rules', category: 'control' },
  { type: 'log_match', label: 'Log Match', description: 'Log when stream matches', category: 'control', hasMessage: true },
];

const ACTION_CATEGORIES = [
  { id: 'creation', label: 'Creation' },
  { id: 'assignment', label: 'Assignment' },
  { id: 'variables', label: 'Variables' },
  { id: 'management', label: 'Management' },
  { id: 'control', label: 'Control' },
] as const;

const IF_EXISTS_OPTIONS: { value: IfExistsBehavior; label: string }[] = [
  { value: 'skip', label: 'Skip' },
  { value: 'merge', label: 'Merge (create if new)' },
  { value: 'merge_only', label: 'Merge Only (existing only)' },
  { value: 'update', label: 'Update' },
  { value: 'use_existing', label: 'Use Existing' },
];

const TARGET_OPTIONS = [
  { value: 'auto', label: 'Auto-detect' },
  { value: 'existing_channel', label: 'Existing Channel' },
  { value: 'new_channel', label: 'New Channel' },
] as const;

const FIND_BY_OPTIONS = [
  { value: 'name_exact', label: 'Exact Name' },
  { value: 'name_regex', label: 'Regex Pattern' },
  { value: 'tvg_id', label: 'TVG-ID' },
] as const;

// Reorder control with position number + up/down arrows
function OrderNumberInput({ orderNumber, totalItems, onReorder }: {
  orderNumber: number;
  totalItems: number;
  onReorder?: (newPosition: number) => void;
}) {
  const [localValue, setLocalValue] = useState(String(orderNumber));

  // Sync when orderNumber prop changes (e.g. after a reorder)
  useEffect(() => {
    setLocalValue(String(orderNumber));
  }, [orderNumber]);

  const commit = (val: string) => {
    const num = parseInt(val, 10);
    if (!isNaN(num) && num >= 1 && num <= totalItems && num !== orderNumber && onReorder) {
      onReorder(num);
    } else {
      setLocalValue(String(orderNumber));
    }
  };

  return (
    <div className="reorder-controls" data-testid="reorder-controls">
      <button
        type="button"
        className="reorder-btn"
        onClick={() => onReorder?.(orderNumber - 1)}
        disabled={orderNumber <= 1}
        aria-label="Move up"
        title="Move up"
      >
        <span className="material-icons">keyboard_arrow_up</span>
      </button>
      <input
        type="text"
        inputMode="numeric"
        className="order-number-input"
        value={localValue}
        onChange={e => setLocalValue(e.target.value)}
        onBlur={() => commit(localValue)}
        onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); commit(localValue); } }}
        aria-label={`Order ${orderNumber} of ${totalItems}`}
        data-testid="order-number"
      />
      <button
        type="button"
        className="reorder-btn"
        onClick={() => onReorder?.(orderNumber + 1)}
        disabled={orderNumber >= totalItems}
        aria-label="Move down"
        title="Move down"
      >
        <span className="material-icons">keyboard_arrow_down</span>
      </button>
    </div>
  );
}

export interface ActionEditorProps {
  action: Action;
  onChange: (action: Action) => void;
  onRemove: () => void;
  canRemove?: boolean;
  showValidation?: boolean;
  showPreview?: boolean;
  readonly?: boolean;
  draggable?: boolean;
  compact?: boolean;
  previousActions?: Action[];
  orderNumber?: number;
  totalItems?: number;
  onReorder?: (newPosition: number) => void;
}

export function ActionEditor({
  action,
  onChange,
  onRemove,
  canRemove = true,
  showValidation = false,
  showPreview = false,
  readonly = false,
  /* draggable - defined in props but not yet used in the component body */
  compact = false,
  previousActions = [],
  orderNumber,
  totalItems,
  onReorder,
}: ActionEditorProps) {
  const id = useId();
  const [typeSelectOpen, setTypeSelectOpen] = useState(false);
  const [showVariables, setShowVariables] = useState(false);
  const [showVarTemplateVariables, setShowVarTemplateVariables] = useState(false);
  const [channelGroups, setChannelGroups] = useState<ChannelGroup[]>([]);
  const [epgSources, setEpgSources] = useState<EPGSource[]>([]);
  const [streamProfiles, setStreamProfiles] = useState<StreamProfile[]>([]);
  const [channelProfiles, setChannelProfiles] = useState<ChannelProfile[]>([]);
  const [channelProfileDropdownOpen, setChannelProfileDropdownOpen] = useState(false);
  const [channelNumberMode, setChannelNumberMode] = useState<'auto' | 'starting' | 'provider'>(
    action.channel_number === '{provider_channel_number}'
      ? 'provider'
      : parseStartingNumber(action.channel_number) !== null ? 'starting' : 'auto'
  );
  const [nameTransformEnabled, setNameTransformEnabled] = useState(
    !!action.name_transform_pattern
  );
  // The two starting-number entries live here as TEXT rather than being derived
  // from the action, because the action cannot hold an entry the rule refuses:
  // Create Channel stores a `min-max` spec and Sort Group stores a `number`, and
  // a refused entry stores neither. Keeping the text means a refused `1.5` stays
  // on screen next to its message instead of snapping back to a value nobody
  // typed. Derived at mount like `channelNumberMode` above.
  const [channelNumberStartText, setChannelNumberStartText] = useState(() => {
    const start = parseStartingNumber(action.channel_number);
    return start === null ? '' : String(start);
  });
  const [sortGroupStartText, setSortGroupStartText] = useState(() =>
    action.starting_number === undefined || action.starting_number === null
      ? ''
      : String(action.starting_number)
  );
  const channelNumberStartError = wholeChannelNumberInputError(channelNumberStartText);
  const sortGroupStartError = wholeChannelNumberInputError(sortGroupStartText);

  // Fetch channel groups for group selector
  useEffect(() => {
    if (action.type === 'create_channel' || action.type === 'create_group'
        || action.type === 'merge_streams') {
      getChannelGroups().then(groups => {
        setChannelGroups(groups.map(g => ({ id: g.id, name: g.name })));
      }).catch(() => {
        // Ignore errors - groups are optional
      });
    }
  }, [action.type]);

  // Fetch EPG sources when assign_epg or assign_logo action is selected
  useEffect(() => {
    if (action.type === 'assign_epg' || action.type === 'assign_logo') {
      getEPGSources().then(setEpgSources).catch(() => setEpgSources([]));
    }
  }, [action.type]);

  // Fetch stream profiles when assign_profile action is selected
  useEffect(() => {
    if (action.type === 'assign_profile' || action.type === 'unassign_profile') {
      getStreamProfiles().then(setStreamProfiles).catch(() => setStreamProfiles([]));
    }
  }, [action.type]);

  // Fetch channel profiles when assign_channel_profile action is selected
  useEffect(() => {
    if (action.type === 'assign_channel_profile' || action.type === 'unassign_channel_profile') {
      getChannelProfiles().then(setChannelProfiles).catch(() => setChannelProfiles([]));
    }
  }, [action.type]);

  const actionDef = ACTION_TYPES.find(a => a.type === action.type);

  // Report the two starting-number refusals to the enclosing rule form so the
  // save seam can see them (bead `enhancedchannelmanager-ay3iq`). Without this
  // the refusal is invisible one layer up: a refused entry leaves the action
  // carrying NO start, RuleBuilder reads that as valid, and the rule saves with
  // automatic numbering / the group's current lowest while the operator is
  // looking at a red error. See `actionFieldValidity.ts`.
  //
  // Conditioned on the field being RENDERED, not just on the text being
  // refused: switching Channel Numbering back to Auto, or changing the action's
  // type, takes the field off screen, and a refusal with no visible field would
  // block a save the operator has no way to unblock. The text state is
  // deliberately left alone in that case: it is what the field shows again if
  // they switch back.
  useReportedFieldError(
    `${id}-ch-start`,
    actionDef?.hasChannelNumbering && channelNumberMode === 'starting'
      ? channelNumberStartError
      : null,
  );
  useReportedFieldError(
    `${id}-sort-group-start`,
    actionDef?.hasSortGroupConfig ? sortGroupStartError : null,
  );

  // Check for dependency warnings
  const getDependencyWarning = (): string | null => {
    if (['assign_logo', 'assign_tvg_id', 'assign_epg', 'assign_profile', 'assign_channel_profile', 'unassign_profile', 'unassign_channel_profile', 'set_channel_number'].includes(action.type)) {
      const hasChannelCreation = previousActions.some(a =>
        a.type === 'create_channel' || a.type === 'merge_streams'
      );
      if (!hasChannelCreation) {
        return 'This action requires a channel to be created or merged first';
      }
    }
    return null;
  };

  // Validation
  const getValidationError = (): string | null => {
    if (!showValidation) return null;

    if (actionDef?.hasNameTemplate && !action.name_template) {
      return 'Name template is required';
    }

    if (action.name_template) {
      // Check for unknown variables (allow {var:*} references)
      const usedVars = action.name_template.match(/\{[^}]+\}/g) || [];
      const knownVars = TEMPLATE_VARIABLES.map(v => v.name);
      const unknown = usedVars.filter(v => !knownVars.includes(v) && !v.startsWith('{var:'));
      if (unknown.length > 0) {
        return `Unknown variable: ${unknown[0]}`;
      }
    }

    if (action.type === 'create_channel' && !action.group_id) {
      const hasPriorCreateGroup = previousActions.some(a => a.type === 'create_group');
      if (!hasPriorCreateGroup) {
        return 'Target group is required (or add a Create Group action before this action)';
      }
    }

    if (action.type === 'merge_streams' && action.target === 'existing_channel') {
      if (action.find_channel_by && !action.find_channel_value) {
        return 'Find value is required';
      }
    }

    if (action.type === 'sort_group' && action.starting_number !== undefined && action.starting_number < 1) {
      return 'Starting number must be at least 1';
    }

    if (action.type === 'unassign_profile' && (action.target ?? 'selected') === 'selected' && !action.profile_id) {
      return 'Stream profile is required for selected removal';
    }

    if (action.type === 'unassign_channel_profile' && (action.target ?? 'selected') === 'selected'
        && !action.channel_profile_ids?.length) {
      return 'At least one channel profile is required for selected removal';
    }

    // Validate name transform regex
    if (action.name_transform_pattern) {
      try {
        new RegExp(action.name_transform_pattern);
      } catch {
        return 'Invalid transform regex pattern';
      }
      // Backend parity: out-of-range $N references error at execution time
      // (enhancedchannelmanager-yom3k) — flag them before the save attempt.
      const refError = getGroupRefError(
        action.name_transform_pattern,
        action.name_transform_replacement || ''
      );
      if (refError) return refError;
    }

    // Validate set_variable
    if (action.type === 'set_variable') {
      if (!action.variable_name) return 'Variable name is required';
      if (action.variable_mode === 'regex_extract' || action.variable_mode === 'regex_replace') {
        if (!action.pattern) return 'Pattern is required';
        try {
          new RegExp(action.pattern);
        } catch {
          return 'Invalid regex pattern';
        }
      }
      if (action.variable_mode === 'literal' && !action.template) {
        return 'Template is required';
      }
    }

    return null;
  };

  const validationError = getValidationError();
  const dependencyWarning = getDependencyWarning();
  const errorId = `${id}-error`;

  const handleTypeChange = (newType: ActionType) => {
    const newDef = ACTION_TYPES.find(a => a.type === newType);
    const newAction: Action = { type: newType };

    // Initialize defaults based on type
    if (newDef?.hasIfExists) {
      newAction.if_exists = 'skip';
    }
    if (newType === 'merge_streams') {
      newAction.target = 'auto';
    }
    if (newType === 'unassign_profile' || newType === 'unassign_channel_profile') {
      newAction.target = 'selected';
    }
    if (newType === 'set_variable') {
      newAction.variable_mode = 'regex_extract';
      newAction.source_field = 'stream_name';
    }
    if (newType === 'set_stream_priority') {
      newAction.priority = 'lowest';
    }
    if (newType === 'sort_group') {
      // Defaults mirror the manual Sort & Renumber modal
      // (ChannelsPane.tsx) and the backend port
      // (backend/channel_pipeline_sort.py).
      newAction.order = 'asc';
      newAction.strip_numbers = true;
      newAction.ignore_country = false;
    }

    onChange(newAction);
    setTypeSelectOpen(false);
    setNameTransformEnabled(false);
  };

  const handleInsertVariable = (variable: string) => {
    const currentTemplate = action.name_template || '';
    onChange({ ...action, name_template: currentTemplate + variable });
    setShowVariables(false);
  };

  const handleInsertValueVariable = (variable: string) => {
    const currentValue = action.value || '';
    onChange({ ...action, value: currentValue + variable });
    setShowVariables(false);
  };

  // Name-transform error the preview must surface (backend parity —
  // enhancedchannelmanager-yom3k): an out-of-range $N reference errors at
  // execution time on every matching stream, so the preview must flag it
  // instead of rendering the literal $N text JS's .replace() produces.
  const getTransformPreviewError = (): string | null => {
    if (!nameTransformEnabled || !action.name_transform_pattern) return null;
    try {
      new RegExp(action.name_transform_pattern);
    } catch {
      return 'Invalid transform regex pattern';
    }
    return getGroupRefError(
      action.name_transform_pattern,
      action.name_transform_replacement || ''
    );
  };

  // Generate preview text
  const getPreviewText = (): string => {
    if (!action.name_template) return '';
    let preview = action.name_template;
    TEMPLATE_VARIABLES.forEach(v => {
      preview = preview.replace(v.name, v.example);
    });
    // Apply name transform preview. The 'g' flag matches the backend's
    // regex.sub semantics (replace ALL occurrences, not just the first).
    if (nameTransformEnabled && action.name_transform_pattern && !getTransformPreviewError()) {
      try {
        const regex = new RegExp(action.name_transform_pattern, 'g');
        preview = preview.replace(regex, action.name_transform_replacement || '');
      } catch {
        // Invalid regex, show untransformed
      }
    }
    return preview;
  };

  return (
    <div
      className={`action-editor ${compact ? 'compact' : ''} ${validationError ? 'has-error' : ''}`}
      data-testid="action-editor"
    >
      {orderNumber !== undefined && totalItems !== undefined && totalItems > 1 && !readonly && (
        <OrderNumberInput
          orderNumber={orderNumber}
          totalItems={totalItems}
          onReorder={onReorder}
        />
      )}

      <div className="action-content">
        {/* Type Selector */}
        <div className="action-type-wrapper">
          <label htmlFor={`${id}-type`} className="sr-only">Action type</label>
          <div className="action-type-select">
            <button
              id={`${id}-type`}
              type="button"
              className="action-type-button"
              onClick={() => !readonly && setTypeSelectOpen(!typeSelectOpen)}
              disabled={readonly}
              aria-haspopup="listbox"
              aria-expanded={typeSelectOpen}
              role="combobox"
            >
              <span>{actionDef?.label || action.type || 'Select an action...'}</span>
              <span className="material-icons">expand_more</span>
            </button>

            {typeSelectOpen && (
              <div className="action-type-dropdown" role="listbox">
                {ACTION_CATEGORIES.map(category => (
                  <div key={category.id} className="action-category">
                    <div className="action-category-label">{category.label}</div>
                    {ACTION_TYPES
                      .filter(a => a.category === category.id)
                      .map(a => (
                        <button
                          key={a.type}
                          type="button"
                          className={`action-type-option ${a.type === action.type ? 'selected' : ''}`}
                          onClick={() => handleTypeChange(a.type)}
                          role="option"
                          aria-selected={a.type === action.type}
                        >
                          <span className="action-option-label">{a.label}</span>
                          <span className="action-option-desc">{a.description}</span>
                        </button>
                      ))}
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Action Description */}
        {actionDef && (
          <div className="action-description">
            {action.type === 'skip' && (
              <span className="action-hint">Stream will not be processed by this or subsequent rules</span>
            )}
            {action.type === 'stop_processing' && (
              <span className="action-hint">No further rules will be applied to this stream</span>
            )}
            {action.type === 'sort_group' && (
              <span className="action-hint">
                Runs once per group after all streams are processed this run — not per-stream. Matches the manual Sort &amp; Renumber tool&apos;s ordering exactly.
              </span>
            )}
          </div>
        )}

        {/* Name Template Field */}
        {actionDef?.hasNameTemplate && (
          <div className="action-field">
            <label htmlFor={`${id}-template`}>Name Template</label>
            <div className="template-input-wrapper">
              <input
                id={`${id}-template`}
                type="text"
                className="action-input"
                value={action.name_template || ''}
                onChange={e => onChange({ ...action, name_template: e.target.value })}
                placeholder="e.g., {stream_name}"
                disabled={readonly}
                aria-describedby={validationError ? errorId : undefined}
                aria-invalid={!!validationError}
              />
              {!readonly && (
                <button
                  type="button"
                  className="show-variables-btn"
                  onClick={() => setShowVariables(!showVariables)}
                  aria-label="Show variables"
                >
                  <span className="material-icons">code</span>
                </button>
              )}
            </div>

            {showVariables && (
              <div className="variables-dropdown">
                <div className="variables-hint">Template variables - click to insert:</div>
                {TEMPLATE_VARIABLES.map(v => (
                  <button
                    key={v.name}
                    type="button"
                    className="variable-option"
                    onClick={() => handleInsertVariable(v.name)}
                  >
                    <span className="variable-name">{v.name}</span>
                    <span className="variable-desc">{v.description}</span>
                  </button>
                ))}
              </div>
            )}

            {showPreview && action.name_template && (
              <div className="template-preview">
                <span className="preview-label">Preview:</span>
                {(() => {
                  const transformError = getTransformPreviewError();
                  return transformError ? (
                    <span className="preview-text preview-error">{transformError}</span>
                  ) : (
                    <span className="preview-text">{getPreviewText()}</span>
                  );
                })()}
              </div>
            )}
          </div>
        )}

        {/* Target Group Selector for create_channel */}
        {action.type === 'create_channel' && (() => {
          const priorCreateGroups = previousActions.filter(a => a.type === 'create_group');
          const lastCreateGroup = priorCreateGroups.length > 0 ? priorCreateGroups[priorCreateGroups.length - 1] : null;
          const autoLabel = lastCreateGroup
            ? `Auto — from Create Group "${lastCreateGroup.name_template || 'unnamed'}"`
            : 'Select a group...';
          return (
            <div className="action-field">
              <label>Target Group</label>
              <CustomSelect
                value={action.group_id?.toString() || ''}
                onChange={val => onChange({ ...action, group_id: val ? parseInt(val) : undefined })}
                options={[
                  { value: '', label: autoLabel },
                  ...channelGroups.map(group => ({
                    value: group.id.toString(),
                    label: group.name,
                  })),
                ]}
                disabled={readonly}
                searchable
                searchPlaceholder="Search groups..."
              />
              {lastCreateGroup && !action.group_id && (
                <span className="field-hint">Will use the group created by the prior Create Group action</span>
              )}
            </div>
          );
        })()}

        {/* If Exists Selector */}
        {actionDef?.hasIfExists && (
          <div className="action-field">
            <label>If already exists</label>
            <CustomSelect
              value={action.if_exists || 'skip'}
              onChange={val => onChange({ ...action, if_exists: val as IfExistsBehavior })}
              options={IF_EXISTS_OPTIONS.map(opt => ({
                value: opt.value,
                label: opt.label,
              }))}
              disabled={readonly}
            />
          </div>
        )}

        {/* Channel Numbering for create_channel */}
        {actionDef?.hasChannelNumbering && (
          <div className="action-field">
            <label>Channel Numbering</label>
            <CustomSelect
              value={channelNumberMode}
              onChange={val => {
                const mode = val as 'auto' | 'starting' | 'provider';
                setChannelNumberMode(mode);
                if (mode === 'auto') {
                  const { channel_number: _, ...rest } = action;
                  onChange(rest);
                } else if (mode === 'starting') {
                  setChannelNumberStartText('100');
                  onChange({ ...action, channel_number: '100-99999' });
                } else {
                  onChange({ ...action, channel_number: '{provider_channel_number}' });
                }
              }}
              options={[
                { value: 'auto', label: 'Auto (sequential from 1)' },
                { value: 'starting', label: 'Starting from...' },
                { value: 'provider', label: 'Provider Channel Number' },
              ]}
              disabled={readonly}
            />
            {channelNumberMode === 'starting' && (
              <div className="channel-number-start-wrapper">
                <label htmlFor={`${id}-ch-start`} className="sr-only">Starting channel number</label>
                <input
                  id={`${id}-ch-start`}
                  type="number"
                  className="action-input"
                  value={channelNumberStartText}
                  onChange={e => {
                    const text = e.target.value;
                    setChannelNumberStartText(text);
                    // Derive the spec from the entry instead of truncating it.
                    // An entry the rule refuses stores NO spec, so the action
                    // cannot end up carrying `1-99999` for a typed `1.5`, nor
                    // the previous `100-99999`, which the operator has not asked
                    // for either. The field goes red while that is the case.
                    const start = startingNumberValue(text);
                    if (start === null) {
                      const { channel_number: _dropped, ...rest } = action;
                      onChange(rest);
                    } else {
                      onChange({ ...action, channel_number: `${start}-99999` });
                    }
                  }}
                  min={1}
                  placeholder="Starting number"
                  disabled={readonly}
                  aria-label="Starting channel number"
                  aria-invalid={!!channelNumberStartError}
                  aria-describedby={channelNumberStartError ? `${id}-ch-start-error` : undefined}
                />
                {channelNumberStartError && (
                  <span id={`${id}-ch-start-error`} className="field-error" role="alert">
                    {channelNumberStartError}
                  </span>
                )}
                <span className="field-hint">Channels will be numbered starting from this value</span>
              </div>
            )}
          </div>
        )}

        {/* Name Transform Section */}
        {actionDef?.hasNameTransform && !readonly && (
          <div className="name-transform-section">
            <label className="transform-toggle">
              <input
                type="checkbox"
                checked={nameTransformEnabled}
                onChange={e => {
                  const enabled = e.target.checked;
                  setNameTransformEnabled(enabled);
                  if (!enabled) {
                    const { name_transform_pattern: _p, name_transform_replacement: _r, ...rest } = action;
                    onChange(rest);
                  }
                }}
              />
              <span>Apply regex transform to name</span>
            </label>
            {nameTransformEnabled && (
              <div className="transform-inputs">
                <div className="action-field">
                  <label htmlFor={`${id}-transform-pattern`}>Pattern (regex)</label>
                  <input
                    id={`${id}-transform-pattern`}
                    type="text"
                    className="action-input mono"
                    value={action.name_transform_pattern || ''}
                    onChange={e => onChange({ ...action, name_transform_pattern: e.target.value })}
                    placeholder="e.g., ^US:\s*"
                    aria-label="Transform pattern"
                  />
                </div>
                <div className="action-field">
                  <label htmlFor={`${id}-transform-replacement`}>Replacement</label>
                  <input
                    id={`${id}-transform-replacement`}
                    type="text"
                    className="action-input mono"
                    value={action.name_transform_replacement || ''}
                    onChange={e => onChange({ ...action, name_transform_replacement: e.target.value })}
                    placeholder="Leave empty to remove match"
                    aria-label="Transform replacement"
                  />
                  <span className="field-hint">Use $1, $2 for capture group backreferences. Pattern matching is case-sensitive.</span>
                </div>
              </div>
            )}
          </div>
        )}

        {/* Set Variable Config */}
        {actionDef?.hasVariableConfig && (
          <div className="variable-config-section">
            <div className="action-field">
              <label htmlFor={`${id}-var-name`}>Variable Name</label>
              <input
                id={`${id}-var-name`}
                type="text"
                className="action-input mono"
                value={action.variable_name || ''}
                onChange={e => onChange({ ...action, variable_name: e.target.value.replace(/[^a-zA-Z0-9_]/g, '') })}
                placeholder="e.g., region"
                disabled={readonly}
                aria-label="Variable name"
              />
              {action.variable_name && (
                <span className="field-hint">Use as <code>{'{var:' + action.variable_name + '}'}</code> in later actions</span>
              )}
            </div>

            <div className="action-field">
              <label>Mode</label>
              <CustomSelect
                value={action.variable_mode || 'regex_extract'}
                onChange={val => onChange({ ...action, variable_mode: val as 'regex_extract' | 'regex_replace' | 'literal' })}
                options={VARIABLE_MODE_OPTIONS.map(opt => ({
                  value: opt.value,
                  label: opt.label,
                }))}
                disabled={readonly}
              />
            </div>

            {(action.variable_mode === 'regex_extract' || action.variable_mode === 'regex_replace') && (
              <>
                <div className="action-field">
                  <label>Source Field</label>
                  <CustomSelect
                    value={action.source_field || 'stream_name'}
                    onChange={val => onChange({ ...action, source_field: val })}
                    options={SOURCE_FIELD_OPTIONS.map(opt => ({
                      value: opt.value,
                      label: opt.label,
                    }))}
                    disabled={readonly}
                  />
                </div>
                <div className="action-field">
                  <label htmlFor={`${id}-var-pattern`}>Pattern (regex)</label>
                  <input
                    id={`${id}-var-pattern`}
                    type="text"
                    className="action-input mono"
                    value={action.pattern || ''}
                    onChange={e => onChange({ ...action, pattern: e.target.value })}
                    placeholder={action.variable_mode === 'regex_extract' ? 'e.g., ^(\\w+):' : 'e.g., ^US:\\s*'}
                    disabled={readonly}
                    aria-label="Regex pattern"
                  />
                </div>
              </>
            )}

            {action.variable_mode === 'regex_replace' && (
              <div className="action-field">
                <label htmlFor={`${id}-var-replacement`}>Replacement</label>
                <input
                  id={`${id}-var-replacement`}
                  type="text"
                  className="action-input mono"
                  value={action.replacement || ''}
                  onChange={e => onChange({ ...action, replacement: e.target.value })}
                  placeholder="Use $1, $2 for capture groups"
                  disabled={readonly}
                  aria-label="Replacement"
                />
              </div>
            )}

            {action.variable_mode === 'literal' && (
              <div className="action-field">
                <label htmlFor={`${id}-var-template`}>Template</label>
                <div className="template-input-wrapper">
                  <input
                    id={`${id}-var-template`}
                    type="text"
                    className="action-input"
                    value={action.template || ''}
                    onChange={e => onChange({ ...action, template: e.target.value })}
                    placeholder="e.g., Channel {var:region}"
                    disabled={readonly}
                    aria-label="Template value"
                  />
                  {!readonly && (
                    <button
                      type="button"
                      className="show-variables-btn"
                      onClick={() => setShowVarTemplateVariables(!showVarTemplateVariables)}
                      aria-label="Show variables"
                    >
                      <span className="material-icons">code</span>
                    </button>
                  )}
                </div>
                {showVarTemplateVariables && (
                  <div className="variables-dropdown">
                    <div className="variables-hint">Template variables - click to insert:</div>
                    {TEMPLATE_VARIABLES.map(v => (
                      <button
                        key={v.name}
                        type="button"
                        className="variable-option"
                        onClick={() => {
                          onChange({ ...action, template: (action.template || '') + v.name });
                          setShowVarTemplateVariables(false);
                        }}
                      >
                        <span className="variable-name">{v.name}</span>
                        <span className="variable-desc">{v.description}</span>
                      </button>
                    ))}
                  </div>
                )}
                <span className="field-hint">Can use template variables and <code>{'{var:name}'}</code> references</span>
              </div>
            )}
          </div>
        )}

        {/* Target Selector for merge_streams */}
        {action.type === 'merge_streams' && (
          <>
            <div className="action-field">
              <label>Target</label>
              <CustomSelect
                value={action.target || 'auto'}
                onChange={val => {
                  const updated = { ...action, target: val as 'auto' | 'existing_channel' | 'new_channel' };
                  if (val === 'existing_channel' && !action.find_channel_by) {
                    updated.find_channel_by = 'name_exact';
                  }
                  onChange(updated);
                }}
                options={TARGET_OPTIONS.map(opt => ({
                  value: opt.value,
                  label: opt.label,
                }))}
                disabled={readonly}
              />
            </div>

            {action.target === 'existing_channel' && (
              <>
                <div className="action-field">
                  <label>Find channel by</label>
                  <CustomSelect
                    value={action.find_channel_by || 'name_exact'}
                    onChange={val => onChange({ ...action, find_channel_by: val as 'name_exact' | 'name_regex' | 'tvg_id' })}
                    options={FIND_BY_OPTIONS.map(opt => ({
                      value: opt.value,
                      label: opt.label,
                    }))}
                    disabled={readonly}
                  />
                </div>
                <div className="action-field">
                  <label htmlFor={`${id}-find-value`}>Find value</label>
                  <input
                    id={`${id}-find-value`}
                    type="text"
                    className="action-input"
                    value={action.find_channel_value || ''}
                    onChange={e => onChange({ ...action, find_channel_value: e.target.value })}
                    placeholder="Enter search value"
                    disabled={readonly}
                    aria-label="Find value"
                  />
                </div>
              </>
            )}

            {/* Max streams per channel */}
            <div className="action-field">
              <label htmlFor={`${id}-max-streams`}>Max Streams Per Provider</label>
              <input
                id={`${id}-max-streams`}
                type="number"
                className="action-input"
                value={action.max_streams_per_channel ?? ''}
                onChange={e => onChange({
                  ...action,
                  max_streams_per_channel: e.target.value ? parseInt(e.target.value, 10) : undefined
                })}
                placeholder="Unlimited"
                min={1}
                disabled={readonly}
              />
              <span className="field-hint">
                Max streams per provider per channel. Use with quality sorting + probe for best results.
              </span>
            </div>

            <div className="action-field">
              <label className="transform-toggle">
                <input
                  type="checkbox"
                  checked={!!action.remove_non_matching}
                  onChange={e => onChange({ ...action, remove_non_matching: e.target.checked })}
                  disabled={readonly}
                  aria-label="Remove non-matching streams on merge"
                />
                Remove streams that no longer match
              </label>
              <span className="field-hint">
                When enabled, the target channel is kept in sync: after this run, it will keep only the streams that were merged into that channel during this run (removing stale streams that no longer match).
              </span>
            </div>

            <div className="action-field">
              <label className="transform-toggle">
                <input
                  type="checkbox"
                  checked={!!action.loose_name_match}
                  onChange={e => onChange({ ...action, loose_name_match: e.target.checked })}
                  disabled={readonly}
                  aria-label="Loose name matching (legacy fuzzy)"
                />
                Loose name matching (legacy fuzzy)
              </label>
              <span className="field-hint">
                Off (default): a stream merges into an existing channel only when its normalized name exactly matches. On: restores the older fuzzy matching (core-name, parentheses, word-prefix, call-sign) — this can over-match unrelated streams.
              </span>
            </div>

            {/* Target-channel group filter (bd-0emgo.3) */}
            <div className="action-field">
              <label>Exclude target groups</label>
              <span className="field-hint">
                After the merge target channel is resolved, skip the merge if that channel is in any selected group. Keeps merges OUT of these groups. This is a real target-channel guard — the stream-side &ldquo;not in group&rdquo; condition only decides whether the rule fires, not where it merges. Leave empty for no filter.
              </span>
              <GroupMultiSelectDropdown
                options={channelGroups}
                selectedIds={action.target_channel_not_in_group ?? []}
                onChange={next =>
                  onChange({
                    ...action,
                    target_channel_not_in_group: next.length > 0 ? next : undefined,
                  })
                }
                label="Exclude target groups"
                placeholder="No groups excluded"
                emptyMessage="No channel groups available."
                disabled={readonly}
              />
            </div>
          </>
        )}

        {/* Value Field for assignment actions */}
        {actionDef?.hasValue && action.type === 'assign_logo' && (
          <div className="action-field">
            <label>Logo Source</label>
            <div className="logo-source-options">
              <label className="transform-toggle">
                <input
                  type="radio"
                  name={`${id}-logo-source`}
                  checked={action.value === 'from_stream' || !action.value}
                  onChange={() => onChange({ ...action, value: 'from_stream', epg_id: undefined })}
                  disabled={readonly}
                />
                From stream (M3U logo)
              </label>
              <label className="transform-toggle">
                <input
                  type="radio"
                  name={`${id}-logo-source`}
                  checked={action.value === 'from_epg'}
                  onChange={() => onChange({ ...action, value: 'from_epg' })}
                  disabled={readonly}
                />
                From EPG source
              </label>
              <label className="transform-toggle">
                <input
                  type="radio"
                  name={`${id}-logo-source`}
                  checked={action.value !== 'from_stream' && action.value !== 'from_epg' && !!action.value}
                  onChange={() => onChange({ ...action, value: '', epg_id: undefined })}
                  disabled={readonly}
                />
                Custom URL / template
              </label>
            </div>

            {action.value === 'from_epg' && (
              <div className="action-field" style={{ marginTop: '0.5rem' }}>
                <label>EPG Source</label>
                <CustomSelect
                  value={action.epg_id?.toString() ?? ''}
                  onChange={val => {
                    onChange({ ...action, epg_id: val ? parseInt(val, 10) : undefined });
                  }}
                  options={[
                    { value: '', label: 'Select EPG source...' },
                    ...epgSources.map(src => ({
                      value: src.id.toString(),
                      label: src.name,
                    })),
                  ]}
                  disabled={readonly}
                  searchable
                  searchPlaceholder="Search EPG sources..."
                />
                {epgSources.length === 0 && (
                  <span className="field-hint">No EPG sources configured. Add sources in the EPG Manager tab.</span>
                )}
                <span className="field-hint">Matches channel to EPG entry and uses its icon/logo</span>
              </div>
            )}

            {action.value !== 'from_stream' && action.value !== 'from_epg' && action.value !== undefined && (
              <>
                <div className="template-input-wrapper" style={{ marginTop: '0.5rem' }}>
                  <input
                    id={`${id}-value`}
                    type="text"
                    className="action-input"
                    value={action.value || ''}
                    onChange={e => onChange({ ...action, value: e.target.value })}
                    placeholder="https://example.com/logo.png or {template}"
                    disabled={readonly}
                  />
                  {!readonly && (
                    <button
                      type="button"
                      className="show-variables-btn"
                      onClick={() => setShowVariables(!showVariables)}
                      aria-label="Show variables"
                      title="Template variables available"
                    >
                      <span className="material-icons">code</span>
                    </button>
                  )}
                </div>

                {showVariables && (
                  <div className="variables-dropdown">
                    <div className="variables-hint">Template variables - click to insert:</div>
                    {TEMPLATE_VARIABLES.map(v => (
                      <button
                        key={v.name}
                        type="button"
                        className="variable-option"
                        onClick={() => handleInsertValueVariable(v.name)}
                      >
                        <span className="variable-name">{v.name}</span>
                        <span className="variable-desc">{v.description}</span>
                      </button>
                    ))}
                  </div>
                )}

                <span className="field-hint">Template variables allowed</span>
              </>
            )}
          </div>
        )}

        {/* Value Field for other assignment actions (not assign_logo) */}
        {actionDef?.hasValue && action.type !== 'assign_logo' && (
          <div className="action-field">
            <label htmlFor={`${id}-value`}>
              {action.type === 'assign_tvg_id' && 'TVG-ID'}
              {action.type === 'set_channel_number' && 'Channel Number'}
            </label>
            <div className="template-input-wrapper">
              <input
                id={`${id}-value`}
                type="text"
                className="action-input"
                value={action.value || ''}
                onChange={e => onChange({ ...action, value: e.target.value })}
                placeholder={
                  action.type === 'set_channel_number' ? '101 or {auto}'
                    : 'Enter value or template'
                }
                disabled={readonly}
              />
              {!readonly && (
                <button
                  type="button"
                  className="show-variables-btn"
                  onClick={() => setShowVariables(!showVariables)}
                  aria-label="Show variables"
                  title="Template variables available"
                >
                  <span className="material-icons">code</span>
                </button>
              )}
            </div>

            {showVariables && (
              <div className="variables-dropdown">
                <div className="variables-hint">Template variables - click to insert:</div>
                {TEMPLATE_VARIABLES.map(v => (
                  <button
                    key={v.name}
                    type="button"
                    className="variable-option"
                    onClick={() => handleInsertValueVariable(v.name)}
                  >
                    <span className="variable-name">{v.name}</span>
                    <span className="variable-desc">{v.description}</span>
                  </button>
                ))}
              </div>
            )}

            <span className="field-hint">Template variables allowed</span>
          </div>
        )}

        {/* Message Field for log_match */}
        {actionDef?.hasMessage && (
          <div className="action-field">
            <label htmlFor={`${id}-message`}>Message</label>
            <input
              id={`${id}-message`}
              type="text"
              className="action-input"
              value={action.message || ''}
              onChange={e => onChange({ ...action, message: e.target.value })}
              placeholder="Log message, e.g., Matched: {stream_name}"
              disabled={readonly}
              aria-label="Message"
            />
          </div>
        )}

        {/* EPG Source Selector for assign_epg */}
        {actionDef?.hasEpgId && (
          <div className="action-field">
            <label>EPG Source</label>
            <CustomSelect
              value={action.epg_id?.toString() ?? ''}
              onChange={val => {
                onChange({ ...action, epg_id: val ? parseInt(val, 10) : undefined });
              }}
              options={[
                { value: '', label: 'Select EPG source...' },
                ...epgSources.map(src => ({
                  value: src.id.toString(),
                  label: src.name,
                })),
              ]}
              disabled={readonly}
              searchable
              searchPlaceholder="Search EPG sources..."
            />
            {epgSources.length === 0 && (
              <span className="field-hint">No EPG sources configured. Add sources in the EPG Manager tab.</span>
            )}
            <label className="transform-toggle">
              <input
                type="checkbox"
                checked={action.set_tvg_id ?? false}
                onChange={e => onChange({ ...action, set_tvg_id: e.target.checked })}
                disabled={readonly}
              />
              Set TVG-ID from matched EPG entry
            </label>
            <span className="field-hint">Also sets the channel&apos;s tvg_id to the matched EPG data entry&apos;s tvg_id</span>
          </div>
        )}

        {actionDef?.hasProfileRemovalTarget && (
          <div className="action-field">
            <label htmlFor={`${id}-profile-removal-target`}>Removal Target</label>
            <CustomSelect
              id={`${id}-profile-removal-target`}
              value={action.target ?? 'selected'}
              onChange={val => onChange({
                ...action,
                target: val as 'selected' | 'all',
                ...(val === 'all'
                  ? { profile_id: undefined, channel_profile_ids: undefined }
                  : {}),
              })}
              options={[
                { value: 'selected', label: 'Selected profile(s) only' },
                { value: 'all', label: 'All assigned profiles' },
              ]}
              disabled={readonly}
            />
          </div>
        )}

        {/* Stream Profile Selector for assignment or selected removal */}
        {actionDef?.hasProfileId && (!actionDef.hasProfileRemovalTarget || (action.target ?? 'selected') === 'selected') && (
          <div className="action-field">
            <label>Stream Profile</label>
            <CustomSelect
              value={action.profile_id?.toString() ?? ''}
              onChange={val => {
                onChange({ ...action, profile_id: val ? parseInt(val, 10) : undefined });
              }}
              options={[
                { value: '', label: 'Select stream profile...' },
                ...streamProfiles.map(p => ({
                  value: p.id.toString(),
                  label: p.name,
                })),
              ]}
              disabled={readonly}
              searchable
              searchPlaceholder="Search profiles..."
            />
            {streamProfiles.length === 0 && (
              <span className="field-hint">No stream profiles found. Configure profiles in Dispatcharr first.</span>
            )}
          </div>
        )}

        {/* Channel Profile Multi-Select for assignment or selected removal */}
        {actionDef?.hasChannelProfileId && (!actionDef.hasProfileRemovalTarget || (action.target ?? 'selected') === 'selected') && (
          <div className="action-field">
            <label>Channel Profiles</label>
            <div className="multi-select-dropdown">
              <button
                type="button"
                className="dropdown-trigger"
                onClick={() => !readonly && setChannelProfileDropdownOpen(!channelProfileDropdownOpen)}
                disabled={readonly}
              >
                <span className="dropdown-value">
                  {(action.channel_profile_ids?.length ?? 0) === 0
                    ? 'Select channel profiles...'
                    : channelProfiles
                        .filter(p => action.channel_profile_ids?.includes(p.id))
                        .map(p => p.name)
                        .join(', ') || `${action.channel_profile_ids?.length} selected`}
                </span>
                <span className="material-icons">{channelProfileDropdownOpen ? 'expand_less' : 'expand_more'}</span>
              </button>
              {channelProfileDropdownOpen && (
                <div className="dropdown-menu">
                  <div className="dropdown-actions">
                    <button type="button" onClick={() => onChange({ ...action, channel_profile_ids: channelProfiles.map(p => p.id) })}>
                      Select All
                    </button>
                    <button type="button" onClick={() => onChange({ ...action, channel_profile_ids: [] })}>
                      Clear All
                    </button>
                  </div>
                  <div className="dropdown-options">
                    {channelProfiles.map(profile => (
                      <label key={profile.id} className="dropdown-option">
                        <input
                          type="checkbox"
                          checked={action.channel_profile_ids?.includes(profile.id) ?? false}
                          onChange={() => {
                            const current = action.channel_profile_ids ?? [];
                            const updated = current.includes(profile.id)
                              ? current.filter(id => id !== profile.id)
                              : [...current, profile.id];
                            onChange({ ...action, channel_profile_ids: updated });
                          }}
                        />
                        <span>{profile.name}</span>
                      </label>
                    ))}
                    {channelProfiles.length === 0 && (
                      <span className="dropdown-empty">No channel profiles found. Configure profiles in Dispatcharr first.</span>
                    )}
                  </div>
                </div>
              )}
            </div>
            {/* y3m6o.2: assign_channel_profile is EXCLUSIVE (subtractive). Warn
                the operator that unselected profiles are removed — the copy
                previously read as additive ("assign to these profiles"). */}
            {action.type === 'assign_channel_profile' && (
              <span className="field-hint" data-testid="channel-profile-exclusive-hint">
                Exclusive membership: the channel is <strong>enabled</strong> in the selected profiles
                and <strong>removed from all other</strong> channel profiles. Profiles you do not select
                here will have this channel disabled.
              </span>
            )}
          </div>
        )}

        {action.type === 'unassign_profile' && (
          <span className="field-hint" data-testid="stream-profile-removal-hint">
            {(action.target ?? 'selected') === 'all'
              ? 'Clear any assigned stream profile from the channel.'
              : 'Remove the selected stream profile only when it is currently assigned; any other profile is left unchanged.'}
          </span>
        )}

        {action.type === 'unassign_channel_profile' && (
          <span className="field-hint" data-testid="channel-profile-removal-hint">
            {(action.target ?? 'selected') === 'all'
              ? 'Remove the channel from every channel profile. This requires an explicit All target.'
              : 'Remove the channel from only the selected channel profiles; all other memberships are left unchanged.'}
          </span>
        )}

        {/* Sort Group Config for sort_group */}
        {actionDef?.hasSortGroupConfig && (
          <>
            <div className="action-field">
              <label>Order</label>
              <CustomSelect
                value={action.order || 'asc'}
                onChange={val => onChange({ ...action, order: val as 'asc' | 'desc' })}
                options={[
                  { value: 'asc', label: 'A → Z (ascending)' },
                  { value: 'desc', label: 'Z → A (descending)' },
                ]}
                disabled={readonly}
              />
            </div>
            <div className="action-field">
              <label htmlFor={`${id}-sort-group-start`}>Starting Channel Number</label>
              <input
                id={`${id}-sort-group-start`}
                type="number"
                className="action-input"
                value={sortGroupStartText}
                onChange={e => {
                  const text = e.target.value;
                  setSortGroupStartText(text);
                  // Same rule as Create Channel's start: honoured or refused,
                  // never truncated. A refused entry leaves no starting number,
                  // which is the documented blank behaviour (the group's current
                  // lowest), not a number nobody typed.
                  onChange({ ...action, starting_number: startingNumberValue(text) ?? undefined });
                }}
                min={1}
                placeholder="Auto (group's current lowest, or 1)"
                disabled={readonly}
                aria-label="Starting channel number"
                aria-invalid={!!sortGroupStartError}
                aria-describedby={sortGroupStartError ? `${id}-sort-group-start-error` : undefined}
              />
              {sortGroupStartError && (
                <span id={`${id}-sort-group-start-error`} className="field-error" role="alert">
                  {sortGroupStartError}
                </span>
              )}
              <span className="field-hint">
                Leave blank to keep the group&apos;s current lowest channel number (or start at 1 if none is set).
              </span>
            </div>
            <div className="action-field">
              <label className="transform-toggle">
                <input
                  type="checkbox"
                  checked={action.strip_numbers ?? true}
                  onChange={e => onChange({ ...action, strip_numbers: e.target.checked })}
                  disabled={readonly}
                  aria-label="Ignore channel numbers in names when sorting"
                />
                Ignore channel numbers in names when sorting
              </label>
              <label className="transform-toggle">
                <input
                  type="checkbox"
                  checked={!!action.ignore_country}
                  onChange={e => onChange({ ...action, ignore_country: e.target.checked })}
                  disabled={readonly}
                  aria-label="Ignore country prefix when sorting"
                />
                Ignore country prefix when sorting (e.g., &quot;US | &quot;, &quot;UK: &quot;)
              </label>
            </div>
          </>
        )}

        {/* Priority Selector for set_stream_priority */}
        {actionDef?.hasPriority && (
          <div className="action-field">
            <label>Priority Position</label>
            <CustomSelect
              value={action.priority || 'lowest'}
              onChange={val => onChange({ ...action, priority: val as 'lowest' | 'highest' })}
              options={[
                { value: 'lowest', label: 'Lowest (last)' },
                { value: 'highest', label: 'Highest (first)' },
              ]}
              disabled={readonly}
            />
          </div>
        )}
      </div>

      {/* Dependency Warning */}
      {dependencyWarning && !readonly && (
        <div className="action-warning">
          <span className="material-icons">warning</span>
          {dependencyWarning}
        </div>
      )}

      {/* Validation Error */}
      {validationError && (
        <div id={errorId} className="action-error" role="alert">
          {validationError}
        </div>
      )}

      {/* Remove Button */}
      {canRemove && !readonly && (
        <button
          type="button"
          className="action-remove-btn"
          onClick={onRemove}
          aria-label="Remove action"
        >
          <span className="material-icons">close</span>
        </button>
      )}
    </div>
  );
}
