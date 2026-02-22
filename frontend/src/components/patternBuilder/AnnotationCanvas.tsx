/**
 * Renders an example string with color-coded annotation highlights.
 * Handles text selection (mouseup) to create new annotations.
 *
 * Only leaf annotations (non-wrappers) are displayed inline in the text.
 * Wrapper annotations are shown as labeled bars below the text.
 */
import { useCallback, useRef, useLayoutEffect, useReducer, memo } from 'react';
import type { Annotation } from './types';
import { getVariableColor } from './colors';
import { hasOverlap, isWrapperAnnotation } from './regexEngine';

interface AnnotationCanvasProps {
  /** The example text to display. */
  text: string;
  /** Current annotations on this text. */
  annotations: Annotation[];
  /** Called when user selects text to annotate. */
  onSelect: (start: number, end: number, anchorRect: DOMRect, selectedText: string) => void;
  /** Called when user clicks an existing annotation to edit/delete. */
  onAnnotationClick: (annotation: Annotation) => void;
  /** Whether the canvas is interactive (false for non-active examples). */
  interactive: boolean;
}

interface Segment {
  type: 'text' | 'annotation';
  start: number;
  end: number;
  content: string;
  annotation?: Annotation;
}

/** Split text into segments based on leaf (non-wrapper) annotations only. */
function buildSegments(text: string, annotations: Annotation[]): Segment[] {
  // Only use leaf annotations for inline display
  const leaves = annotations.filter(a => !isWrapperAnnotation(a, annotations));
  const sorted = [...leaves].sort((a, b) => a.start - b.start);
  const segments: Segment[] = [];
  let cursor = 0;

  for (const ann of sorted) {
    if (ann.start > cursor) {
      segments.push({
        type: 'text',
        start: cursor,
        end: ann.start,
        content: text.slice(cursor, ann.start),
      });
    }
    segments.push({
      type: 'annotation',
      start: ann.start,
      end: ann.end,
      content: text.slice(ann.start, ann.end),
      annotation: ann,
    });
    cursor = ann.end;
  }

  if (cursor < text.length) {
    segments.push({
      type: 'text',
      start: cursor,
      end: text.length,
      content: text.slice(cursor),
    });
  }

  return segments;
}

interface WrapperPos {
  left: number;
  width: number;
}

