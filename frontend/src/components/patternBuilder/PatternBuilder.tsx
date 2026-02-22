/**
 * PatternBuilder — orchestrator component for the Visual Pattern Builder.
 *
 * Manages state, composes child components, and emits pattern changes
 * back to the parent (DummyEPGProfileModal).
 */
import { useState, useCallback, useEffect, useMemo, memo } from 'react';
import type { Annotation, Example, PatternBuilderState, PatternBuilderProps, VariableType } from './types';
import { AnnotationCanvas } from './AnnotationCanvas';
import { AnnotationPopover } from './AnnotationPopover';
import { VariableChips } from './VariableChips';
import { ExamplesList } from './ExamplesList';
import { ValidationPanel } from './ValidationPanel';
import {
  annotationsToRegex,
  validateAgainstExamples,
  regexToAnnotations,
  getVariableNames,
} from './regexEngine';
import { resetVariableColors } from './colors';
import './PatternBuilder.css';

let nextExampleId = 1;
function generateId(): string {
  return `ex-${nextExampleId++}`;
}

export const PatternBuilder = memo(function PatternBuilder({
  titlePattern,
  timePattern,
  datePattern,
  onTitlePatternChange,
  onTimePatternChange,
  onDatePatternChange,
  builderExamples,
  onBuilderExamplesChange,
}: PatternBuilderProps) {
  // Mode: 'visual' or 'advanced' (raw regex editing)
  const [mode, setMode] = useState<'visual' | 'advanced'>('visual');

  // Builder state
  const [examples, setExamples] = useState<Example[]>([]);
  const [activeIndex, setActiveIndex] = useState(0);

  // Popover state
  const [popover, setPopover] = useState<{
    start: number;
    end: number;
    anchorRect: DOMRect;
    selectedText: string;
    editingAnnotation?: Annotation;
  } | null>(null);

  // Load persisted builder state on mount
  useEffect(() => {
    resetVariableColors();

    if (builderExamples) {
      try {
        const state: PatternBuilderState = JSON.parse(builderExamples);
        if (state.examples?.length) {
          setExamples(state.examples);
          setActiveIndex(state.activeExampleIndex || 0);
          setMode('visual');
          return;
        }
      } catch { /* ignore parse errors */ }
    }

    // If we have regex patterns but no builder state, try to reverse-parse
    if (titlePattern || timePattern || datePattern) {
      setMode('advanced');
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // Run only on mount

  // Persist builder state whenever examples change
  useEffect(() => {
    if (examples.length > 0) {
      const state: PatternBuilderState = {
        examples,
        activeExampleIndex: activeIndex,
      };
      onBuilderExamplesChange(JSON.stringify(state));
    }
  }, [examples, activeIndex, onBuilderExamplesChange]);

  // Generate patterns whenever annotations change (visual mode)
  const activeExample = examples[activeIndex] || null;
  const annotations = activeExample?.annotations || [];

  useEffect(() => {
    if (mode !== 'visual' || !activeExample) return;

    const { titlePattern: tp, timePattern: tmp, datePattern: dp } =
      annotationsToRegex(activeExample.text, annotations);

    onTitlePatternChange(tp);
    onTimePatternChange(tmp);
    onDatePatternChange(dp);
  }, [mode, activeExample, annotations, onTitlePatternChange, onTimePatternChange, onDatePatternChange]);

  // Validation results — compute combined pattern directly to avoid state timing issues
  const validationResults = useMemo(() => {
    if (!examples.length) return [];

    // In visual mode, compute the combined pattern directly from annotations.
    // This avoids timing issues between useEffect (split patterns) and useMemo.
    if (mode === 'visual' && activeExample && annotations.length > 0) {
      const { titlePattern: tp, timePattern: tmp, datePattern: dp, combinedPattern: cp } =
        annotationsToRegex(activeExample.text, annotations);
      return validateAgainstExamples(tp, tmp, dp, examples, cp);
    }

    // Advanced mode: use parent props (manually entered patterns)
    if (!titlePattern && !timePattern && !datePattern) return [];
    return validateAgainstExamples(titlePattern, timePattern, datePattern, examples);
  }, [examples, titlePattern, timePattern, datePattern, mode, activeExample, annotations]);

  // --- Handlers ---

  const handleAddExample = useCallback((text: string) => {
    const newExample: Example = {
      id: generateId(),
      text,
      annotations: [],
    };
    setExamples(prev => {
      const next = [...prev, newExample];
      // If this is the first example, make it active
      if (prev.length === 0) {
        setActiveIndex(0);
      }
      // If adding another example and active has annotations, try to
      // reverse-parse the current patterns to seed annotations
      if (prev.length > 0 && prev[activeIndex]?.annotations.length > 0) {
        const { combinedPattern: cp } = annotationsToRegex(
          prev[activeIndex].text, prev[activeIndex].annotations
        );
        if (cp) {
          const seeded = regexToAnnotations(cp, text);
          if (seeded) {
            newExample.annotations = seeded;
          }
        }
      }
      return next;
    });
  }, [activeIndex]);

  const handleRemoveExample = useCallback((index: number) => {
    setExamples(prev => {
      const next = prev.filter((_, i) => i !== index);
      if (next.length === 0) {
        setActiveIndex(0);
        onTitlePatternChange('');
        onTimePatternChange('');
        onDatePatternChange('');
      } else {
        setActiveIndex(Math.min(activeIndex, next.length - 1));
      }
      return next;
    });
  }, [activeIndex, onTitlePatternChange, onTimePatternChange, onDatePatternChange]);

  const handleSelectExample = useCallback((index: number) => {
    setActiveIndex(index);
    setPopover(null);
  }, []);

  const handleCanvasSelect = useCallback((
    start: number,
    end: number,
    anchorRect: DOMRect,
    selectedText: string,
  ) => {
    setPopover({ start, end, anchorRect, selectedText });
  }, []);

  const handleConfirmAnnotation = useCallback((
    variableName: string,
    variableType: VariableType,
    customRegex?: string,
  ) => {
    if (!popover || activeIndex >= examples.length) return;

    const newAnnotation: Annotation = {
      start: popover.start,
      end: popover.end,
      variableName,
      variableType,
      customRegex,
    };

    setExamples(prev => prev.map((ex, i) => {
      if (i !== activeIndex) return ex;
      if (popover.editingAnnotation) {
        // Replace existing annotation (match by position + name for overlapping support)
        return {
          ...ex,
          annotations: ex.annotations.map(a =>
            (a.start === popover.editingAnnotation!.start && a.end === popover.editingAnnotation!.end && a.variableName === popover.editingAnnotation!.variableName)
              ? newAnnotation : a
          ),
        };
      }
      return { ...ex, annotations: [...ex.annotations, newAnnotation] };
    }));
    setPopover(null);
  }, [popover, activeIndex, examples.length]);

  const handleCancelPopover = useCallback(() => {
    setPopover(null);
  }, []);

  const handleDeleteEditingAnnotation = useCallback(() => {
    if (!popover?.editingAnnotation) return;
    const ann = popover.editingAnnotation;
    setExamples(prev => prev.map((ex, i) => {
      if (i !== activeIndex) return ex;
      return {
        ...ex,
        annotations: ex.annotations.filter(a =>
          !(a.start === ann.start && a.end === ann.end && a.variableName === ann.variableName)
        ),
      };
    }));
    setPopover(null);
  }, [popover, activeIndex]);

  const handleAnnotationClick = useCallback((annotation: Annotation) => {
    // Open edit popover for the clicked annotation
    // Use a synthetic rect based on approximate position
    const canvas = document.querySelector('.pb-canvas');
    if (!canvas) return;
    const canvasRect = canvas.getBoundingClientRect();
    const anchorRect = new DOMRect(
      canvasRect.left + 20,
      canvasRect.top + canvasRect.height / 2,
      100,
      20,
    );
    const text = examples[activeIndex]?.text || '';
    setPopover({
      start: annotation.start,
      end: annotation.end,
      anchorRect,
      selectedText: text.slice(annotation.start, annotation.end),
      editingAnnotation: annotation,
    });
  }, [activeIndex, examples]);

  const handleDeleteVariable = useCallback((variableName: string) => {
    setExamples(prev => prev.map((ex, i) => {
      if (i !== activeIndex) return ex;
      return {
        ...ex,
        annotations: ex.annotations.filter(a => a.variableName !== variableName),
      };
    }));
  }, [activeIndex]);

  const handleModeToggle = useCallback(() => {
    if (mode === 'visual') {
      setMode('advanced');
    } else {
      // Switching from advanced to visual — try to reverse-parse current patterns
      if (examples.length > 0) {
        const combined = [titlePattern, timePattern, datePattern].filter(Boolean).join('');
        if (combined) {
          const parsed = regexToAnnotations(combined, examples[activeIndex]?.text || '');
          if (parsed) {
            setExamples(prev => prev.map((ex, i) => {
              if (i !== activeIndex) return ex;
              return { ...ex, annotations: parsed };
            }));
          }
        }
      }
      setMode('visual');
    }
  }, [mode, examples, activeIndex, titlePattern, timePattern, datePattern]);

  const existingVariables = useMemo(() =>
    getVariableNames(annotations), [annotations]);

  return (
    <div className="pb-container">
      <div className="pb-header">
        <span className="pb-header-title">Pattern Configuration</span>
        <button
          type="button"
          className="pb-mode-toggle"
          onClick={handleModeToggle}
        >
          <span className="material-icons">
            {mode === 'visual' ? 'code' : 'auto_fix_high'}
          </span>
          {mode === 'visual' ? 'Advanced' : 'Visual Builder'}
        </button>
      </div>

      {mode === 'visual' ? (
        <>
          <p className="pb-hint">
            Add example titles, then highlight text spans to define variables. The regex patterns are generated automatically.
          </p>

          <ExamplesList
            examples={examples}
            activeIndex={activeIndex}
            validationResults={validationResults}
            onAddExample={handleAddExample}
            onRemoveExample={handleRemoveExample}
            onSelectExample={handleSelectExample}
          />

          {activeExample && (
            <>
              <div className="pb-canvas-area">
                <div className="pb-canvas-label">
                  Annotate Example
                  <span className="pb-canvas-hint">(highlight text to create variables)</span>
                </div>
                <AnnotationCanvas
                  text={activeExample.text}
                  annotations={annotations}
                  onSelect={handleCanvasSelect}
                  onAnnotationClick={handleAnnotationClick}
                  interactive={true}
                />
              </div>

              <VariableChips
                annotations={annotations}
                onDelete={handleDeleteVariable}
              />
            </>
          )}

          <ValidationPanel
            titlePattern={titlePattern}
            timePattern={timePattern}
            datePattern={datePattern}
            results={validationResults}
            hasAnnotations={annotations.length > 0}
          />

          {popover && (
            <AnnotationPopover
              anchorRect={popover.anchorRect}
              selectedText={popover.selectedText}
              existingVariables={existingVariables}
              onConfirm={handleConfirmAnnotation}
              onCancel={handleCancelPopover}
              editingAnnotation={popover.editingAnnotation}
              onDelete={popover.editingAnnotation ? handleDeleteEditingAnnotation : undefined}
            />
          )}
        </>
      ) : (
        /* Advanced mode — raw regex inputs */
        <div className="pb-advanced">
          <p className="pb-hint">
            Define regex patterns with named capture groups. Use (?&lt;groupname&gt;pattern) syntax.
          </p>

          <div className="modal-form-group">
            <label htmlFor="pbTitlePattern">Title Pattern <span className="modal-required">*</span></label>
            <input
              id="pbTitlePattern"
              type="text"
              value={titlePattern}
              onChange={(e) => onTitlePatternChange(e.target.value)}
              placeholder="(?<league>\w+) \d+: (?<team1>.*) VS (?<team2>.*)"
              spellCheck={false}
            />
            <p className="form-hint">Regex with named groups to extract title info</p>
          </div>

          <div className="modal-form-group">
            <label htmlFor="pbTimePattern">Time Pattern (Optional)</label>
            <input
              id="pbTimePattern"
              type="text"
              value={timePattern}
              onChange={(e) => onTimePatternChange(e.target.value)}
              placeholder="@ (?<hour>\d+):(?<minute>\d+)(?<ampm>AM|PM)"
              spellCheck={false}
            />
            <p className="form-hint">Groups: hour, minute, ampm</p>
          </div>

          <div className="modal-form-group">
            <label htmlFor="pbDatePattern">Date Pattern (Optional)</label>
            <input
              id="pbDatePattern"
              type="text"
              value={datePattern}
              onChange={(e) => onDatePatternChange(e.target.value)}
              placeholder="@ (?<month>\w+) (?<day>\d+)"
              spellCheck={false}
            />
            <p className="form-hint">Groups: month, day, year</p>
          </div>
        </div>
      )}
    </div>
  );
});
