import { useState, useRef, useCallback, useEffect, ReactNode } from 'react';
import './SplitPane.css';

interface SplitPaneProps {
  left: ReactNode;
  right: ReactNode;
  leftLabel?: string;
  rightLabel?: string;
  defaultLeftWidth?: number; // percentage (0-100)
  minLeftWidth?: number; // percentage
  maxLeftWidth?: number; // percentage
}

export function SplitPane({
  left,
  right,
  leftLabel = 'Left pane',
  rightLabel = 'Right pane',
  defaultLeftWidth = 58,
  minLeftWidth = 35,
  maxLeftWidth = 70,
}: SplitPaneProps) {
  // `defaultLeftWidth` is intentionally an uncontrolled, mount-time default.
  // Callers do not currently need to drive the split after mount; pointer and
  // keyboard interaction own the value from this point forward.
  const [leftWidth, setLeftWidth] = useState(
    () => Math.min(Math.max(defaultLeftWidth, minLeftWidth), maxLeftWidth),
  );
  const [isDragging, setIsDragging] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const activePointerIdRef = useRef<number | null>(null);
  const dividerRef = useRef<HTMLDivElement>(null);
  const previousBodyStylesRef = useRef<{ cursor: string; userSelect: string } | null>(null);

  const finishDrag = useCallback((releaseCapture = true) => {
    const divider = dividerRef.current;
    const pointerId = activePointerIdRef.current;
    if (
      releaseCapture
      && divider
      && pointerId !== null
      && divider.hasPointerCapture?.(pointerId)
    ) {
      divider.releasePointerCapture(pointerId);
    }
    activePointerIdRef.current = null;
    setIsDragging(false);
    const previous = previousBodyStylesRef.current;
    if (previous) {
      document.body.style.cursor = previous.cursor;
      document.body.style.userSelect = previous.userSelect;
      previousBodyStylesRef.current = null;
    }
  }, []);

  const handlePointerDown = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    if (activePointerIdRef.current !== null) return;
    e.preventDefault();
    activePointerIdRef.current = e.pointerId;
    previousBodyStylesRef.current = {
      cursor: document.body.style.cursor,
      userSelect: document.body.style.userSelect,
    };
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    e.currentTarget.setPointerCapture(e.pointerId);
    setIsDragging(true);
  }, []);

  const handlePointerMove = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    if (activePointerIdRef.current !== e.pointerId || !containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    if (rect.width <= 0) return;
    const nextWidth = ((e.clientX - rect.left) / rect.width) * 100;
    setLeftWidth(Math.min(Math.max(nextWidth, minLeftWidth), maxLeftWidth));
  }, [maxLeftWidth, minLeftWidth]);

  const handlePointerEnd = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    if (activePointerIdRef.current !== e.pointerId) return;
    finishDrag(e.type !== 'lostpointercapture');
  }, [finishDrag]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent<HTMLDivElement>) => {
    let nextWidth: number | null = null;
    if (e.key === 'ArrowLeft') nextWidth = leftWidth - 2;
    if (e.key === 'ArrowRight') nextWidth = leftWidth + 2;
    if (e.key === 'Home') nextWidth = minLeftWidth;
    if (e.key === 'End') nextWidth = maxLeftWidth;
    if (nextWidth === null) return;
    e.preventDefault();
    setLeftWidth(Math.min(Math.max(nextWidth, minLeftWidth), maxLeftWidth));
  }, [leftWidth, maxLeftWidth, minLeftWidth]);

  useEffect(() => {
    const handleBlur = () => finishDrag();
    window.addEventListener('blur', handleBlur);
    return () => {
      window.removeEventListener('blur', handleBlur);
      finishDrag();
    };
  }, [finishDrag]);

  return (
    <div
      ref={containerRef}
      className="split-pane"
      style={{ '--split-pane-left': `${leftWidth}%` } as React.CSSProperties}
    >
      <section className="split-pane-left" aria-label={leftLabel}>
        {left}
      </section>
      <div
        ref={dividerRef}
        className={`split-pane-divider ${isDragging ? 'dragging' : ''}`}
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerEnd}
        onPointerCancel={handlePointerEnd}
        onLostPointerCapture={handlePointerEnd}
        onKeyDown={handleKeyDown}
        role="separator"
        tabIndex={0}
        aria-label={`Resize ${leftLabel} and ${rightLabel} panes`}
        aria-orientation="vertical"
        aria-valuemin={minLeftWidth}
        aria-valuemax={maxLeftWidth}
        aria-valuenow={Math.round(leftWidth)}
      >
        <div className="divider-handle" />
      </div>
      <section className="split-pane-right" aria-label={rightLabel}>
        {right}
      </section>
    </div>
  );
}
