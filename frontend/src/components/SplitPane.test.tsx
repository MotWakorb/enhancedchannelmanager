import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { SplitPane } from './SplitPane';

describe('SplitPane operator workspace', () => {
  it('names both panes and exposes a keyboard-operable separator', () => {
    render(
      <SplitPane
        left={<div>Channels content</div>}
        right={<div>Streams content</div>}
        leftLabel="Channels"
        rightLabel="Streams"
      />,
    );

    expect(screen.getByRole('region', { name: 'Channels' })).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Streams' })).toBeInTheDocument();

    const separator = screen.getByRole('separator', { name: 'Resize Channels and Streams panes' });
    expect(separator).toHaveAttribute('tabindex', '0');
    expect(separator).toHaveAttribute('aria-valuenow', '58');

    fireEvent.keyDown(separator, { key: 'ArrowLeft' });
    expect(separator).toHaveAttribute('aria-valuenow', '56');
    fireEvent.keyDown(separator, { key: 'Home' });
    expect(separator).toHaveAttribute('aria-valuenow', '35');
    fireEvent.keyDown(separator, { key: 'End' });
    expect(separator).toHaveAttribute('aria-valuenow', '70');
  });

  it('uses min-width-safe pane wrappers without percentage width styles', () => {
    render(
      <SplitPane
        left={<div>Channels content</div>}
        right={<div>Streams content</div>}
        leftLabel="Channels"
        rightLabel="Streams"
      />,
    );

    expect(screen.getByRole('region', { name: 'Channels' })).not.toHaveStyle({ width: '58%' });
    expect(screen.getByRole('region', { name: 'Streams' })).not.toHaveStyle({ width: '42%' });
  });
});
