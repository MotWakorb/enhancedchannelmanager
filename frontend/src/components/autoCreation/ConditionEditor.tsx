/**
 * Component for editing individual conditions in auto-creation rules.
 */
import { useState, useEffect, useId } from 'react';
import type { Condition, ConditionType } from '../../types/autoCreation';
import './ConditionEditor.css';

// Condition type definitions with metadata
const CONDITION_TYPES: {
  type: ConditionType;
  label: string;
  category: 'stream' | 'channel' | 'special';
  valueType: 'string' | 'number' | 'boolean' | 'regex' | 'none';
  placeholder?: string;
}[] = [
  // Stream conditions
  { type: 'stream_name_contains', label: 'Stream Name Contains', category: 'stream', valueType: 'string', placeholder: 'Enter text to match' },
  { type: 'stream_name_matches', label: 'Stream Name Matches (Regex)', category: 'stream', valueType: 'regex', placeholder: 'Enter regex pattern' },
  { type: 'stream_group_contains', label: 'Stream Group Contains', category: 'stream', valueType: 'string', placeholder: 'Enter group text to match' },
  { type: 'stream_group_matches', label: 'Stream Group Matches (Regex)', category: 'stream', valueType: 'regex', placeholder: 'Enter regex pattern' },
  { type: 'tvg_id_exists', label: 'TVG-ID Exists', category: 'stream', valueType: 'boolean' },
  { type: 'tvg_id_matches', label: 'TVG-ID Matches', category: 'stream', valueType: 'string', placeholder: 'Enter TVG-ID pattern' },
  { type: 'logo_exists', label: 'Logo Exists', category: 'stream', valueType: 'boolean' },
  { type: 'provider_is', label: 'Provider Is', category: 'stream', valueType: 'string', placeholder: 'Enter provider name' },
  { type: 'quality_min', label: 'Minimum Quality', category: 'stream', valueType: 'number', placeholder: 'e.g., 720' },
  { type: 'quality_max', label: 'Maximum Quality', category: 'stream', valueType: 'number', placeholder: 'e.g., 1080' },
  { type: 'codec_is', label: 'Codec Is', category: 'stream', valueType: 'string', placeholder: 'e.g., h264' },
  { type: 'has_audio_tracks', label: 'Has Audio Tracks', category: 'stream', valueType: 'boolean' },
  // Channel conditions
  { type: 'has_channel', label: 'Has Channel', category: 'channel', valueType: 'boolean' },
  { type: 'channel_exists_with_name', label: 'Channel Exists With Name', category: 'channel', valueType: 'string', placeholder: 'Enter channel name' },
  { type: 'channel_exists_matching', label: 'Channel Exists Matching', category: 'channel', valueType: 'regex', placeholder: 'Enter regex pattern' },
  { type: 'channel_in_group', label: 'Channel In Group', category: 'channel', valueType: 'string', placeholder: 'Enter group name' },
  { type: 'channel_has_streams', label: 'Channel Has Streams', category: 'channel', valueType: 'boolean' },
  // Special
  { type: 'always', label: 'Always', category: 'special', valueType: 'none' },
  { type: 'never', label: 'Never', category: 'special', valueType: 'none' },
];

