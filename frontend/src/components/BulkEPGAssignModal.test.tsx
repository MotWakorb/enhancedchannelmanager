/**
 * Unit tests for BulkEPGAssignModal — Cancel button (bead
 * enhancedchannelmanager-09x38.3 audit follow-up). Footer had only a single
 * mutating primary button per wizard phase ("Match N Channels" / "Assign N
 * Channels") — header X-close was the only escape hatch. Add a Cancel
 * secondary so the footer follows the documented Cancel+Primary pattern.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { BulkEPGAssignModal } from './BulkEPGAssignModal';
import type { Channel, EPGData, EPGSource } from '../types';

vi.mock('../services/api', () => ({
  matchChannelsToEPG: vi.fn(),
}));

import * as api from '../services/api';

const CHANNELS: Channel[] = [];
const EPG_DATA: EPGData[] = [];
const EPG_SOURCES: EPGSource[] = [];

const BASE_PROPS = {
  isOpen: true,
  selectedChannels: CHANNELS,
  epgData: EPG_DATA,
  epgSources: EPG_SOURCES,
  onClose: vi.fn(),
  onAssign: vi.fn(),
};

describe('BulkEPGAssignModal — Cancel secondary button', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders a Cancel button in the configure-phase footer', () => {
    render(<BulkEPGAssignModal {...BASE_PROPS} />);

    expect(screen.getByRole('button', { name: 'Cancel' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Match \d+ Channel/ })).toBeInTheDocument();
  });

  it('clicking Cancel closes the modal without calling matchChannelsToEPG or onAssign', () => {
    const onClose = vi.fn();
    const onAssign = vi.fn();
    render(<BulkEPGAssignModal {...BASE_PROPS} onClose={onClose} onAssign={onAssign} />);

    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(onAssign).not.toHaveBeenCalled();
    expect(api.matchChannelsToEPG).not.toHaveBeenCalled();
  });
});
