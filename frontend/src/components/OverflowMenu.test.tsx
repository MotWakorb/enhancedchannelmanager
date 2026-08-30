/**
 * Unit tests for OverflowMenu — the generic kebab used by the shared
 * header/toolbar overflow policy (bead 09x38.2).
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { OverflowMenu } from './OverflowMenu';
import type { OverflowMenuItem } from './OverflowMenu';

function items(overrides: Partial<OverflowMenuItem>[] = []): OverflowMenuItem[] {
  const base: OverflowMenuItem[] = [
    { label: 'First', icon: 'star', onClick: vi.fn() },
    { label: 'Second', icon: 'link', onClick: vi.fn() },
  ];
  return base.map((item, i) => ({ ...item, ...overrides[i] }));
}

describe('OverflowMenu', () => {
  it('renders only the trigger until opened', () => {
    render(<OverflowMenu items={items()} label="More actions" />);

    expect(screen.getByRole('button', { name: /more actions/i })).toBeInTheDocument();
    expect(screen.queryByRole('menuitem')).not.toBeInTheDocument();
  });

  it('opens on click to reveal every item', () => {
    render(<OverflowMenu items={items()} />);

    fireEvent.click(screen.getByRole('button', { name: /more actions/i }));

    expect(screen.getByRole('menuitem', { name: /first/i })).toBeInTheDocument();
    expect(screen.getByRole('menuitem', { name: /second/i })).toBeInTheDocument();
  });

  it('invokes the item onClick and closes the menu on selection', () => {
    const onFirst = vi.fn();
    render(<OverflowMenu items={items([{ onClick: onFirst }])} />);

    fireEvent.click(screen.getByRole('button', { name: /more actions/i }));
    fireEvent.click(screen.getByRole('menuitem', { name: /first/i }));

    expect(onFirst).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('menuitem')).not.toBeInTheDocument();
  });

  it('does not fire onClick for a disabled item', () => {
    const onFirst = vi.fn();
    render(<OverflowMenu items={items([{ onClick: onFirst, disabled: true }])} />);

    fireEvent.click(screen.getByRole('button', { name: /more actions/i }));
    fireEvent.click(screen.getByRole('menuitem', { name: /first/i }));

    expect(onFirst).not.toHaveBeenCalled();
  });

  it('toggles closed when the trigger is clicked again', () => {
    render(<OverflowMenu items={items()} />);

    const trigger = screen.getByRole('button', { name: /more actions/i });
    fireEvent.click(trigger);
    expect(screen.getByRole('menuitem', { name: /first/i })).toBeInTheDocument();

    fireEvent.click(trigger);
    expect(screen.queryByRole('menuitem')).not.toBeInTheDocument();
  });

  it('supports arrow navigation, Escape, and trigger focus return', () => {
    render(<OverflowMenu items={items()} label="More actions" />);
    const trigger = screen.getByRole('button', { name: /more actions/i });

    fireEvent.keyDown(trigger, { key: 'ArrowDown' });
    expect(screen.getByRole('menuitem', { name: /first/i })).toHaveFocus();
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'End' });
    expect(screen.getByRole('menuitem', { name: /second/i })).toHaveFocus();
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'ArrowDown' });
    expect(screen.getByRole('menuitem', { name: /first/i })).toHaveFocus();
    fireEvent.keyDown(screen.getByRole('menu'), { key: 'Escape' });

    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  // bead 09x38.15 item 9: the Stats section jump nav reuses this component
  // with a non-kebab trigger icon (e.g. "list") instead of the default
  // "more_vert", so other consumers can signal a different affordance
  // (navigation vs. actions) without a bespoke dropdown.
  it('defaults the trigger icon to the kebab, and renders a custom icon when given one', () => {
    const { rerender } = render(<OverflowMenu items={items()} label="More actions" />);
    expect(screen.getByRole('button', { name: /more actions/i }).textContent).toContain('more_vert');

    rerender(<OverflowMenu items={items()} label="Jump to section" icon="list" />);
    const trigger = screen.getByRole('button', { name: /jump to section/i });
    expect(trigger.textContent).toContain('list');
    expect(trigger.textContent).not.toContain('more_vert');
  });
});
