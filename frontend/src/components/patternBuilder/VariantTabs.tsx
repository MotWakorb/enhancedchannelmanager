/**
 * Horizontal tabs for managing pattern variants.
 * Supports add, rename (inline), delete, and select.
 */
import { useState, useRef, useEffect, useCallback, memo } from 'react';

export interface VariantTabInfo {
  name: string;
}

interface VariantTabsProps {
  variants: VariantTabInfo[];
  activeIndex: number;
  onSelect: (index: number) => void;
  onAdd: () => void;
  onRename: (index: number, name: string) => void;
  onDelete: (index: number) => void;
}

export const VariantTabs = memo(function VariantTabs({
  variants,
  activeIndex,
  onSelect,
  onAdd,
  onRename,
  onDelete,
}: VariantTabsProps) {
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [editValue, setEditValue] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editingIndex !== null) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editingIndex]);

  const handleDoubleClick = useCallback((index: number) => {
    setEditingIndex(index);
    setEditValue(variants[index].name);
  }, [variants]);

  const handleRenameConfirm = useCallback(() => {
    if (editingIndex === null) return;
    const trimmed = editValue.trim();
    if (trimmed && trimmed !== variants[editingIndex].name) {
      onRename(editingIndex, trimmed);
    }
    setEditingIndex(null);
  }, [editingIndex, editValue, variants, onRename]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      handleRenameConfirm();
    } else if (e.key === 'Escape') {
      setEditingIndex(null);
    }
  }, [handleRenameConfirm]);

  return (
    <div className="pb-variant-tabs">
      <div className="pb-variant-tabs-list">
        {variants.map((v, i) => (
          <div
            key={i}
            className={`pb-variant-tab${i === activeIndex ? ' pb-variant-tab-active' : ''}`}
            onClick={() => { if (editingIndex !== i) onSelect(i); }}
            onDoubleClick={() => handleDoubleClick(i)}
          >
            {editingIndex === i ? (
              <input
                ref={inputRef}
                type="text"
                className="pb-variant-tab-input"
                value={editValue}
                onChange={(e) => setEditValue(e.target.value)}
                onBlur={handleRenameConfirm}
                onKeyDown={handleKeyDown}
                spellCheck={false}
              />
            ) : (
              <span className="pb-variant-tab-name">{v.name}</span>
            )}
            {variants.length > 1 && editingIndex !== i && (
              <button
                type="button"
                className="pb-variant-tab-delete"
                onClick={(e) => { e.stopPropagation(); onDelete(i); }}
                title="Delete variant"
              >
                <span className="material-icons">close</span>
              </button>
            )}
          </div>
        ))}
        <button
          type="button"
          className="pb-variant-tab-add"
          onClick={onAdd}
          title="Add variant"
        >
          <span className="material-icons">add</span>
        </button>
      </div>
    </div>
  );
});
