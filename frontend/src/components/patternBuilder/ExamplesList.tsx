/**
 * List of example titles with add/remove/select controls.
 * Shows validation status (green/red) per example.
 */
import { useState, memo, useCallback } from 'react';
import type { Example, ValidationResult } from './types';

interface ExamplesListProps {
  examples: Example[];
  activeIndex: number;
  validationResults: ValidationResult[];
  onAddExample: (text: string) => void;
  onRemoveExample: (index: number) => void;
  onSelectExample: (index: number) => void;
}

export const ExamplesList = memo(function ExamplesList({
  examples,
  activeIndex,
  validationResults,
  onAddExample,
  onRemoveExample,
  onSelectExample,
}: ExamplesListProps) {
  const [newText, setNewText] = useState('');

  const handleAdd = useCallback(() => {
    const trimmed = newText.trim();
    if (!trimmed) return;
    onAddExample(trimmed);
    setNewText('');
  }, [newText, onAddExample]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      e.stopPropagation();
      handleAdd();
    }
  }, [handleAdd]);

  return (
    <div className="pb-examples">
      <div className="pb-examples-header">
        <span className="pb-examples-title">Example Titles</span>
        <span className="pb-examples-count">{examples.length}</span>
      </div>

      {examples.length > 0 && (
        <div className="pb-examples-list">
          {examples.map((ex, i) => {
            const result = validationResults[i];
            const isActive = i === activeIndex;
            return (
              <div
                key={ex.id}
                className={`pb-example-item${isActive ? ' pb-example-active' : ''}`}
                onClick={() => onSelectExample(i)}
              >
                <span className="pb-example-status">
                  {result ? (
                    <span
                      className={`material-icons pb-example-icon ${result.matched ? 'pb-match' : 'pb-no-match'}`}
                    >
                      {result.matched ? 'check_circle' : 'cancel'}
                    </span>
                  ) : (
                    <span className="material-icons pb-example-icon pb-pending">radio_button_unchecked</span>
                  )}
                </span>
                <span className="pb-example-text">{ex.text}</span>
                <button
                  type="button"
                  className="pb-example-remove"
                  onClick={(e) => { e.stopPropagation(); onRemoveExample(i); }}
                  title="Remove example"
                >
                  <span className="material-icons">close</span>
                </button>
              </div>
            );
          })}
        </div>
      )}

      <div className="pb-examples-add">
        <input
          type="text"
          className="pb-examples-input"
          value={newText}
          onChange={(e) => setNewText(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Paste an example channel title..."
          spellCheck={false}
        />
        <button
          type="button"
          className="pb-examples-add-btn"
          disabled={!newText.trim()}
          onClick={handleAdd}
          title="Add example"
        >
          <span className="material-icons">add</span>
        </button>
      </div>
    </div>
  );
});
