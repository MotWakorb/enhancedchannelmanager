/**
 * Component for building and editing auto-creation rules.
 */
import { useState, useEffect, useId, useCallback } from 'react';
import type { AutoCreationRule, CreateRuleData, Condition, Action, ConditionType, ActionType } from '../../types/autoCreation';
import { ConditionEditor } from './ConditionEditor';
import { ActionEditor } from './ActionEditor';
import { CustomSelect } from '../CustomSelect';
import { getNormalizationRules } from '../../services/api';
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
  const [normalizeNames, setNormalizeNames] = useState(rule?.normalize_names ?? false);
  const [skipStruckStreams, setSkipStruckStreams] = useState(rule?.skip_struck_streams ?? false);
  const [orphanAction, setOrphanAction] = useState(rule?.orphan_action || 'delete');
  const [conditions, setConditions] = useState<Condition[]>(rule?.conditions || []);
  const [actions, setActions] = useState<Action[]>(rule?.actions || []);

  const [hasActiveNormRules, setHasActiveNormRules] = useState(false);
  const [errors, setErrors] = useState<ValidationErrors>({});
  const [saving, setSaving] = useState(false);
  const [isDirty, setIsDirty] = useState(false);
  const [showCancelConfirm, setShowCancelConfirm] = useState(false);

  // Check if any enabled normalization groups have enabled rules
  useEffect(() => {
    getNormalizationRules().then(({ groups }) => {
      const active = groups.some(g => g.enabled && g.rules?.some(r => r.enabled));
      setHasActiveNormRules(active);
    }).catch(() => {});
  }, []);

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
      enabled !== (rule?.enabled ?? true) ||
      conditions.length !== (rule?.conditions?.length || 0) ||
      actions.length !== (rule?.actions?.length || 0);
    setIsDirty(hasChanges);
  }, [name, description, enabled, conditions, actions, rule]);

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
        sort_field: sortField || '',
        sort_order: sortOrder,
        probe_on_sort: probeOnSort,
        sort_regex: sortRegex || '',
        stream_sort_field: streamSortField || '',
        stream_sort_order: streamSortOrder,
        normalize_names: normalizeNames,
        skip_struck_streams: skipStruckStreams,
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
              {!normalizeNames && hasActiveNormRules && (
                <span className="norm-hint">
                  <span className="material-icons norm-hint-icon">info</span>
                  Active normalization rules won't apply unless this is enabled
                </span>
              )}
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

          <div className="form-field">
            <label>Channel Sort</label>
            <span className="field-hint">Controls the order channels are numbered (renumbers on every run)</span>
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
                  onChange={setSortOrder}
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
                  onChange={setStreamSortOrder}
                />
              )}
            </div>
            {streamSortField === 'quality' && (
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
              onClick={handleAddCondition}
              aria-label="Add condition"
            >
              <span className="material-icons">add</span>
              Add Condition
            </button>
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
              onClick={handleAddAction}
              aria-label="Add action"
            >
              <span className="material-icons">add</span>
              Add Action
            </button>
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
        <div className="modal-overlay">
          <div className="modal-container modal-sm">
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
  const noValueTypes: ConditionType[] = ['always', 'never', 'tvg_id_exists', 'logo_exists', 'has_channel', 'channel_has_streams', 'has_audio_tracks', 'normalized_name_exists', 'normalized_name_not_exists'];
  return !noValueTypes.includes(type);
}


