import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { EPGSource } from '../types';
import * as api from '../services/api';
import { GuideMigrationModal } from './GuideMigrationModal';

vi.mock('../services/api', () => ({
  previewGuideMigration: vi.fn(),
  applyGuideMigration: vi.fn(),
}));

const sources = [
  { id: 1, name: 'IPTV', source_type: 'xmltv' },
  { id: 2, name: 'Gracenote', source_type: 'schedules_direct' },
] as EPGSource[];

const preview: api.GuideMigrationPreview = {
  target_source_id: 2,
  target_source_name: 'Gracenote',
  preview_token: 'signed',
  counts: {
    ready: 1,
    already_target: 0,
    unassigned: 0,
    missing_lcn: 0,
    missing_target: 1,
    ambiguous_target: 0,
    unsupported_origin: 0,
  },
  rows: [
    {
      channel_id: 7,
      channel_name: 'News',
      current_epg_data_id: 11,
      current_source_id: 1,
      current_source_name: 'IPTV',
      lcn: '10101',
      target_epg_data_id: 22,
      target_name: 'News SD',
      current_tvg_id: 'iptv.news',
      target_tvg_id: '10101',
      status: 'ready',
    },
    {
      channel_id: 8,
      channel_name: 'Local',
      current_epg_data_id: 12,
      current_source_id: 1,
      current_source_name: 'IPTV',
      lcn: '20202',
      target_epg_data_id: null,
      target_name: null,
      current_tvg_id: 'iptv.local',
      target_tvg_id: null,
      status: 'missing_target',
    },
  ],
};

describe('GuideMigrationModal', () => {
  beforeEach(() => vi.clearAllMocks());

  it('requires a target, preview, and explicit confirmation before apply', async () => {
    vi.mocked(api.previewGuideMigration).mockResolvedValue(preview);
    vi.mocked(api.applyGuideMigration).mockResolvedValue({
      mutated: 1,
      updated: 1,
      audit_failed: 0,
      skipped: 0,
      failed: 0,
      results: [{ channel_id: 7, status: 'updated' }],
      batch_id: 'batch01',
    });
    const onApplied = vi.fn();
    const onClose = vi.fn();
    render(
      <GuideMigrationModal
        isOpen
        sources={sources}
        onClose={onClose}
        onApplied={onApplied}
      />
    );

    const dialog = screen.getByRole('dialog', { name: 'Migrate channel guides' });
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    await waitFor(() =>
      expect(screen.getByLabelText('Target EPG source')).toHaveFocus()
    );
    const previewButton = screen.getByRole('button', { name: 'Preview migration' });
    expect(previewButton).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Target EPG source'), {
      target: { value: '2' },
    });
    fireEvent.click(previewButton);
    expect(await screen.findByText('News SD')).toBeInTheDocument();
    expect(screen.getByText('No target match: 1')).toBeInTheDocument();

    const applyButton = screen.getByRole('button', { name: 'Apply 1 migration' });
    expect(applyButton).toBeDisabled();
    fireEvent.click(
      screen.getByLabelText('Change guide assignments for exactly 1 ready channel.')
    );
    fireEvent.click(applyButton);

    await waitFor(() => expect(api.applyGuideMigration).toHaveBeenCalledWith(preview));
    expect(onApplied).toHaveBeenCalledWith(
      expect.objectContaining({ mutated: 1, updated: 1 })
    );
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByText('News: updated')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Done' }));
    expect(onClose).toHaveBeenCalled();
  });

  it('never offers apply when every row is unresolved', async () => {
    vi.mocked(api.previewGuideMigration).mockResolvedValue({
      ...preview,
      counts: { ...preview.counts, ready: 0, missing_target: 2 },
      rows: preview.rows.map((row) => ({
        ...row,
        status: 'missing_target' as const,
        target_epg_data_id: null,
        target_name: null,
      })),
    });
    render(
      <GuideMigrationModal
        isOpen
        sources={sources}
        onClose={vi.fn()}
        onApplied={vi.fn()}
      />
    );
    fireEvent.change(screen.getByLabelText('Target EPG source'), {
      target: { value: '2' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Preview migration' }));
    expect(await screen.findByText('Ready: 0')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Apply 0 migrations' })).toBeDisabled();
  });
});
