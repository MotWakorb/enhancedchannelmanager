/**
 * AddTagInput Component
 *
 * Input component for adding custom normalization tags.
 * Includes a text field and mode selector (prefix/suffix/both).
 */
import { useState, useCallback } from 'react';
import './AddTagInput.css';
import { NormalizationTagMode } from '../services/api';

export interface AddTagInputProps {
  /** Callback when a tag is added */
  onAdd: (value: string, mode: NormalizationTagMode) => void;
  /** Placeholder text for the input */
  placeholder?: string;
  /** Show in compact mode */
  compact?: boolean;
  /** Show in inline mode (no background) */
  inline?: boolean;
  /** Default mode selection */
  defaultMode?: NormalizationTagMode;
  /** Disabled state */
  disabled?: boolean;
}

const MODE_OPTIONS: { value: NormalizationTagMode; label: string }[] = [
  { value: 'both', label: 'Any position' },
  { value: 'prefix', label: 'Prefix only' },
  { value: 'suffix', label: 'Suffix only' },
];

export function AddTagInput({
  onAdd,
  placeholder = 'Add custom tag...',
  compact = false,
  inline = false,
  defaultMode = 'both',
  disabled = false,
}: AddTagInputProps) {
  const [value, setValue] = useState('');
  const [mode, setMode] = useState<NormalizationTagMode>(defaultMode);

  const handleSubmit = useCallback(() => {
    const trimmed = value.trim().toUpperCase();
    if (trimmed) {
      onAdd(trimmed, mode);
      setValue('');
    }
  }, [value, mode, onAdd]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && value.trim()) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const classNames = [
    'add-tag-input',
    compact && 'add-tag-input-compact',
    inline && 'add-tag-input-inline',
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <div className={classNames}>
      <div className="add-tag-input-field">
        <input
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          disabled={disabled}
          aria-label="New tag value"
        />
      </div>
      <div className="add-tag-input-mode">
        <select
          value={mode}
          onChange={(e) => setMode(e.target.value as NormalizationTagMode)}
          disabled={disabled}
          aria-label="Tag matching mode"
        >
          {MODE_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
      </div>
      <button
        className="add-tag-input-btn"
        onClick={handleSubmit}
        disabled={disabled || !value.trim()}
        type="button"
        aria-label="Add tag"
      >
        <span className="material-icons">add</span>
        Add
      </button>
    </div>
  );
}

export default AddTagInput;
