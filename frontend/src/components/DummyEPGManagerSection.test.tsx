/**
 * Unit tests for DummyEPGManagerSection — section retitle (bead
 * enhancedchannelmanager-09x38.4).
 *
 * PO DECISION #2 (Option B): this is now THE dummy EPG feature, so the section
 * drops the "ECM" qualifier and is titled simply "Dummy EPG Profiles".
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { DummyEPGManagerSection } from './DummyEPGManagerSection';

vi.mock('../services/api', () => ({
  getDummyEPGProfiles: vi.fn().mockResolvedValue([]),
}));

vi.mock('./DummyEPGProfileModal', () => ({
  DummyEPGProfileModal: () => null,
}));
vi.mock('./ImportDummyEPGModal', () => ({
  ImportDummyEPGModal: () => null,
}));

vi.mock('../contexts/NotificationContext', () => ({
  useNotifications: () => ({
    success: vi.fn(),
    warning: vi.fn(),
    error: vi.fn(),
  }),
}));

describe('DummyEPGManagerSection — title', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('titles the section "Dummy EPG Profiles" without the ECM qualifier', async () => {
    render(<DummyEPGManagerSection />);

    expect(
      await screen.findByRole('heading', { name: 'Dummy EPG Profiles' })
    ).toBeInTheDocument();
    expect(screen.queryByText('ECM Dummy EPG Profiles')).not.toBeInTheDocument();
  });

  it('uses non-ECM copy in the empty state', async () => {
    render(<DummyEPGManagerSection />);

    expect(
      await screen.findByText(/No Dummy EPG profiles\./i)
    ).toBeInTheDocument();
    expect(screen.queryByText(/No ECM Dummy EPG profiles/i)).not.toBeInTheDocument();
  });
});