export const AnnotationCanvas = memo(function AnnotationCanvas({
  text,
  annotations,
  onSelect,
  onAnnotationClick,
  interactive,
}: AnnotationCanvasProps) {
  const canvasRef = useRef<HTMLDivElement>(null);
  const wrapperPositionsRef = useRef<Record<string, WrapperPos>>({});
  const selectionJustProcessed = useRef(false);
  const [, forceUpdate] = useReducer(x => x + 1, 0);

  const handleMouseUp = useCallback(() => {
    selectionJustProcessed.current = false;
    if (!interactive) return;

    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || !selection.rangeCount) return;

    const range = selection.getRangeAt(0);
    if (!canvasRef.current?.contains(range.startContainer)) return;

    // Walk text nodes to compute character offset
    const startOffset = getCharOffset(canvasRef.current, range.startContainer, range.startOffset);
    const endOffset = getCharOffset(canvasRef.current, range.endContainer, range.endOffset);

    if (startOffset === null || endOffset === null || startOffset >= endOffset) {
      selection.removeAllRanges();
      return;
    }

    // Allow full containment (wrappers/inners), reject partial overlaps
    if (hasOverlap(annotations, startOffset, endOffset)) {
      selection.removeAllRanges();
      return;
    }

    const selectedText = text.slice(startOffset, endOffset);
    const rect = range.getBoundingClientRect();

    selection.removeAllRanges();
    selectionJustProcessed.current = true;
    onSelect(startOffset, endOffset, rect, selectedText);
  }, [interactive, annotations, text, onSelect]);

  const handleAnnotationClickGuarded = useCallback((annotation: Annotation) => {
    // Suppress click if a text selection was just processed by handleMouseUp,
    // otherwise the edit popover overwrites the new-annotation popover.
    if (selectionJustProcessed.current) {
      selectionJustProcessed.current = false;
      return;
    }
    onAnnotationClick(annotation);
  }, [onAnnotationClick]);

  const segments = buildSegments(text, annotations);
  const wrappers = annotations.filter(a => isWrapperAnnotation(a, annotations));

  // Measure actual DOM positions of segments to align wrapper bars.
  // Uses a ref to avoid re-render loops; only triggers forceUpdate when positions change.
  useLayoutEffect(() => {
    if (!wrappers.length || !canvasRef.current) {
      if (Object.keys(wrapperPositionsRef.current).length > 0) {
        wrapperPositionsRef.current = {};
        forceUpdate();
      }
      return;
    }

    const canvas = canvasRef.current;
    // Measure relative to the wrappers container (the positioning context for
    // absolutely-positioned wrapper bars), not the canvas. The canvas has padding
    // and the wrappers container has margin to visually align — measuring from the
    // canvas would double-count the horizontal offset.
    const wrappersEl = canvas.nextElementSibling as HTMLElement | null;
    const refRect = wrappersEl?.getBoundingClientRect() ?? canvas.getBoundingClientRect();

    // Build a map from character offset to { textNode, offsetInNode } for Range API measurement.
    // Walk text nodes, skipping variable labels (visual only).
    const charMap: { node: Text; offsetInNode: number }[] = [];
    const walker = document.createTreeWalker(canvas, NodeFilter.SHOW_TEXT);
    let textNode = walker.nextNode() as Text | null;
    while (textNode) {
      const isLabel = textNode.parentElement?.classList.contains('pb-canvas-var-label');
      if (!isLabel) {
        // Skip whitespace-only text nodes inside annotation spans (JSX artifacts)
        const nodeText = textNode.textContent || '';
        const isWhitespaceArtifact = nodeText.trim() === '' &&
          textNode.parentElement?.classList.contains('pb-canvas-annotation');
        if (!isWhitespaceArtifact) {
          for (let i = 0; i < textNode.length; i++) {
            charMap.push({ node: textNode, offsetInNode: i });
          }
        }
      }
      textNode = walker.nextNode() as Text | null;
    }
    // Add end sentinel for measuring the end of the last character
    if (charMap.length > 0) {
      const last = charMap[charMap.length - 1];
      charMap.push({ node: last.node, offsetInNode: last.offsetInNode + 1 });
    }

    const newPositions: Record<string, WrapperPos> = {};

    for (const ann of wrappers) {
      if (ann.start >= charMap.length || ann.end > charMap.length) continue;

      const startEntry = charMap[ann.start];
      const endEntry = charMap[ann.end];
      if (!startEntry || !endEntry) continue;

      try {
        const range = document.createRange();
        range.setStart(startEntry.node, startEntry.offsetInNode);
        range.setEnd(endEntry.node, endEntry.offsetInNode);
        const rect = range.getBoundingClientRect();
        range.detach();

        if (rect.width > 0) {
          newPositions[ann.variableName] = {
            left: rect.left - refRect.left,
            width: rect.width,
          };
        }
      } catch {
        // Range API can fail if nodes were removed; skip
      }
    }

    // Only update if positions actually changed
    const prev = wrapperPositionsRef.current;
    const changed = Object.keys(newPositions).length !== Object.keys(prev).length ||
      Object.entries(newPositions).some(([k, v]) =>
        !prev[k] || Math.abs(prev[k].left - v.left) > 0.5 || Math.abs(prev[k].width - v.width) > 0.5
      );

    if (changed) {
      wrapperPositionsRef.current = newPositions;
      forceUpdate();
    }
  });

  return (
    <div className="pb-canvas-container">
      <div
        className={`pb-canvas${interactive ? ' pb-canvas-interactive' : ''}`}
        ref={canvasRef}
        onMouseUp={handleMouseUp}
      >
        {segments.map((seg, i) => {
          if (seg.type === 'annotation' && seg.annotation) {
            const color = getVariableColor(seg.annotation.variableName);
            return (
              <span
                key={i}
                className="pb-canvas-annotation"
                data-char-start={seg.start}
                data-char-end={seg.end}
                style={{
                  backgroundColor: color.bg,
                  borderBottom: `2px solid ${color.full}`,
                }}
                title={`${seg.annotation.variableName} (${seg.annotation.variableType})`}
                onClick={interactive ? () => handleAnnotationClickGuarded(seg.annotation!) : undefined}
              >{seg.content}<span className="pb-canvas-var-label" style={{ color: color.full }}>{seg.annotation.variableName}</span></span>
            );
          }
          return (
            <span
              key={i}
              className="pb-canvas-text"
              data-char-start={seg.start}
              data-char-end={seg.end}
            >
              {seg.content}
            </span>
          );
        })}
        {!text && (
          <span className="pb-canvas-placeholder">Add an example above to get started</span>
        )}
      </div>
      {wrappers.length > 0 && (
        <div className="pb-canvas-wrappers">
          {wrappers.map(ann => {
            const color = getVariableColor(ann.variableName);
            const pos = wrapperPositionsRef.current[ann.variableName];
            // Hide until measured
            if (!pos) return null;
            return (
              <div
                key={ann.variableName}
                className="pb-canvas-wrapper-bar"
                style={{
                  left: `${pos.left}px`,
                  width: `${pos.width}px`,
                  borderColor: color.full,
                  backgroundColor: color.bg,
                }}
                title={`${ann.variableName}: ${text.slice(ann.start, ann.end)}`}
                onClick={interactive ? () => handleAnnotationClickGuarded(ann) : undefined}
              >
                <span className="pb-canvas-wrapper-label" style={{ color: color.full }}>
                  {ann.variableName}
                </span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
});

/**
 * Walk text nodes in container to compute the character offset of a
 * position within a text node relative to the entire container's text.
 */
function getCharOffset(container: HTMLElement, node: Node, offset: number): number | null {
  let charCount = 0;
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
  let current = walker.nextNode();

  while (current) {
    // Skip text nodes inside variable labels — they're visual only
    const isLabel = (current as Text).parentElement?.classList.contains('pb-canvas-var-label');
    if (current === node) {
      return isLabel ? null : charCount + offset;
    }
    if (!isLabel) {
      const nodeText = (current as Text).textContent || '';
      const isWhitespaceArtifact = nodeText.trim() === '' &&
        (current as Text).parentElement?.classList.contains('pb-canvas-annotation');
      if (!isWhitespaceArtifact) {
        charCount += (current as Text).length;
      }
    }
    current = walker.nextNode();
  }

  return null;
}
