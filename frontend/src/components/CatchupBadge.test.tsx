/**
 * Tests for <CatchupBadge> (bead enhancedchannelmanager-sy1sz).
 *
 * Contracts under test (the four edge cases from the pinned bead contract):
 *   (a) is_catchup:true,  catchup_days:7 → badge + "Catch-up: 7 days" tooltip
 *   (b) is_catchup:true,  catchup_days:0 → badge + "Catch-up available" (never "0 days")
 *   (c) is_catchup:false, catchup_days:5 → NO badge (flag is authoritative)
 *   (d) fields absent                    → NO badge
 * Plus: the replay glyph, singular "1 day", and className pass-through.
 */
import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { CatchupBadge } from './CatchupBadge';

describe('CatchupBadge', () => {
  // --- (a) supported, with days ---

  it('renders the badge with a "Catch-up: 7 days" tooltip for (true, 7)', () => {
    const { container } = render(<CatchupBadge isCatchup={true} catchupDays={7} />);
    const badge = container.querySelector('.catchup-badge');
    expect(badge).toBeInTheDocument();
    expect(badge?.getAttribute('title')).toBe('Catch-up: 7 days');
    expect(badge?.getAttribute('aria-label')).toBe('Catch-up: 7 days');
  });

  it('renders the replay icon glyph for a supported badge', () => {
    const { container } = render(<CatchupBadge isCatchup={true} catchupDays={7} />);
    const icon = container.querySelector('.catchup-badge__icon');
    expect(icon?.textContent).toBe('replay');
  });

  it('renders a compact day count in the visible label for (true, 7)', () => {
    const { container } = render(<CatchupBadge isCatchup={true} catchupDays={7} />);
    expect(container.querySelector('.catchup-badge__text')?.textContent).toBe('7d');
  });

  it('uses singular "1 day" in the tooltip for (true, 1)', () => {
    const { container } = render(<CatchupBadge isCatchup={true} catchupDays={1} />);
    expect(container.querySelector('.catchup-badge')?.getAttribute('title')).toBe('Catch-up: 1 day');
  });

  // --- (b) supported, zero/absent days ---

  it('renders "Catch-up available" (not "0 days") for (true, 0)', () => {
    const { container } = render(<CatchupBadge isCatchup={true} catchupDays={0} />);
    const badge = container.querySelector('.catchup-badge');
    expect(badge).toBeInTheDocument();
    expect(badge?.getAttribute('title')).toBe('Catch-up available');
    expect(badge?.textContent).not.toContain('0');
    expect(container.querySelector('.catchup-badge__text')?.textContent).toBe('Catch-up');
  });

  it('renders "Catch-up available" when the flag is true but days is absent', () => {
    const { container } = render(<CatchupBadge isCatchup={true} />);
    expect(container.querySelector('.catchup-badge')?.getAttribute('title')).toBe('Catch-up available');
  });

  // --- (c) flag false with days present → no badge (flag wins) ---

  it('renders NOTHING for (false, 5) — the flag is authoritative', () => {
    const { container } = render(<CatchupBadge isCatchup={false} catchupDays={5} />);
    expect(container.querySelector('.catchup-badge')).not.toBeInTheDocument();
    expect(container).toBeEmptyDOMElement();
  });

  // --- (d) fields absent → no badge ---

  it('renders NOTHING when both fields are absent', () => {
    const { container } = render(<CatchupBadge />);
    expect(container.querySelector('.catchup-badge')).not.toBeInTheDocument();
    expect(container).toBeEmptyDOMElement();
  });

  // --- className pass-through ---

  it('appends additional className to the outer span', () => {
    const { container } = render(<CatchupBadge isCatchup={true} catchupDays={7} className="my-extra-class" />);
    expect(container.querySelector('.catchup-badge.my-extra-class')).toBeInTheDocument();
  });
});