const CATEGORIES = [
  { id: 'stream', label: 'Stream Conditions' },
  { id: 'channel', label: 'Channel Conditions' },
  { id: 'special', label: 'Special' },
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

export interface ConditionEditorProps {
  condition: Condition;
  onChange: (condition: Condition) => void;
  onRemove: () => void;
  canRemove?: boolean;
  showValidation?: boolean;
  showNegateOption?: boolean;
  showCaseSensitiveOption?: boolean;
  readonly?: boolean;
  draggable?: boolean;
  compact?: boolean;
  orderNumber?: number;
  totalItems?: number;
  onReorder?: (newPosition: number) => void;
}

export function ConditionEditor({
  condition,
  onChange,
  onRemove,
  canRemove = true,
  showValidation = false,
  showNegateOption = false,
  showCaseSensitiveOption = false,
  readonly = false,
  draggable = false,
  compact = false,
  orderNumber,
  totalItems,
  onReorder,
}: ConditionEditorProps) {
  const id = useId();
  const [typeSelectOpen, setTypeSelectOpen] = useState(false);

  const conditionDef = CONDITION_TYPES.find(c => c.type === condition.type);
  const needsValue = conditionDef?.valueType !== 'none' && conditionDef?.valueType !== 'boolean';
  const isBoolean = conditionDef?.valueType === 'boolean';
  const isRegex = conditionDef?.valueType === 'regex';
  const isNumber = conditionDef?.valueType === 'number';
  const isString = conditionDef?.valueType === 'string' || isRegex;

  // Validation
  const getValidationError = (): string | null => {
    if (!showValidation) return null;

    if (needsValue && !condition.value && condition.value !== 0) {
      return 'Value is required';
    }

    if (isRegex && condition.value) {
      try {
        new RegExp(String(condition.value));
      } catch {
        return 'Invalid regex pattern';
      }
    }

    if (isNumber && condition.value !== undefined) {
      const num = Number(condition.value);
      if (isNaN(num) || num < 0) {
        return 'Must be a positive number';
      }
    }

    return null;
  };

  const validationError = getValidationError();
  const errorId = `${id}-error`;

  const handleTypeChange = (newType: ConditionType) => {
    const newDef = CONDITION_TYPES.find(c => c.type === newType);
    const newCondition: Condition = { type: newType };

    // Initialize value based on type
    if (newDef?.valueType === 'boolean') {
      newCondition.value = true;
    }

    // Preserve options if applicable
    if (condition.negate !== undefined) newCondition.negate = condition.negate;
    if (condition.case_sensitive !== undefined) newCondition.case_sensitive = condition.case_sensitive;

    onChange(newCondition);
    setTypeSelectOpen(false);
  };

  const handleValueChange = (value: string | number | boolean) => {
    onChange({ ...condition, value });
  };

  return (
    <div
      className={`condition-editor ${compact ? 'compact' : ''} ${validationError ? 'has-error' : ''}`}
      data-testid="condition-editor"
    >
      {orderNumber !== undefined && totalItems !== undefined && totalItems > 1 && !readonly && (
        <OrderNumberInput
          orderNumber={orderNumber}
          totalItems={totalItems}
          onReorder={onReorder}
        />
      )}

      <div className="condition-content">
        {/* Row 1: Type + Value */}
        <div className="condition-main-row">
          {/* Type Selector */}
          <div className="condition-type-wrapper">
            <label htmlFor={`${id}-type`} className="sr-only">Condition type</label>
            <div className="condition-type-select">
              <button
                id={`${id}-type`}
                type="button"
                className="condition-type-button"
                onClick={() => !readonly && setTypeSelectOpen(!typeSelectOpen)}
                disabled={readonly}
                aria-haspopup="listbox"
                aria-expanded={typeSelectOpen}
                role="combobox"
              >
                <span>{conditionDef?.label || condition.type}</span>
                <span className="material-icons">expand_more</span>
              </button>

              {typeSelectOpen && (
                <div className="condition-type-dropdown" role="listbox">
                  {CATEGORIES.map(category => (
                    <div key={category.id} className="condition-category">
                      <div className="condition-category-label">{category.label}</div>
                      {CONDITION_TYPES
                        .filter(c => c.category === category.id)
                        .map(c => (
                          <button
                            key={c.type}
                            type="button"
                            className={`condition-type-option ${c.type === condition.type ? 'selected' : ''}`}
                            onClick={() => handleTypeChange(c.type)}
                            role="option"
                            aria-selected={c.type === condition.type}
                          >
                            {c.label}
                          </button>
                        ))}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Value Input */}
          {needsValue && (
            <div className="condition-value-wrapper">
              <label htmlFor={`${id}-value`} className="sr-only">Value</label>
              {isNumber ? (
                <input
                  id={`${id}-value`}
                  type="number"
                  className="condition-value-input"
                  value={condition.value as number ?? ''}
                  onChange={e => handleValueChange(e.target.valueAsNumber)}
                  placeholder={conditionDef?.placeholder || 'Enter value'}
                  disabled={readonly}
                  min={0}
                  aria-required="true"
                  aria-describedby={validationError ? errorId : undefined}
                  aria-invalid={!!validationError}
                  role="spinbutton"
                />
              ) : (
                <input
                  id={`${id}-value`}
                  type="text"
                  className="condition-value-input"
                  value={String(condition.value ?? '')}
                  onChange={e => handleValueChange(e.target.value)}
                  placeholder={conditionDef?.placeholder || 'Enter value'}
                  disabled={readonly}
                  aria-required="true"
                  aria-describedby={validationError ? errorId : undefined}
                  aria-invalid={!!validationError}
                />
              )}
              {isRegex && <span className="condition-hint">Regex</span>}
            </div>
          )}

          {/* Boolean Toggle */}
          {isBoolean && (
            <div className="condition-boolean-wrapper">
              <label className="condition-toggle">
                <input
                  type="checkbox"
                  checked={condition.value as boolean ?? true}
                  onChange={e => handleValueChange(e.target.checked)}
                  disabled={readonly}
                  role="checkbox"
                />
                <span className="toggle-slider"></span>
                <span className="toggle-label">{condition.value ? 'Yes' : 'No'}</span>
              </label>
            </div>
          )}
        </div>

        {/* Row 2: Options (negate, case sensitive) */}
        {(showNegateOption || (showCaseSensitiveOption && isString)) && (
          <div className="condition-options">
            {showNegateOption && (
              <label className="condition-option">
                <input
                  type="checkbox"
                  checked={condition.negate ?? false}
                  onChange={e => onChange({ ...condition, negate: e.target.checked })}
                  disabled={readonly}
                  aria-label="Negate"
                />
                <span>Negate (NOT)</span>
              </label>
            )}
            {showCaseSensitiveOption && isString && (
              <label className="condition-option">
                <input
                  type="checkbox"
                  checked={condition.case_sensitive ?? false}
                  onChange={e => onChange({ ...condition, case_sensitive: e.target.checked })}
                  disabled={readonly}
                  aria-label="Case sensitive"
                />
                <span>Case sensitive</span>
              </label>
            )}
          </div>
        )}
      </div>

      {/* Validation Error */}
      {validationError && (
        <div id={errorId} className="condition-error" role="alert">
          {validationError}
        </div>
      )}

      {/* Remove Button */}
      {canRemove && !readonly && (
        <button
          type="button"
          className="condition-remove-btn"
          onClick={onRemove}
          aria-label="Remove condition"
        >
          <span className="material-icons">close</span>
        </button>
      )}
    </div>
  );
}
