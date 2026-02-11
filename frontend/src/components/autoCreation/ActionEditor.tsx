/**
 * Component for editing individual actions in auto-creation rules.
 */
import { useState, useId, useEffect } from 'react';
import type { Action, ActionType, IfExistsBehavior } from '../../types/autoCreation';
import { getChannelGroups, getEPGSources } from '../../services/api';
import type { EPGSource } from '../../types';
import { CustomSelect } from '../CustomSelect';
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
  { name: '{normalized_name}', description: 'Normalized name', example: 'ESPN' },
];

// Parse starting number from backend range format (e.g., "100-99999" -> 100)
function parseStartingNumber(spec: string | number | undefined): number | null {
  if (spec === undefined || spec === null) return null;
  const s = String(spec);
  const match = s.match(/^(\d+)-\d+$/);
  if (match) return parseInt(match[1], 10);
  return null;
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
  category: 'creation' | 'assignment' | 'control' | 'variables';
  hasNameTemplate?: boolean;
  hasIfExists?: boolean;
  hasTarget?: boolean;
  hasValue?: boolean;
  hasMessage?: boolean;
  hasEpgId?: boolean;
  hasChannelNumbering?: boolean;
  hasNameTransform?: boolean;
  hasVariableConfig?: boolean;
}[] = [
  // Creation actions
  { type: 'create_channel', label: 'Create Channel', description: 'Create a new channel for the stream', category: 'creation', hasNameTemplate: true, hasIfExists: true, hasChannelNumbering: true, hasNameTransform: true },
  { type: 'create_group', label: 'Create Group', description: 'Create a new channel group', category: 'creation', hasNameTemplate: true, hasIfExists: true, hasNameTransform: true },
  { type: 'merge_streams', label: 'Merge Streams', description: 'Merge stream into existing channel', category: 'creation', hasTarget: true },
  // Assignment actions
  { type: 'assign_logo', label: 'Assign Logo', description: 'Assign a logo to the channel', category: 'assignment', hasValue: true },
  { type: 'assign_tvg_id', label: 'Assign TVG-ID', description: 'Set the TVG-ID for the channel', category: 'assignment', hasValue: true },
  { type: 'assign_epg', label: 'Assign EPG', description: 'Assign EPG data source', category: 'assignment', hasEpgId: true },
  { type: 'assign_profile', label: 'Assign Profile', description: 'Assign a stream profile', category: 'assignment' },
  { type: 'set_channel_number', label: 'Set Channel Number', description: 'Set the channel number', category: 'assignment', hasValue: true },
  // Variables
  { type: 'set_variable', label: 'Set Variable', description: 'Define a reusable variable from stream data', category: 'variables', hasVariableConfig: true },
  // Control actions
  { type: 'skip', label: 'Skip', description: 'Skip this stream (do not process)', category: 'control' },
  { type: 'stop_processing', label: 'Stop Processing', description: 'Stop processing further rules', category: 'control' },
  { type: 'log_match', label: 'Log Match', description: 'Log when stream matches', category: 'control', hasMessage: true },
];

const ACTION_CATEGORIES = [
  { id: 'creation', label: 'Creation' },
  { id: 'assignment', label: 'Assignment' },
  { id: 'variables', label: 'Variables' },
  { id: 'control', label: 'Control' },
] as const;

const IF_EXISTS_OPTIONS: { value: IfExistsBehavior; label: string }[] = [
  { value: 'skip', label: 'Skip' },
  { value: 'merge', label: 'Merge' },
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
  draggable = false,
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
  const [channelNumberMode, setChannelNumberMode] = useState<'auto' | 'starting'>(
    parseStartingNumber(action.channel_number) !== null ? 'starting' : 'auto'
  );
  const [nameTransformEnabled, setNameTransformEnabled] = useState(
    !!action.name_transform_pattern
  );

  // Fetch channel groups for group selector
  useEffect(() => {
    if (action.type === 'create_channel' || action.type === 'create_group') {
      getChannelGroups().then(groups => {
        setChannelGroups(groups.map(g => ({ id: g.id, name: g.name })));
      }).catch(() => {
        // Ignore errors - groups are optional
      });
    }
  }, [action.type]);

  // Fetch EPG sources when assign_epg action is selected
  useEffect(() => {
    if (action.type === 'assign_epg') {
      getEPGSources().then(setEpgSources).catch(() => setEpgSources([]));
    }
  }, [action.type]);

  const actionDef = ACTION_TYPES.find(a => a.type === action.type);

  // Check for dependency warnings
  const getDependencyWarning = (): string | null => {
    if (['assign_logo', 'assign_tvg_id', 'assign_epg', 'assign_profile', 'set_channel_number'].includes(action.type)) {
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

    if (action.type === 'merge_streams' && action.target === 'existing_channel') {
      if (action.find_channel_by && !action.find_channel_value) {
        return 'Find value is required';
      }
    }

    // Validate name transform regex
    if (action.name_transform_pattern) {
      try {
        new RegExp(action.name_transform_pattern);
      } catch {
        return 'Invalid transform regex pattern';
      }
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
    if (newType === 'set_variable') {
      newAction.variable_mode = 'regex_extract';
      newAction.source_field = 'stream_name';
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

  // Generate preview text
  const getPreviewText = (): string => {
    if (!action.name_template) return '';
    let preview = action.name_template;
    TEMPLATE_VARIABLES.forEach(v => {
      preview = preview.replace(v.name, v.example);
    });
    // Apply name transform preview
    if (nameTransformEnabled && action.name_transform_pattern) {
      try {
        const regex = new RegExp(action.name_transform_pattern);
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
              <span>{actionDef?.label || action.type}</span>
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
                <span className="preview-text">{getPreviewText()}</span>
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
                const mode = val as 'auto' | 'starting';
                setChannelNumberMode(mode);
                if (mode === 'auto') {
                  const { channel_number: _, ...rest } = action;
                  onChange(rest);
                } else {
                  onChange({ ...action, channel_number: '100-99999' });
                }
              }}
              options={[
                { value: 'auto', label: 'Auto (sequential from 1)' },
                { value: 'starting', label: 'Starting from...' },
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
                  value={parseStartingNumber(action.channel_number) ?? 100}
                  onChange={e => {
                    const start = parseInt(e.target.value, 10);
                    if (!isNaN(start) && start >= 1) {
                      onChange({ ...action, channel_number: `${start}-99999` });
                    }
                  }}
                  min={1}
                  placeholder="Starting number"
                  disabled={readonly}
                  aria-label="Starting channel number"
                />
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
                  <span className="field-hint">Use $1, $2 for capture group backreferences</span>
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
          </>
        )}

        {/* Value Field for assignment actions */}
        {actionDef?.hasValue && (
          <div className="action-field">
            <label htmlFor={`${id}-value`}>
              {action.type === 'assign_logo' && 'Logo URL'}
              {action.type === 'assign_tvg_id' && 'TVG-ID'}
              {action.type === 'set_channel_number' && 'Channel Number'}
            </label>
            <div className="template-input-wrapper">
              <input
                id={`${id}-value`}
                type="text"
                className="action-input"
                value={action.value || action.channel_number || ''}
                onChange={e => {
                  if (action.type === 'set_channel_number') {
                    onChange({ ...action, channel_number: e.target.value });
                  } else {
                    onChange({ ...action, value: e.target.value });
                  }
                }}
                placeholder={
                  action.type === 'assign_logo' ? 'https://example.com/logo.png or {template}'
                    : action.type === 'set_channel_number' ? '101 or {auto}'
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
