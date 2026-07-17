/**
 * Unit tests for NormalizeNamesModal — Cancel button (bead
 * enhancedchannelmanager-09x38.3 audit follow-up). Footer had only the
 * mutating "Apply N Change(s)" primary button — header X-close was the only
 * escape hatch. Add a Cancel secondary so the footer follows the documented
 * Cancel+Primary pattern.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { NormalizeNamesModal } from './NormalizeNamesModal';

vi.mock('../services/api', () => ({
  normalizeTexts: vi.fn(),
}));

import * as api from '../services/api';

const CHANNELS = [
  { id: 1, name: 'espn  hd' },
  { id: 2, name: 'cnn   news' },
];

const BASE_PROPS = {
  channels: CHANNELS,
  onConfirm: vi.fn(),
  onCancel: vi.fn(),
};

describe('NormalizeNamesModal — Cancel secondary button', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(api.normalizeTexts).mockResolvedValue({
      results: [
        { normalized: 'ESPN HD' },
        { normalized: 'CNN News' },
      ],
    } as never);
  });

  it('renders a Cancel button alongside the primary Apply Changes button', async () => {
    render(<NormalizeNamesModal {...BASE_PROPS} />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Apply 2 Changes/ })).toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
  });

  it('clicking Cancel invokes onCancel without calling onConfirm', async () => {
    const onCancel = vi.fn();
    const onConfirm = vi.fn();
    render(<NormalizeNamesModal {...BASE_PROPS} onCancel={onCancel} onConfirm={onConfirm} />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });
});
