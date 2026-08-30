/**
 * The persistent "this is restored work" marker in the Edit Mode header
 * (epic enhancedchannelmanager-r93hq).
 *
 * The restore dialog is the loud signal, but it is a moment. An operator who
 * restores, gets pulled away, and comes back to a header reading "12 changes"
 * has no way to tell staged work they made from staged work that survived a
 * dead session — and the difference decides whether they trust Apply. This
 * badge is the standing answer, and it is why `restoredFrom` is carried on the
 * Edit Mode state at all.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { EditModeRestoredBadge } from './EditModeRestoredBadge';

const SAVED_AT = new Date('2026-08-16T09:30:00Z').getTime();

describe('EditModeRestoredBadge', () => {
  it('renders nothing for work staged in this session', () => {
    render(<EditModeRestoredBadge restoredFrom={null} />);
    expect(screen.queryByTestId('edit-mode-restored-badge')).toBeNull();
  });

  it('says the staged work was restored, and when it was staged', () => {
    render(<EditModeRestoredBadge restoredFrom={SAVED_AT} />);
    const badge = screen.getByTestId('edit-mode-restored-badge');
    expect(badge.textContent).toMatch(/restored/i);
    expect(badge.getAttribute('title')).toContain(new Date(SAVED_AT).toLocaleString());
  });
});
