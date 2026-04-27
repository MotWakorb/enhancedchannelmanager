import { memo, useCallback, useRef, useState } from 'react';
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core';
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import type { SubstitutionPair } from '../types';
import './SubstitutionPairsEditor.css';

interface SubstitutionPairsEditorProps {
  pairs: SubstitutionPair[];
  onChange: (pairs: SubstitutionPair[]) => void;
}

interface SortablePairRowProps {
  pair: SubstitutionPair;
  index: number;
  id: string;
  onUpdate: (index: number, updates: Partial<SubstitutionPair>) => void;
  onDelete: (index: number) => void;
}

function SortablePairRow({ pair, index, id, onUpdate, onDelete }: SortablePairRowProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };

  const [regexError, setRegexError] = useState<string | null>(null);

  const validateRegex = useCallback((value: string) => {
    if (!value || !pair.is_regex) {
      setRegexError(null);
      return;
    }
    try {
      new RegExp(value);
      setRegexError(null);
    } catch {
      setRegexError('Invalid regex');
    }
  }, [pair.is_regex]);

  return (
    <div ref={setNodeRef} style={style} className={`sub-pair-row ${!pair.enabled ? 'sub-pair-disabled' : ''}`}>
      <div className="sub-pair-drag" {...attributes} {...listeners}>
        <span className="material-icons">drag_indicator</span>
      </div>
      <div className="sub-pair-fields">
        <div className="sub-pair-field">
          <input
            type="text"
            value={pair.find}
            onChange={(e) => {
              onUpdate(index, { find: e.target.value });
              validateRegex(e.target.value);
            }}
            placeholder="Find..."
            className={regexError ? 'error' : ''}
          />
          {regexError && <span className="sub-pair-regex-error">{regexError}</span>}
        </div>
        <span className="sub-pair-arrow">
          <span className="material-icons">arrow_forward</span>
        </span>
        <div className="sub-pair-field">
          <input
            type="text"
            value={pair.replace}
            onChange={(e) => onUpdate(index, { replace: e.target.value })}
            placeholder="Replace..."
          />
        </div>
      </div>
      <label className="sub-pair-regex" title="Use regex pattern matching">
        <input
          type="checkbox"
          checked={pair.is_regex}
          onChange={(e) => {
            onUpdate(index, { is_regex: e.target.checked });
            if (e.target.checked) validateRegex(pair.find);
            else setRegexError(null);
          }}
        />
        <span className="sub-pair-regex-label">.*</span>
      </label>
      <button
        type="button"
        className={`sub-pair-toggle ${pair.enabled ? 'active' : ''}`}
        onClick={() => onUpdate(index, { enabled: !pair.enabled })}
        title={pair.enabled ? 'Disable pair' : 'Enable pair'}
      >
        <span className="material-icons">{pair.enabled ? 'toggle_on' : 'toggle_off'}</span>
      </button>
      <button
        type="button"
        className="sub-pair-delete"
        onClick={() => onDelete(index)}
        title="Remove pair"
      >
        <span className="material-icons">close</span>
      </button>
    </div>
  );
}

export const SubstitutionPairsEditor = memo(function SubstitutionPairsEditor({ pairs, onChange }: SubstitutionPairsEditorProps) {
  const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  );

  // Maintain stable unique IDs for drag-and-drop. IDs are always aligned 1:1
  // with pair positions. We compute in useMemo so the fix lifts the length-sync
  // logic out of an effect; stateful reorder is tracked in setItemIds (handler).
  const [itemIds, setItemIds] = useState<string[]>(() => []);
  const idCounterRef = useRef(0);

  // Normalize itemIds length to match pairs length. This is the React-blessed
  // "derive-state-from-props" pattern (update-during-render with a guard). The
  // ref-as-counter is only read/incremented inside this guarded branch, not on
  // every render, so the "refs during render" rule's concern (instability) is
  // moot here — the mutation is bounded and functional.
  // See: https://react.dev/learn/you-might-not-need-an-effect#adjusting-some-state-when-a-prop-changes
  if (itemIds.length !== pairs.length) {
    const next = itemIds.slice(0, pairs.length);
    while (next.length < pairs.length) {
      idCounterRef.current += 1;
      next.push(`sp-${idCounterRef.current}`);
    }
    setItemIds(next);
  }

  const handleDragEnd = useCallback((event: DragEndEvent) => {
    const { active, over } = event;
    if (over && active.id !== over.id) {
      const oldIndex = itemIds.indexOf(String(active.id));
      const newIndex = itemIds.indexOf(String(over.id));
      if (oldIndex !== -1 && newIndex !== -1) {
        onChange(arrayMove(pairs, oldIndex, newIndex));
        setItemIds(arrayMove(itemIds, oldIndex, newIndex));
      }
    }
  }, [pairs, itemIds, onChange]);

  const handleUpdate = useCallback((index: number, updates: Partial<SubstitutionPair>) => {
    const newPairs = [...pairs];
    newPairs[index] = { ...newPairs[index], ...updates };
    onChange(newPairs);
  }, [pairs, onChange]);

  const handleDelete = useCallback((index: number) => {
    onChange(pairs.filter((_, i) => i !== index));
    setItemIds(prev => prev.filter((_, i) => i !== index));
  }, [pairs, onChange]);

  const handleAdd = useCallback(() => {
    onChange([...pairs, { find: '', replace: '', is_regex: false, enabled: true }]);
  }, [pairs, onChange]);

  // Reorder mode gates the DndContext mount. @dnd-kit's useRect() attaches a
  // MutationObserver to document.body for every mounted sortable; mounting the
  // observers only while the user is actively reordering avoids retaining
  // millions of MutationRecords on heavy notification-stream pages (gh #207).
  const [isReorderMode, setIsReorderMode] = useState(false);

  return (
    <div className="sub-pairs-editor">
      {pairs.length > 0 ? (
        isReorderMode ? (
          <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={handleDragEnd}>
            <SortableContext items={itemIds} strategy={verticalListSortingStrategy}>
              {pairs.map((pair, index) => (
                <SortablePairRow
                  key={itemIds[index] || index}
                  id={itemIds[index] || `sp-fallback-${index}`}
                  pair={pair}
                  index={index}
                  onUpdate={handleUpdate}
                  onDelete={handleDelete}
                />
              ))}
            </SortableContext>
          </DndContext>
        ) : (
          pairs.map((pair, index) => (
            <SortablePairRow
              key={itemIds[index] || index}
              id={itemIds[index] || `sp-fallback-${index}`}
              pair={pair}
              index={index}
              onUpdate={handleUpdate}
              onDelete={handleDelete}
            />
          ))
        )
      ) : (
        <p className="sub-pairs-empty">No substitution pairs. Add one to transform names before pattern matching.</p>
      )}
      <div className="sub-pairs-actions">
        <button type="button" className="sub-pairs-add" onClick={handleAdd}>
          <span className="material-icons">add</span>
          Add Pair
        </button>
        {pairs.length > 1 && (
          <button
            type="button"
            className="btn-secondary"
            onClick={() => setIsReorderMode((v) => !v)}
            title={isReorderMode ? 'Exit reorder mode' : 'Reorder pairs'}
          >
            <span className="material-icons">
              {isReorderMode ? 'check' : 'reorder'}
            </span>
            {isReorderMode ? 'Done' : 'Reorder'}
          </button>
        )}
      </div>
    </div>
  );
});
