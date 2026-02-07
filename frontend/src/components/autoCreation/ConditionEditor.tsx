/**
 * Component for editing individual conditions in auto-creation rules.
 * Uses a three-part layout: Field + Operator + Value
 */
import { useState, useEffect, useId } from 'react';
import type { Condition, ConditionType } from '../../types/autoCreation';
import { CustomSelect } from '../CustomSelect';
import type { SelectOption } from '../CustomSelect';
import './ConditionEditor.css';

// ============================================================================
// Helpers
// ============================================================================

function escapeRegex(str: string): string {
  return str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** Try to unescape an escaped regex back to a literal. Returns null if it contains real regex syntax. */
function tryUnescapeRegex(str: string): string | null {
  const unescaped = str.replace(/\\([.*+?^${}()|[\]\\])/g, '$1');
  if (escapeRegex(unescaped) === str) return unescaped;
  return null;
}

// ============================================================================
// Field & Operator Definitions
// ============================================================================

interface OperatorDef {
  id: string;
  label: string;
  valueType: 'string' | 'number' | 'regex' | 'none';
  placeholder?: string;
}

interface FieldDef {
  id: string;
  label: string;
  category: 'stream' | 'channel' | 'special';
  operators: OperatorDef[];
}

const TEXT_OPS: OperatorDef[] = [
  { id: 'contains', label: 'Contains', valueType: 'string', placeholder: 'Enter text' },
  { id: 'does_not_contain', label: 'Does Not Contain', valueType: 'string', placeholder: 'Enter text' },
  { id: 'begins_with', label: 'Begins With', valueType: 'string', placeholder: 'Enter text' },
  { id: 'ends_with', label: 'Ends With', valueType: 'string', placeholder: 'Enter text' },
  { id: 'matches', label: 'Matches (Regex)', valueType: 'regex', placeholder: 'Enter regex pattern' },
];

const EXISTS_OPS: OperatorDef[] = [
  { id: 'exists', label: 'Exists', valueType: 'none' },
  { id: 'does_not_exist', label: 'Does Not Exist', valueType: 'none' },
];

const FIELDS: FieldDef[] = [
  { id: 'stream_name', label: 'Stream Name', category: 'stream', operators: TEXT_OPS },
  { id: 'stream_group', label: 'Stream Group', category: 'stream', operators: TEXT_OPS },
  { id: 'tvg_id', label: 'TVG-ID', category: 'stream', operators: [...EXISTS_OPS, ...TEXT_OPS] },
  {
    id: 'provider', label: 'Provider', category: 'stream',
    operators: [
      { id: 'is', label: 'Is', valueType: 'string', placeholder: 'Enter provider name' },
      { id: 'is_not', label: 'Is Not', valueType: 'string', placeholder: 'Enter provider name' },
    ],
  },
  { id: 'logo', label: 'Logo', category: 'stream', operators: EXISTS_OPS },
  {
    id: 'quality', label: 'Quality', category: 'stream',
    operators: [
      { id: 'at_least', label: 'At Least', valueType: 'number', placeholder: 'e.g., 720' },
      { id: 'at_most', label: 'At Most', valueType: 'number', placeholder: 'e.g., 1080' },
    ],
  },
  {
    id: 'codec', label: 'Codec', category: 'stream',
    operators: [
      { id: 'is', label: 'Is', valueType: 'string', placeholder: 'e.g., h264' },
      { id: 'is_not', label: 'Is Not', valueType: 'string', placeholder: 'e.g., h264' },
    ],
  },
  { id: 'audio_tracks', label: 'Audio Tracks', category: 'stream', operators: EXISTS_OPS },
  { id: 'channel', label: 'Channel', category: 'channel', operators: EXISTS_OPS },
  {
    id: 'channel_name', label: 'Channel Name', category: 'channel',
    operators: [
      { id: 'is', label: 'Is', valueType: 'string', placeholder: 'Enter channel name' },
      ...TEXT_OPS,
    ],
  },
  {
    id: 'channel_group', label: 'Channel Group', category: 'channel',
    operators: [{ id: 'is', label: 'Is', valueType: 'string', placeholder: 'Enter group name' }],
  },
  {
    id: 'channel_streams', label: 'Channel Streams', category: 'channel',
    operators: [
      { id: 'exists', label: 'Has Streams', valueType: 'none' },
      { id: 'does_not_exist', label: 'No Streams', valueType: 'none' },
    ],
  },
  { id: 'always', label: 'Always', category: 'special', operators: [] },
  { id: 'never', label: 'Never', category: 'special', operators: [] },
];

const FIELD_OPTIONS: SelectOption[] = FIELDS.map(f => ({ value: f.id, label: f.label }));

// ============================================================================
// Mapping: (field, operator, value) → Condition  (for saving)
// ============================================================================

function buildCondition(
  field: string, operator: string, userValue: string | number,
  caseSensitive?: boolean, connector?: 'and' | 'or',
): Condition {
  let type: ConditionType;
  let value: string | number | boolean | undefined;
  let negate = false;

  switch (field) {
    case 'stream_name':
      switch (operator) {
        case 'does_not_contain': type = 'stream_name_contains'; value = userValue; negate = true; break;
        case 'begins_with': type = 'stream_name_matches'; value = `^${escapeRegex(String(userValue))}`; break;
        case 'ends_with': type = 'stream_name_matches'; value = `${escapeRegex(String(userValue))}$`; break;
        case 'matches': type = 'stream_name_matches'; value = userValue; break;
        default: type = 'stream_name_contains'; value = userValue; break;
      }
      break;
    case 'stream_group':
      switch (operator) {
        case 'does_not_contain': type = 'stream_group_contains'; value = userValue; negate = true; break;
        case 'begins_with': type = 'stream_group_matches'; value = `^${escapeRegex(String(userValue))}`; break;
        case 'ends_with': type = 'stream_group_matches'; value = `${escapeRegex(String(userValue))}$`; break;
        case 'matches': type = 'stream_group_matches'; value = userValue; break;
        default: type = 'stream_group_contains'; value = userValue; break;
      }
      break;
    case 'tvg_id':
      switch (operator) {
        case 'exists': type = 'tvg_id_exists'; value = true; break;
        case 'does_not_exist': type = 'tvg_id_exists'; value = false; break;
        case 'contains': type = 'tvg_id_matches'; value = escapeRegex(String(userValue)); break;
        case 'does_not_contain': type = 'tvg_id_matches'; value = escapeRegex(String(userValue)); negate = true; break;
        case 'begins_with': type = 'tvg_id_matches'; value = `^${escapeRegex(String(userValue))}`; break;
        case 'ends_with': type = 'tvg_id_matches'; value = `${escapeRegex(String(userValue))}$`; break;
        case 'matches': type = 'tvg_id_matches'; value = userValue; break;
        default: type = 'tvg_id_exists'; value = true; break;
      }
      break;
    case 'provider':
      type = 'provider_is'; value = userValue;
      if (operator === 'is_not') negate = true;
      break;
    case 'logo':
      type = 'logo_exists';
      value = operator !== 'does_not_exist';
      break;
    case 'quality':
      type = operator === 'at_most' ? 'quality_max' : 'quality_min';
      value = userValue;
      break;
    case 'codec':
      type = 'codec_is'; value = userValue;
      if (operator === 'is_not') negate = true;
      break;
    case 'audio_tracks':
      type = 'has_audio_tracks'; value = true;
      if (operator === 'does_not_exist') negate = true;
      break;
    case 'channel':
      type = 'has_channel';
      value = operator !== 'does_not_exist';
      break;
    case 'channel_name':
      switch (operator) {
        case 'is': type = 'channel_exists_with_name'; value = userValue; break;
        case 'contains': type = 'channel_exists_matching'; value = escapeRegex(String(userValue)); break;
        case 'does_not_contain': type = 'channel_exists_matching'; value = escapeRegex(String(userValue)); negate = true; break;
        case 'begins_with': type = 'channel_exists_matching'; value = `^${escapeRegex(String(userValue))}`; break;
        case 'ends_with': type = 'channel_exists_matching'; value = `${escapeRegex(String(userValue))}$`; break;
        case 'matches': type = 'channel_exists_matching'; value = userValue; break;
        default: type = 'channel_exists_with_name'; value = userValue; break;
      }
      break;
    case 'channel_group':
      type = 'channel_in_group'; value = userValue;
      break;
    case 'channel_streams':
      type = 'channel_has_streams'; value = true;
      if (operator === 'does_not_exist') negate = true;
      break;
    case 'always': type = 'always'; break;
    case 'never': type = 'never'; break;
    default: type = 'stream_name_contains'; value = userValue; break;
  }

  const condition: Condition = { type };
  if (value !== undefined) condition.value = value;
  if (negate) condition.negate = true;
  if (caseSensitive) condition.case_sensitive = true;
  if (connector) condition.connector = connector;
  return condition;
}

// ============================================================================
// Mapping: Condition → (field, operator, displayValue)  (for loading)
// ============================================================================

function parseCondition(condition: Condition): { field: string; operator: string; displayValue: string | number } {
  const { type, value, negate } = condition;

  switch (type) {
    case 'stream_name_contains':
      return { field: 'stream_name', operator: negate ? 'does_not_contain' : 'contains', displayValue: String(value ?? '') };
    case 'stream_name_matches':
      return detectRegexOp('stream_name', String(value ?? ''), true);

    case 'stream_group_contains':
      return { field: 'stream_group', operator: negate ? 'does_not_contain' : 'contains', displayValue: String(value ?? '') };
    case 'stream_group_matches':
      return detectRegexOp('stream_group', String(value ?? ''), true);

    case 'tvg_id_exists':
      return { field: 'tvg_id', operator: value === false ? 'does_not_exist' : 'exists', displayValue: '' };
    case 'tvg_id_matches':
      if (negate) {
        const lit = tryUnescapeRegex(String(value ?? ''));
        if (lit !== null) return { field: 'tvg_id', operator: 'does_not_contain', displayValue: lit };
      }
      return detectRegexOp('tvg_id', String(value ?? ''), false);

    case 'logo_exists':
      return { field: 'logo', operator: (negate || value === false) ? 'does_not_exist' : 'exists', displayValue: '' };
    case 'provider_is':
      return { field: 'provider', operator: negate ? 'is_not' : 'is', displayValue: String(value ?? '') };
    case 'quality_min':
      return { field: 'quality', operator: 'at_least', displayValue: Number(value) || 0 };
    case 'quality_max':
      return { field: 'quality', operator: 'at_most', displayValue: Number(value) || 0 };
    case 'codec_is':
      return { field: 'codec', operator: negate ? 'is_not' : 'is', displayValue: String(value ?? '') };
    case 'has_audio_tracks':
      return { field: 'audio_tracks', operator: negate ? 'does_not_exist' : 'exists', displayValue: '' };
    case 'has_channel':
      return { field: 'channel', operator: (negate || value === false) ? 'does_not_exist' : 'exists', displayValue: '' };

    case 'channel_exists_with_name':
      return { field: 'channel_name', operator: 'is', displayValue: String(value ?? '') };
    case 'channel_exists_matching':
      if (negate) {
        const lit = tryUnescapeRegex(String(value ?? ''));
        if (lit !== null) return { field: 'channel_name', operator: 'does_not_contain', displayValue: lit };
      }
      return detectRegexOp('channel_name', String(value ?? ''), false);
    case 'channel_in_group':
      return { field: 'channel_group', operator: 'is', displayValue: String(value ?? '') };
    case 'channel_has_streams':
      return { field: 'channel_streams', operator: negate ? 'does_not_exist' : 'exists', displayValue: '' };

    case 'always': return { field: 'always', operator: '', displayValue: '' };
    case 'never': return { field: 'never', operator: '', displayValue: '' };
    default: return { field: 'stream_name', operator: 'contains', displayValue: String(value ?? '') };
  }
}

/**
 * Detect the UI operator from a regex value stored in a _matches condition type.
 * @param hasNativeContains If true, plain escaped literals stay as "matches" (the field has a separate _contains type).
 */
function detectRegexOp(
  field: string, value: string, hasNativeContains: boolean,
): { field: string; operator: string; displayValue: string } {
  if (value.startsWith('^') && !value.endsWith('$')) {
    const lit = tryUnescapeRegex(value.slice(1));
    if (lit !== null) return { field, operator: 'begins_with', displayValue: lit };
  }
  if (value.endsWith('$') && !value.startsWith('^')) {
    const lit = tryUnescapeRegex(value.slice(0, -1));
    if (lit !== null) return { field, operator: 'ends_with', displayValue: lit };
  }
  if (!hasNativeContains) {
    const lit = tryUnescapeRegex(value);
    if (lit !== null) return { field, operator: 'contains', displayValue: lit };
  }
  return { field, operator: 'matches', displayValue: value };
}

// ============================================================================
// OrderNumberInput
// ============================================================================

function OrderNumberInput({ orderNumber, totalItems, onReorder }: {
  orderNumber: number;
  totalItems: number;
  onReorder?: (newPosition: number) => void;
}) {
  const [localValue, setLocalValue] = useState(String(orderNumber));

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
      <button type="button" className="reorder-btn"
        onClick={() => onReorder?.(orderNumber - 1)} disabled={orderNumber <= 1}
        aria-label="Move up" title="Move up">
        <span className="material-icons">keyboard_arrow_up</span>
      </button>
      <input type="text" inputMode="numeric" className="order-number-input"
        value={localValue} onChange={e => setLocalValue(e.target.value)}
        onBlur={() => commit(localValue)}
        onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); commit(localValue); } }}
        aria-label={`Order ${orderNumber} of ${totalItems}`} data-testid="order-number"
      />
      <button type="button" className="reorder-btn"
        onClick={() => onReorder?.(orderNumber + 1)} disabled={orderNumber >= totalItems}
        aria-label="Move down" title="Move down">
        <span className="material-icons">keyboard_arrow_down</span>
      </button>
    </div>
  );
}

