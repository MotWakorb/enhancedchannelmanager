/**
 * The point-of-action notice on the two operations Edit Mode cannot stage
 * (bead enhancedchannelmanager-kz089).
 *
 * Merge and Import CSV are the PO's accepted staging exceptions: a merge
 * reconciles records across providers and would need server-side support to be
 * representable as a reversible diff, and that work is explicitly out of scope.
 * The defect was never that they are immediate. It was that Edit Mode said
 * nothing while they were, and said the opposite two clicks away, in a Bulk
 * Delete dialog promising changes could be undone in edit mode. An operator who
 * merged twenty channels and hit Discard had lost the originals.
 *
 * So each of the three entry points (toolbar Merge, the merge inside Find
 * Duplicates, Import CSV) must state before it will run that it applies
 * immediately and cannot be discarded, and must not run until that is
 * acknowledged.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MergeChannelsModal } from './MergeChannelsModal';
import { FindDuplicatesModal } from './FindDuplicatesModal';
import { CSVImportModal } from './CSVImportModal';
import * as api from '../services/api';
import type { Channel } from '../types';

function makeChannel(id: number, name: string): Channel {
  return {
    id,
    channel_number: id,
    name,
    channel_group_id: null,
    tvg_id: null,
    tvc_guide_stationid: null,
    epg_data_id: null,
    streams: [],
    stream_profile_id: null,
    uuid: `uuid-${id}`,
    logo_id: null,
    auto_created: false,
    auto_created_by: null,
    auto_created_by_name: null,
  };
}

const MERGE_CHANNELS = [makeChannel(1, 'Alpha'), makeChannel(2, 'Alpha HD')];

function renderMergeModal(isEditMode: boolean) {
  return render(
    <MergeChannelsModal
      isEditMode={isEditMode}
      channels={MERGE_CHANNELS}
      logos={[]}
      epgData={[]}
      epgSources={[]}
      channelGroups={[]}
      streamProfiles={[]}
      streams={[]}
      onClose={vi.fn()}
      onMerged={vi.fn()}
    />,
  );
}

const NOTICE = 'irreversible-action-notice';

afterEach(() => {
  vi.restoreAllMocks();
});

describe('toolbar Merge', () => {
  beforeEach(() => {
    vi.spyOn(api, 'mergeChannels').mockResolvedValue(makeChannel(3, 'Alpha'));
  });

  it('says the merge applies immediately and cannot be discarded', () => {
    renderMergeModal(true);

    const notice = screen.getByTestId(NOTICE);
    expect(notice).toHaveTextContent(/applies immediately and is NOT staged/i);
    expect(notice).toHaveTextContent(/cannot be undone by Discard, Cancel or Undo/i);
  });

  it('keeps Merge dead until the operator acknowledges', async () => {
    const user = userEvent.setup();
    renderMergeModal(true);

    const merge = screen.getByRole('button', { name: /Merge 2 Channels/ });
    expect(merge).toBeDisabled();

    await user.click(screen.getByRole('checkbox', { name: /cannot be discarded/i }));

    expect(merge).toBeEnabled();
    await user.click(merge);
    await waitFor(() => expect(api.mergeChannels).toHaveBeenCalled());
  });

  it('shows no notice outside Edit Mode, where there is no promise to correct', () => {
    renderMergeModal(false);

    expect(screen.queryByTestId(NOTICE)).toBeNull();
    expect(screen.getByRole('button', { name: /Merge 2 Channels/ })).toBeEnabled();
  });
});

describe('the merge inside Find Duplicates', () => {
  beforeEach(() => {
    vi.spyOn(api, 'findDuplicateChannels').mockResolvedValue({
      groups: [
        {
          match_key: 'alpha',
          channels: [
            { id: 1, name: 'Alpha', channel_number: 1, stream_count: 1, channel_group_id: null, logo_id: null },
            { id: 2, name: 'Alpha HD', channel_number: 2, stream_count: 1, channel_group_id: null, logo_id: null },
          ],
        },
      ],
      total_duplicates: 1,
    } as unknown as Awaited<ReturnType<typeof api.findDuplicateChannels>>);
  });

  it('carries the same notice as the toolbar Merge button', async () => {
    render(<FindDuplicatesModal isEditMode onClose={vi.fn()} onMerged={vi.fn()} />);

    const notice = await screen.findByTestId(NOTICE);
    expect(notice).toHaveTextContent(/applies immediately and is NOT staged/i);
  });

  it('keeps its Merge button dead until acknowledged', async () => {
    const user = userEvent.setup();
    render(<FindDuplicatesModal isEditMode onClose={vi.fn()} onMerged={vi.fn()} />);

    const merge = await screen.findByRole('button', { name: /Merge 1 Group/ });
    expect(merge).toBeDisabled();

    await user.click(screen.getByRole('checkbox', { name: /cannot be discarded/i }));
    expect(merge).toBeEnabled();
  });
});

describe('Import CSV', () => {
  it('says the import applies immediately, and gates the button on it', async () => {
    const user = userEvent.setup();
    vi.spyOn(api, 'parseCSVPreview').mockResolvedValue({
      rows: [{ name: 'Alpha', channel_number: 1, group_name: 'Sports', tvg_id: '' }],
      errors: [],
    } as unknown as Awaited<ReturnType<typeof api.parseCSVPreview>>);

    const { container } = render(
      <CSVImportModal isOpen onClose={vi.fn()} onSuccess={vi.fn()} />,
    );

    const file = new File(['name,channel_number\nAlpha,1\n'], 'channels.csv', { type: 'text/csv' });
    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    await user.upload(input, file);

    const submit = await screen.findByTestId('csv-import-submit');
    await waitFor(() => expect(submit).toBeDisabled());

    expect(screen.getByTestId(NOTICE)).toHaveTextContent(/applies immediately and is NOT staged/i);

    await user.click(screen.getByRole('checkbox', { name: /cannot be discarded/i }));
    expect(submit).toBeEnabled();
  });
});
