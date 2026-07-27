import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SplitPane } from './SplitPane';

function renderPane(props: Partial<React.ComponentProps<typeof SplitPane>> = {}) {
  const result = render(
    <SplitPane
      left={<div>Channels content</div>}
      right={<div>Streams content</div>}
      leftLabel="Channels"
      rightLabel="Streams"
      {...props}
    />,
  );
  const separator = screen.getByRole('separator', { name: 'Resize Channels and Streams panes' });
  const container = separator.parentElement as HTMLDivElement;
  vi.spyOn(container, 'getBoundingClientRect').mockReturnValue({
    left: 100, right: 1100, top: 0, bottom: 600, width: 1000, height: 600, x: 100, y: 0,
    toJSON: () => ({}),
  });
  separator.setPointerCapture = vi.fn();
  separator.releasePointerCapture = vi.fn();
  separator.hasPointerCapture = vi.fn(() => true);
  return { ...result, separator, container };
}

afterEach(() => {
  document.body.style.cursor = '';
  document.body.style.userSelect = '';
});

describe('SplitPane operator workspace', () => {
  it('clamps the one-time uncontrolled default and intentionally ignores later default changes', () => {
    const { rerender, separator } = renderPane({ defaultLeftWidth: 99 });
    expect(separator).toHaveAttribute('aria-valuenow', '70');
    rerender(
      <SplitPane
        left={<div>Channels content</div>}
        right={<div>Streams content</div>}
        leftLabel="Channels"
        rightLabel="Streams"
        defaultLeftWidth={36}
      />,
    );
    expect(separator).toHaveAttribute('aria-valuenow', '70');
  });

  it('uses exact pointer drag math and clamps below and above bounds', () => {
    const { separator } = renderPane();
    fireEvent.pointerDown(separator, { pointerId: 4, clientX: 680, pointerType: 'touch' });
    expect(separator.setPointerCapture).toHaveBeenCalledWith(4);
    fireEvent.pointerMove(separator, { pointerId: 4, clientX: 600 });
    expect(separator).toHaveAttribute('aria-valuenow', '50');
    fireEvent.pointerMove(separator, { pointerId: 4, clientX: 0 });
    expect(separator).toHaveAttribute('aria-valuenow', '35');
    fireEvent.pointerMove(separator, { pointerId: 4, clientX: 1200 });
    expect(separator).toHaveAttribute('aria-valuenow', '70');
    fireEvent.pointerUp(separator, { pointerId: 4 });
    expect(separator.releasePointerCapture).toHaveBeenCalledWith(4);
  });

  it.each(['pointerCancel', 'lostPointerCapture'] as const)(
    'cleans up drag state on %s and restores prior body styles',
    (eventName) => {
      document.body.style.cursor = 'wait';
      document.body.style.userSelect = 'text';
      const { separator } = renderPane();
      fireEvent.pointerDown(separator, { pointerId: 8, clientX: 680, pointerType: 'pen' });
      expect(document.body.style.cursor).toBe('col-resize');
      fireEvent[eventName](separator, { pointerId: 8 });
      expect(document.body.style.cursor).toBe('wait');
      expect(document.body.style.userSelect).toBe('text');
      expect(separator).not.toHaveClass('dragging');
    },
  );

  it('cleans up on window blur and unmount without erasing prior body styles', () => {
    document.body.style.cursor = 'wait';
    document.body.style.userSelect = 'text';
    const first = renderPane();
    fireEvent.pointerDown(first.separator, { pointerId: 9, clientX: 680 });
    fireEvent.blur(window);
    expect(document.body.style.cursor).toBe('wait');
    expect(document.body.style.userSelect).toBe('text');
    first.unmount();

    const second = renderPane();
    fireEvent.pointerDown(second.separator, { pointerId: 10, clientX: 680 });
    second.unmount();
    expect(document.body.style.cursor).toBe('wait');
    expect(document.body.style.userSelect).toBe('text');
  });

  it('supports keyboard resizing while respecting both bounds', () => {
    const { separator } = renderPane();
    expect(separator).toHaveAttribute('tabindex', '0');
    fireEvent.keyDown(separator, { key: 'ArrowLeft' });
    expect(separator).toHaveAttribute('aria-valuenow', '56');
    fireEvent.keyDown(separator, { key: 'Home' });
    fireEvent.keyDown(separator, { key: 'ArrowLeft' });
    expect(separator).toHaveAttribute('aria-valuenow', '35');
    fireEvent.keyDown(separator, { key: 'End' });
    fireEvent.keyDown(separator, { key: 'ArrowRight' });
    expect(separator).toHaveAttribute('aria-valuenow', '70');
  });

  it('keeps named, min-width-safe pane wrappers free of width styles', () => {
    renderPane();
    expect(screen.getByRole('region', { name: 'Channels' })).not.toHaveStyle({ width: '58%' });
    expect(screen.getByRole('region', { name: 'Streams' })).not.toHaveStyle({ width: '42%' });
  });
});
