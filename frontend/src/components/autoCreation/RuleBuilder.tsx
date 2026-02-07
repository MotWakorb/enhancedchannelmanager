/**
 * Component for building and editing auto-creation rules.
 */
import { useState, useEffect, useId, useCallback } from 'react';
import type { AutoCreationRule, CreateRuleData, Condition, Action, ConditionType, ActionType } from '../../types/autoCreation';
import { ConditionEditor } from './ConditionEditor';
import { ActionEditor } from './ActionEditor';
import { CustomSelect } from '../CustomSelect';
import './RuleBuilder.css';

export interface RuleBuilderProps {
  rule?: Partial<AutoCreationRule>;
  onSave: (data: CreateRuleData) => Promise<void> | void;
  onCancel: () => void;
  isLoading?: boolean;
}

interface ValidationErrors {
  name?: string;
  conditions?: string;
  actions?: string;
}

export function RuleBuilder({
  rule,
  onSave,
  onCancel,
  isLoading = false,
}: RuleBuilderProps) {
  const id = useId();
  const [name, setName] = useState(rule?.name || '');
  const [description, setDescription] = useState(rule?.description || '');
  const [priority, setPriority] = useState(rule?.priority ?? 0);
  const [enabled, setEnabled] = useState(rule?.enabled ?? true);
  const [runOnRefresh, setRunOnRefresh] = useState(rule?.run_on_refresh ?? false);
  const [stopOnFirstMatch, setStopOnFirstMatch] = useState(rule?.stop_on_first_match ?? true);
  const [sortField, setSortField] = useState(rule?.sort_field || '');
  const [sortOrder, setSortOrder] = useState(rule?.sort_order || 'asc');
  const [probeOnSort, setProbeOnSort] = useState(rule?.probe_on_sort ?? false);
  const [normalizeNames, setNormalizeNames] = useState(rule?.normalize_names ?? false);
  const [orphanAction, setOrphanAction] = useState(rule?.orphan_action || 'delete');
  const [conditions, setConditions] = useState<Condition[]>(rule?.conditions || []);
  const [actions, setActions] = useState<Action[]>(rule?.actions || []);

  const [errors, setErrors] = useState<ValidationErrors>({});
  const [saving, setSaving] = useState(false);
  const [isDirty, setIsDirty] = useState(false);
  const [showCancelConfirm, setShowCancelConfirm] = useState(false);
  const [showConditionSelector, setShowConditionSelector] = useState(false);
  const [showActionSelector, setShowActionSelector] = useState(false);

  // Escape key closes the cancel confirm dialog (capture phase to intercept before parent ModalOverlay)
  useEffect(() => {
    if (!showCancelConfirm) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopImmediatePropagation();
        setShowCancelConfirm(false);
      }
    };
    document.addEventListener('keydown', handler, true);
    return () => document.removeEventListener('keydown', handler, true);
  }, [showCancelConfirm]);

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

  // Track if form has been modified
  useEffect(() => {
    const hasChanges =
      name !== (rule?.name || '') ||
      description !== (rule?.description || '') ||
      priority !== (rule?.priority ?? 0) ||
      enabled !== (rule?.enabled ?? true) ||
      conditions.length !== (rule?.conditions?.length || 0) ||
      actions.length !== (rule?.actions?.length || 0);
    setIsDirty(hasChanges);
  }, [name, description, priority, enabled, conditions, actions, rule]);

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
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0 ? null : newErrors;
  }, [name, conditions, actions]);

  const handleSave = async () => {
    const validationErrors = validate();
    if (validationErrors) {
      // Focus first error field
      if (validationErrors.name) {
        document.getElementById(`${id}-name`)?.focus();
      }
      return;
    }

    setSaving(true);
    try {
      await onSave({
        name: name.trim(),
        description: description.trim() || undefined,
        enabled,
        priority,
        conditions,
        actions,
        run_on_refresh: runOnRefresh,
        stop_on_first_match: stopOnFirstMatch,
        sort_field: sortField || null,
        sort_order: sortOrder,
        probe_on_sort: probeOnSort,
        normalize_names: normalizeNames,
        orphan_action: orphanAction,
      });
    } finally {
      setSaving(false);
    }
  };

  const handleCancel = () => {
    if (isDirty) {
      setShowCancelConfirm(true);
    } else {
      onCancel();
    }
  };

  const handleAddCondition = (type: ConditionType) => {
    const newCondition: Condition = { type, connector: 'and' };
    setConditions([...conditions, newCondition]);
    setShowConditionSelector(false);
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

  const handleAddAction = (type: ActionType) => {
    const newAction: Action = { type };
    if (type === 'create_channel' || type === 'create_group') {
      newAction.if_exists = 'skip';
    }
    if (type === 'merge_streams') {
      newAction.target = 'auto';
    }
    if (type === 'set_variable') {
      newAction.variable_mode = 'regex_extract';
      newAction.source_field = 'stream_name';
    }
    setActions([...actions, newAction]);
    setShowActionSelector(false);
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

  return (
    <div className="rule-builder" data-testid="rule-builder" onKeyDown={handleKeyDown}>
      {isLoading && (
        <div className="loading-overlay" data-testid="loading-indicator">
          <div className="loading-spinner"></div>
          <span>Loading...</span>
        </div>
      )}
      <div className="rule-builder-content">
        {/* Basic Info Section */}
        <section className="rule-section">
          <h3 className="section-title">Basic Information</h3>

          <div className="form-field">
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

          <div className="form-field">
            <label htmlFor={`${id}-priority`}>Priority</label>
            <input
              id={`${id}-priority`}
              type="number"
              value={priority}
              onChange={e => setPriority(e.target.valueAsNumber || 0)}
              min={0}
              disabled={isLoading}
              aria-label="Priority"
            />
            <span className="field-hint">Lower values run first</span>
          </div>

          <div className="form-field">
            <label>Options</label>
            <div className="checkbox-group horizontal">
              <label className="checkbox-item">
                <input
                  type="checkbox"
                  checked={enabled}
                  onChange={e => setEnabled(e.target.checked)}
                  disabled={isLoading}
                  aria-label="Enabled"
                />
                <span>Enabled</span>
              </label>
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
                  checked={normalizeNames}
                  onChange={e => setNormalizeNames(e.target.checked)}
                  disabled={isLoading}
                  aria-label="Normalize channel names"
                />
                <span>Normalize names</span>
              </label>
            </div>
          </div>

          <div className="form-field">
            <label>Sort Matched Streams</label>
            <span className="field-hint">Controls the order streams are processed (affects channel numbering)</span>
            <div className="sort-config-row">
              <CustomSelect
                options={[
                  { value: '', label: 'No sorting (default)' },
                  { value: 'stream_name', label: 'Stream Name' },
                  { value: 'stream_name_natural', label: 'Stream Name (Natural)' },
                  { value: 'group_name', label: 'Group Name' },
                  { value: 'quality', label: 'Quality (Resolution)' },
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
                  onChange={setSortOrder}
                />
              )}
            </div>
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
              onChange={setOrphanAction}
            />
          </div>
        </section>

        {/* Conditions Section */}
        <section className="rule-section">
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
              onClick={() => setShowConditionSelector(!showConditionSelector)}
              aria-expanded={showConditionSelector}
              aria-label="Add condition"
            >
              <span className="material-icons">add</span>
              Add Condition
            </button>

            {showConditionSelector && (
              <ConditionTypeSelector
                onSelect={handleAddCondition}
                onClose={() => setShowConditionSelector(false)}
              />
            )}
          </div>
        </section>

        {/* Actions Section */}
        <section className="rule-section">
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
              onClick={() => setShowActionSelector(!showActionSelector)}
              aria-expanded={showActionSelector}
              aria-label="Add action"
            >
              <span className="material-icons">add</span>
              Add Action
            </button>

            {showActionSelector && (
              <ActionTypeSelector
                onSelect={handleAddAction}
                onClose={() => setShowActionSelector(false)}
              />
            )}
          </div>
        </section>
      </div>

      {/* Footer */}
      <div className="rule-builder-footer">
        <button
          type="button"
          className="btn btn-secondary"
          onClick={handleCancel}
          disabled={saving}
        >
          Cancel
        </button>
        <button
          type="button"
          className="btn btn-primary"
          onClick={handleSave}
          disabled={saving || isLoading}
        >
          {saving ? 'Saving...' : 'Save'}
        </button>
      </div>

      {/* Cancel Confirmation Dialog */}
      {showCancelConfirm && (
        <div className="confirm-dialog-overlay">
          <div className="confirm-dialog">
            <h4>Unsaved Changes</h4>
            <p>You have unsaved changes. Are you sure you want to discard them?</p>
            <div className="confirm-dialog-actions">
              <button
                type="button"
                className="btn btn-secondary"
                onClick={() => setShowCancelConfirm(false)}
              >
                Keep Editing
              </button>
              <button
                type="button"
                className="btn btn-danger"
                onClick={onCancel}
              >
                Discard
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// Helper function to check if a condition type needs a value
function needsValue(type: ConditionType): boolean {
  const noValueTypes: ConditionType[] = ['always', 'never', 'tvg_id_exists', 'logo_exists', 'has_channel', 'channel_has_streams', 'has_audio_tracks'];
  return !noValueTypes.includes(type);
}

// Condition Type Selector Component
function ConditionTypeSelector({
  onSelect,
  onClose,
}: {
  onSelect: (type: ConditionType) => void;
  onClose: () => void;
}) {
  const categories = [
    {
      label: 'Stream Conditions',
      types: [
        { type: 'stream_name_contains' as ConditionType, label: 'Stream Name Contains' },
        { type: 'stream_name_matches' as ConditionType, label: 'Stream Name Matches (Regex)' },
        { type: 'stream_group_contains' as ConditionType, label: 'Stream Group Contains' },
        { type: 'stream_group_matches' as ConditionType, label: 'Stream Group Matches (Regex)' },
        { type: 'quality_min' as ConditionType, label: 'Minimum Quality' },
        { type: 'quality_max' as ConditionType, label: 'Maximum Quality' },
        { type: 'tvg_id_exists' as ConditionType, label: 'TVG-ID Exists' },
        { type: 'logo_exists' as ConditionType, label: 'Logo Exists' },
      ],
    },
    {
      label: 'Channel Conditions',
      types: [
        { type: 'has_channel' as ConditionType, label: 'Has Channel' },
        { type: 'channel_exists_with_name' as ConditionType, label: 'Channel Exists With Name' },
      ],
    },
    {
      label: 'Special',
      types: [
        { type: 'always' as ConditionType, label: 'Always' },
        { type: 'never' as ConditionType, label: 'Never' },
      ],
    },
  ];

  return (
    <div className="type-selector-dropdown">
      <div className="type-selector-header">
        <span>Select Condition Type</span>
        <button type="button" className="close-btn" onClick={onClose}>
          <span className="material-icons">close</span>
        </button>
      </div>
      {categories.map(cat => (
        <div key={cat.label} className="type-category">
          <div className="type-category-label">{cat.label}</div>
          {cat.types.map(t => (
            <button
              key={t.type}
              type="button"
              className="type-option"
              onClick={() => onSelect(t.type)}
            >
              {t.label}
            </button>
          ))}
        </div>
      ))}
    </div>
  );
}

// Action Type Selector Component
function ActionTypeSelector({
  onSelect,
  onClose,
}: {
  onSelect: (type: ActionType) => void;
  onClose: () => void;
}) {
  const categories = [
    {
      label: 'Creation',
      types: [
        { type: 'create_channel' as ActionType, label: 'Create Channel' },
        { type: 'create_group' as ActionType, label: 'Create Group' },
        { type: 'merge_streams' as ActionType, label: 'Merge Streams' },
      ],
    },
    {
      label: 'Assignment',
      types: [
        { type: 'assign_logo' as ActionType, label: 'Assign Logo' },
        { type: 'assign_tvg_id' as ActionType, label: 'Assign TVG-ID' },
        { type: 'assign_epg' as ActionType, label: 'Assign EPG' },
        { type: 'set_channel_number' as ActionType, label: 'Set Channel Number' },
      ],
    },
    {
      label: 'Variables',
      types: [
        { type: 'set_variable' as ActionType, label: 'Set Variable' },
      ],
    },
    {
      label: 'Control',
      types: [
        { type: 'skip' as ActionType, label: 'Skip' },
        { type: 'stop_processing' as ActionType, label: 'Stop Processing' },
        { type: 'log_match' as ActionType, label: 'Log Match' },
      ],
    },
  ];

  return (
    <div className="type-selector-dropdown">
      <div className="type-selector-header">
        <span>Select Action Type</span>
        <button type="button" className="close-btn" onClick={onClose}>
          <span className="material-icons">close</span>
        </button>
      </div>
      {categories.map(cat => (
        <div key={cat.label} className="type-category">
          <div className="type-category-label">{cat.label}</div>
          {cat.types.map(t => (
            <button
              key={t.type}
              type="button"
              className="type-option"
              onClick={() => onSelect(t.type)}
            >
              {t.label}
            </button>
          ))}
        </div>
      ))}
    </div>
  );
}
