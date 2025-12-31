import { useState, useRef, useCallback, useEffect, ReactNode } from 'react';
import './SplitPane.css';

interface SplitPaneProps {
  left: ReactNode;
  right: ReactNode;
  defaultLeftWidth?: number; // percentage (0-100)
  minLeftWidth?: number; // percentage
  maxLeftWidth?: number; // percentage
}

export function SplitPane({
  left,
  right,
  defaultLeftWidth = 50,
  minLeftWidth = 20,
  maxLeftWidth = 80,
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
    <div ref={containerRef} className="split-pane">
      <div className="split-pane-left" style={{ width: `${leftWidth}%` }}>
        {left}
      </div>
      <div
        className={`split-pane-divider ${isDragging ? 'dragging' : ''}`}
        onMouseDown={handleMouseDown}
      >
        <div className="divider-handle" />
      </div>
      <div className="split-pane-right" style={{ width: `${100 - leftWidth}%` }}>
        {right}
      </div>
    </div>
  );
}
