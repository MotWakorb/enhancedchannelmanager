/**
 * Unit tests for DeleteOrphanedGroupsModal — Cancel button (bead
 * enhancedchannelmanager-09x38.3 audit follow-up). Footer had only the
 * mutating "Delete N Group(s)" danger button — header X-close was the only
 * escape hatch. Add a Cancel secondary so the footer follows the documented
 * Cancel+Primary pattern.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { DeleteOrphanedGroupsModal } from './DeleteOrphanedGroupsModal';

const GROUPS = [
  { id: 1, name: 'Orphan Group A', reason: 'no streams' },
  { id: 2, name: 'Orphan Group B' },
];

const BASE_PROPS = {
  isOpen: true,
  onClose: vi.fn(),
  onConfirm: vi.fn(),
  groups: GROUPS,
};

describe('DeleteOrphanedGroupsModal — Cancel secondary button', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders a Cancel button alongside the Delete danger button', () => {
    render(<DeleteOrphanedGroupsModal {...BASE_PROPS} />);

    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Delete 2 Groups/ })).toBeInTheDocument();
  });

  it('clicking Cancel closes the modal without calling onConfirm', () => {
    const onClose = vi.fn();
    const onConfirm = vi.fn();
    render(<DeleteOrphanedGroupsModal {...BASE_PROPS} onClose={onClose} onConfirm={onConfirm} />);

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });
});