// ============================================================================
// ConditionEditor Component
// ============================================================================

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
  showCaseSensitiveOption = false,
  readonly = false,
  compact = false,
  orderNumber,
  totalItems,
  onReorder,
}: ConditionEditorProps) {
  const id = useId();

  // Derive field, operator, display value from the condition data
  const { field: currentField, operator: currentOperator, displayValue } = parseCondition(condition);

  const fieldDef = FIELDS.find(f => f.id === currentField);
  const operatorDef = fieldDef?.operators.find(o => o.id === currentOperator);
  const hasOperators = fieldDef != null && fieldDef.operators.length > 0;
  const needsValue = operatorDef != null && operatorDef.valueType !== 'none';
  const isRegex = operatorDef?.valueType === 'regex';
  const isNumber = operatorDef?.valueType === 'number';
  const isStringLike = operatorDef?.valueType === 'string' || isRegex;

  const operatorOptions: SelectOption[] = fieldDef?.operators.map(o => ({ value: o.id, label: o.label })) ?? [];

  // Validation
  const getValidationError = (): string | null => {
    if (!showValidation) return null;
    if (needsValue && !displayValue && displayValue !== 0) return 'Value is required';
    if (isRegex && displayValue) {
      try { new RegExp(String(displayValue)); } catch { return 'Invalid regex pattern'; }
    }
    if (isNumber && displayValue !== undefined) {
      const num = Number(displayValue);
      if (isNaN(num) || num < 0) return 'Must be a positive number';
    }
    return null;
  };

  const validationError = getValidationError();
  const errorId = `${id}-error`;

  const handleFieldChange = (newField: string) => {
    const def = FIELDS.find(f => f.id === newField);
    if (!def || def.operators.length === 0) {
      onChange(buildCondition(newField, '', '', undefined, condition.connector));
      return;
    }
    const firstOp = def.operators[0];
    onChange(buildCondition(newField, firstOp.id, firstOp.valueType === 'none' ? '' : '', undefined, condition.connector));
  };

  const handleOperatorChange = (newOp: string) => {
    const opDef = fieldDef?.operators.find(o => o.id === newOp);
    const val = opDef?.valueType === 'none' ? '' : displayValue;
    onChange(buildCondition(currentField, newOp, val, condition.case_sensitive, condition.connector));
  };

  const handleValueChange = (value: string | number) => {
    onChange(buildCondition(currentField, currentOperator, value, condition.case_sensitive, condition.connector));
  };

  return (
    <div
      className={`condition-editor ${compact ? 'compact' : ''} ${validationError ? 'has-error' : ''}`}
      data-testid="condition-editor"
    >
      {orderNumber !== undefined && totalItems !== undefined && totalItems > 1 && !readonly && (
        <OrderNumberInput orderNumber={orderNumber} totalItems={totalItems} onReorder={onReorder} />
      )}

      <div className="condition-content">
        <div className="condition-main-row">
          {/* Field selector */}
          <CustomSelect
            options={FIELD_OPTIONS}
            value={currentField}
            onChange={handleFieldChange}
            placeholder="Select field..."
            disabled={readonly}
            className="condition-field-select"
          />

          {/* Operator selector */}
          {hasOperators && (
            <CustomSelect
              options={operatorOptions}
              value={currentOperator}
              onChange={handleOperatorChange}
              placeholder="Select operator..."
              disabled={readonly}
              className="condition-operator-select"
            />
          )}

          {/* Value input */}
          {needsValue && (
            <div className="condition-value-wrapper">
              <label htmlFor={`${id}-value`} className="sr-only">Value</label>
              {isNumber ? (
                <input
                  id={`${id}-value`}
                  type="number"
                  className="condition-value-input"
                  value={displayValue as number ?? ''}
                  onChange={e => handleValueChange(e.target.valueAsNumber)}
                  placeholder={operatorDef?.placeholder || 'Enter value'}
                  disabled={readonly}
                  min={0}
                  aria-required="true"
                  aria-describedby={validationError ? errorId : undefined}
                  aria-invalid={!!validationError}
                />
              ) : (
                <input
                  id={`${id}-value`}
                  type="text"
                  className="condition-value-input"
                  value={String(displayValue ?? '')}
                  onChange={e => handleValueChange(e.target.value)}
                  placeholder={operatorDef?.placeholder || 'Enter value'}
                  disabled={readonly}
                  aria-required="true"
                  aria-describedby={validationError ? errorId : undefined}
                  aria-invalid={!!validationError}
                />
              )}
              {isRegex && <span className="condition-hint">Regex</span>}
            </div>
          )}
        </div>

        {/* Case sensitive checkbox */}
        {showCaseSensitiveOption && isStringLike && (
          <div className="condition-options">
            <label className="condition-option">
              <input
                type="checkbox"
                checked={condition.case_sensitive ?? false}
                onChange={e => onChange(buildCondition(
                  currentField, currentOperator, displayValue,
                  e.target.checked, condition.connector,
                ))}
                disabled={readonly}
                aria-label="Case sensitive"
              />
              <span>Case sensitive</span>
            </label>
          </div>
        )}

        {/* Validation error */}
        {validationError && (
          <div id={errorId} className="condition-error" role="alert">{validationError}</div>
        )}
      </div>

      {/* Remove button */}
      {canRemove && !readonly && (
        <button type="button" className="condition-remove-btn" onClick={onRemove} aria-label="Remove condition">
          <span className="material-icons">close</span>
        </button>
      )}
    </div>
  );
}
