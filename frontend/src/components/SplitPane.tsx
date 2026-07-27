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
  const [leftWidth, setLeftWidth] = useState(defaultLeftWidth);
  const [isDragging, setIsDragging] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    setIsDragging(true);
  }, []);

  const handleMouseMove = useCallback(
    (e: MouseEvent) => {
      if (!isDragging || !containerRef.current) return;

      const container = containerRef.current;
      const containerRect = container.getBoundingClientRect();
      const newLeftWidth = ((e.clientX - containerRect.left) / containerRect.width) * 100;

      // Clamp to min/max bounds
      const clampedWidth = Math.min(Math.max(newLeftWidth, minLeftWidth), maxLeftWidth);
      setLeftWidth(clampedWidth);
    },
    [isDragging, minLeftWidth, maxLeftWidth]
  );

  const handleMouseUp = useCallback(() => {
    setIsDragging(false);
  }, []);

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
    if (isDragging) {
      document.addEventListener('mousemove', handleMouseMove);
      document.addEventListener('mouseup', handleMouseUp);
      document.body.style.cursor = 'col-resize';
      document.body.style.userSelect = 'none';
    }

    return () => {
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
  }, [isDragging, handleMouseMove, handleMouseUp]);

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
        className={`split-pane-divider ${isDragging ? 'dragging' : ''}`}
        onMouseDown={handleMouseDown}
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
