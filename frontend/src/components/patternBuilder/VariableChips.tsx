/**
 * Displays color-coded chips for each defined variable.
 * Clicking a chip selects it for editing or deletion.
 */
import { memo } from 'react';
import type { Annotation } from './types';
import { getVariableColor } from './colors';
import { getVariableNames, getPatternTarget, isWrapperAnnotation } from './regexEngine';

interface VariableChipsProps {
  annotations: Annotation[];
  onDelete: (variableName: string) => void;
}

const TARGET_LABELS: Record<string, string> = {
  title: 'Title',
  time: 'Time',
  date: 'Date',
};

export const VariableChips = memo(function VariableChips({
  annotations,
  onDelete,
}: VariableChipsProps) {
  const variableNames = getVariableNames(annotations);

  if (!variableNames.length) return null;

  return (
    <div className="pb-variables">
      <span className="pb-variables-label">Variables:</span>
      <div className="pb-variables-list">
        {variableNames.map(name => {
          const color = getVariableColor(name);
          const ann = annotations.find(a => a.variableName === name)!;
          const target = getPatternTarget(name);
          const isWrapper = isWrapperAnnotation(ann, annotations);
          return (
            <span
              key={name}
              className={`pb-chip${isWrapper ? ' pb-chip-wrapper' : ''}`}
              style={{
                backgroundColor: color.bg,
                borderColor: color.full,
                color: color.full,
              }}
            >
              <span className="pb-chip-name">{name}</span>
              <span className="pb-chip-type">{ann.variableType}</span>
              <span className="pb-chip-target">{TARGET_LABELS[target]}</span>
              {isWrapper && <span className="pb-chip-badge">wraps</span>}
              <button
                type="button"
                className="pb-chip-delete"
                onClick={() => onDelete(name)}
                title={`Remove ${name}`}
              >
                <span className="material-icons">close</span>
              </button>
            </span>
          );
        })}
      </div>
    </div>
  );
});
