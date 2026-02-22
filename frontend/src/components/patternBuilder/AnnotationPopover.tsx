/**
 * Floating popover for naming and typing a new annotation.
 * Appears when the user selects text in the AnnotationCanvas.
 * Also supports edit mode when clicking an existing annotation.
 */
import { useState, useRef, useEffect, memo } from 'react';
import type { Annotation, VariableType } from './types';
import { VARIABLE_TYPE_LABELS, NAME_TYPE_HINTS } from './types';

interface AnnotationPopoverProps {
  /** Position to anchor the popover (from getBoundingClientRect). */
  anchorRect: DOMRect;
  /** The selected text (shown as preview). */
  selectedText: string;
  /** Existing variable names (for autocomplete / validation). */
  existingVariables: string[];
  /** Called when the user confirms the annotation. */
  onConfirm: (variableName: string, variableType: VariableType, customRegex?: string) => void;
  /** Called when the user cancels. */
  onCancel: () => void;
  /** Optional: annotation being edited (pre-fills fields). */
  editingAnnotation?: Annotation;
  /** Called when user wants to delete the annotation being edited. */
  onDelete?: () => void;
}

export const AnnotationPopover = memo(function AnnotationPopover({
  anchorRect,
  selectedText,
  existingVariables,
  onConfirm,
  onCancel,
  editingAnnotation,
  onDelete,
}: AnnotationPopoverProps) {
  const [name, setName] = useState(editingAnnotation?.variableName || '');
  const [type, setType] = useState<VariableType>(editingAnnotation?.variableType || 'text');
  const [customRegex, setCustomRegex] = useState(editingAnnotation?.customRegex || '');
  const [error, setError] = useState('');
  const popoverRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const isEditing = Boolean(editingAnnotation);

  // Auto-focus the name input
  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  // Close on outside click
  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      if (popoverRef.current && !popoverRef.current.contains(e.target as Node)) {
        onCancel();
      }
    };
    document.addEventListener('mousedown', handleClick);
    return () => document.removeEventListener('mousedown', handleClick);
  }, [onCancel]);

  // Close on Escape
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel();
    };
    document.addEventListener('keydown', handleKey);
    return () => document.removeEventListener('keydown', handleKey);
  }, [onCancel]);

  // Auto-detect type based on selected text (only for new annotations)
  useEffect(() => {
    if (isEditing) return;
    // Check for date expressions (e.g. "Feb 15", "02/15", "Feb 15, 2025")
    if (/(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}/i.test(selectedText) ||
        /\d{1,2}\/\d{1,2}/.test(selectedText)) {
      setType('date');
      setName('date');
    // Check for time expressions (e.g. "8:00PM ET", "2:30 PM", "8PM")
    } else if (/\d{1,2}\s*:\s*\d{2}\s*[AaPp][Mm]/.test(selectedText) ||
        /\d{1,2}\s*[AaPp][Mm]/.test(selectedText)) {
      setType('time');
      setName('time');
    } else if (/^\d+$/.test(selectedText)) setType('number');
    else setType('text');
  }, [selectedText, isEditing]);

  // Override type when user enters a recognized variable name (e.g. "hours" → day)
  useEffect(() => {
    if (isEditing) return;
    const hint = NAME_TYPE_HINTS[name.trim().toLowerCase()];
    if (hint) setType(hint);
  }, [name, isEditing]);

  // Force name to "time"/"date" when compound type is selected
  useEffect(() => {
    if (type === 'time') setName('time');
    if (type === 'date') setName('date');
  }, [type]);

  const handleConfirm = () => {
    const trimmed = name.trim();
    if (!trimmed) {
      setError('Name is required');
      return;
    }
    if (!/^[a-zA-Z_][a-zA-Z0-9_]*$/.test(trimmed)) {
      setError('Must be a valid identifier (letters, numbers, underscores)');
      return;
    }
    if (type === 'custom' && !customRegex.trim()) {
      setError('Custom regex is required');
      return;
    }
    // Compound types create sub-groups — check for conflicts
    if (type === 'time') {
      const timeGroups = ['hour', 'minute', 'ampm', 'timezone'];
      const conflicts = timeGroups.filter(g => existingVariables.includes(g));
      if (conflicts.length > 0) {
        setError(`Conflicts with existing variable${conflicts.length > 1 ? 's' : ''}: ${conflicts.join(', ')}`);
        return;
      }
    }
    if (type === 'date') {
      const dateGroups = ['month', 'day', 'year'];
      const conflicts = dateGroups.filter(g => existingVariables.includes(g));
      if (conflicts.length > 0) {
        setError(`Conflicts with existing variable${conflicts.length > 1 ? 's' : ''}: ${conflicts.join(', ')}`);
        return;
      }
    }
    onConfirm(trimmed, type, type === 'custom' ? customRegex.trim() : undefined);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      e.stopPropagation();
      handleConfirm();
    }
  };

  // Position the popover below the selection
  const style: React.CSSProperties = {
    position: 'fixed',
    left: Math.max(8, anchorRect.left),
    top: anchorRect.bottom + 8,
    zIndex: 10000,
  };

  const typeButtonLabel = (t: VariableType) => {
    switch (t) {
      case 'text': return 'Text';
      case 'number': return 'Num';
      case 'word': return 'Word';
      case 'date': return 'Date';
      case 'time': return 'Time';
      case 'custom': return 'Custom';
    }
  };

  return (
    <div className="pb-popover" ref={popoverRef} style={style} onKeyDown={handleKeyDown}>
      <div>
        <div className="pb-popover-preview">
          <span className="pb-popover-label">{isEditing ? 'Editing:' : 'Selected:'}</span>
          <code className="pb-popover-text">{selectedText}</code>
        </div>

        <div className="pb-popover-field">
          <label className="pb-popover-label">Variable name</label>
          <input
            ref={inputRef}
            type="text"
            className="pb-popover-input"
            value={name}
            onChange={(e) => { setName(e.target.value); setError(''); }}
            placeholder="e.g. team1, league, hour"
            autoComplete="off"
            spellCheck={false}
            disabled={type === 'time' || type === 'date'}
          />
          {type === 'time' && (
            <div className="pb-popover-hint">Creates: hour, minute, ampm, timezone</div>
          )}
          {type === 'date' && (
            <div className="pb-popover-hint">Creates: month, day, year</div>
          )}
        </div>

        {existingVariables.length > 0 && !name && !isEditing && (
          <div className="pb-popover-suggestions">
            {existingVariables.slice(0, 5).map(v => (
              <button
                key={v}
                type="button"
                className="pb-popover-suggestion"
                onClick={() => setName(v)}
              >
                {v}
              </button>
            ))}
          </div>
        )}

        <div className="pb-popover-field">
          <label className="pb-popover-label">Type</label>
          <div className="pb-popover-types">
            {(Object.keys(VARIABLE_TYPE_LABELS) as VariableType[]).map(t => (
              <button
                key={t}
                type="button"
                className={`pb-popover-type-btn${type === t ? ' active' : ''}`}
                onClick={() => setType(t)}
              >
                {typeButtonLabel(t)}
              </button>
            ))}
          </div>
        </div>

        {type === 'custom' && (
          <div className="pb-popover-field">
            <label className="pb-popover-label">Custom regex</label>
            <input
              type="text"
              className="pb-popover-input pb-popover-input-mono"
              value={customRegex}
              onChange={(e) => setCustomRegex(e.target.value)}
              placeholder="e.g. [A-Z]{2,4}"
              spellCheck={false}
            />
          </div>
        )}

        {error && <div className="pb-popover-error">{error}</div>}

        <div className="pb-popover-actions">
          {isEditing && onDelete && (
            <button type="button" className="pb-popover-btn pb-popover-btn-delete" onClick={onDelete}>
              Delete
            </button>
          )}
          <button type="button" className="pb-popover-btn pb-popover-btn-cancel" onClick={onCancel}>
            Cancel
          </button>
          <button type="button" className="pb-popover-btn pb-popover-btn-confirm" onClick={handleConfirm}>
            {isEditing ? 'Update' : 'Add Variable'}
          </button>
        </div>
      </div>
    </div>
  );
});
