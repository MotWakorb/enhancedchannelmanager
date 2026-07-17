/**
 * Unit tests for BulkLCNFetchModal — Cancel button (bead enhancedchannelmanager-09x38.3
 * audit follow-up). Footer had only the mutating "Assign N Gracenote ID(s)"
 * primary button — header X-close was the only escape hatch. Add a Cancel
 * secondary so the footer follows the documented Cancel+Primary pattern.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen, fireEvent, waitFor } from '@testing-library/react';
import { renderWithProviders as render } from '../test/utils/renderWithProviders';
import { BulkLCNFetchModal } from './BulkLCNFetchModal';
import type { Channel, EPGData } from '../types';

vi.mock('../services/api', () => ({
  getEPGLcnBatch: vi.fn(),
}));

import * as api from '../services/api';

const CHANNELS: Channel[] = [];
const EPG_DATA: EPGData[] = [];

const BASE_PROPS = {
  isOpen: true,
  selectedChannels: CHANNELS,
  epgData: EPG_DATA,
  onClose: vi.fn(),
  onAssign: vi.fn(),
};

describe('BulkLCNFetchModal — Cancel secondary button', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders a Cancel button in the footer', async () => {
    render(<BulkLCNFetchModal {...BASE_PROPS} />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
    });
  });

  it('clicking Cancel closes the modal without calling onAssign', async () => {
    const onClose = vi.fn();
    const onAssign = vi.fn();
    render(<BulkLCNFetchModal {...BASE_PROPS} onClose={onClose} onAssign={onAssign} />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onAssign).not.toHaveBeenCalled();
    expect(api.getEPGLcnBatch).not.toHaveBeenCalled();
  });
});
