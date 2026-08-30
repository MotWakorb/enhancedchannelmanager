/**
 * A destructive action refuses at the HANDLER, not only at the button
 * (bead enhancedchannelmanager-kz089, fix round 2).
 *
 * Round 1 gave Merge (twice) and Import CSV an acknowledgement checkbox and held
 * the button `disabled` until it was ticked. The reviewer confirmed native click
 * and keyboard activation are both blocked by that and found no form-submit
 * route, so a production bypass is plausible rather than demonstrated. This is
 * defence in depth on an operation that deletes channels or writes rows an
 * operator cannot discard: `disabled` is a rendering decision, and every one of
 * these three expressions could be changed by an unrelated edit without anyone
 * noticing the gate had gone.
 *
 * Each test applies the exact dangerous mutation the guard exists to catch —
 * the button rendered live while the acknowledgement is untickled — and asserts
 * the handler refuses anyway. A guard that passes its own dangerous mutant is
 * worse than no guard, because it reads as coverage.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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

/**
 * Apply the dangerous mutant: make the button live while the acknowledgement is
 * NOT ticked, exactly as it would be if the `disabled` expression were dropped
 * or inverted by an unrelated edit.
 *
 * Removing the DOM attribute alone is not enough. React reads `props.disabled`
 * from the fiber (`shouldPreventMouseEvent`) and drops click events for a
 * disabled button whatever the attribute says — so a test that only removed the
 * attribute would pass with the handler guard deleted, which is a guard that
 * passes its own mutant. Both are cleared here.
 */
function unGate(button: HTMLElement) {
  expect(button).toBeDisabled();
  button.removeAttribute('disabled');
  const node = button as unknown as Record<string, Record<string, unknown>>;
  const propsKey = Object.keys(button).find((key) => key.startsWith('__reactProps$'));
  expect(propsKey).toBeDefined();
  // React freezes the props object in development, so it is replaced rather
  // than mutated.
  node[propsKey!] = { ...node[propsKey!], disabled: false };
  fireEvent.click(button);
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('MergeChannelsModal', () => {
  beforeEach(() => {
    vi.spyOn(api, 'mergeChannels').mockResolvedValue(
      {} as unknown as Awaited<ReturnType<typeof api.mergeChannels>>,
    );
  });

  it('refuses the merge with the acknowledgement untickled, button or no button', async () => {
    const onMerged = vi.fn();
    render(
      <MergeChannelsModal
        isEditMode
        channels={[makeChannel(1, 'Alpha'), makeChannel(2, 'Bravo')]}
        logos={[]}
        epgData={[]}
        epgSources={[]}
        channelGroups={[]}
        streamProfiles={[]}
        streams={[]}
        onClose={vi.fn()}
        onMerged={onMerged}
      />,
    );

    unGate(screen.getByRole('button', { name: /Merge 2 Channels/ }));

    await waitFor(() =>
      expect(screen.getByText(/Acknowledge that this merge applies immediately/))
        .toBeInTheDocument());
    expect(api.mergeChannels).not.toHaveBeenCalled();
    expect(onMerged).not.toHaveBeenCalled();
  });

  it('merges once the acknowledgement is ticked', async () => {
    render(
      <MergeChannelsModal
        isEditMode
        channels={[makeChannel(1, 'Alpha'), makeChannel(2, 'Bravo')]}
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

    fireEvent.click(screen.getByLabelText(/I understand this cannot be discarded/));
    fireEvent.click(screen.getByRole('button', { name: /Merge 2 Channels/ }));

    await waitFor(() => expect(api.mergeChannels).toHaveBeenCalled());
  });
});

describe('FindDuplicatesModal', () => {
  beforeEach(() => {
    vi.spyOn(api, 'findDuplicateChannels').mockResolvedValue({
      groups: [{
        normalized_name: 'alpha',
        channels: [makeChannel(1, 'Alpha'), makeChannel(2, 'Alpha')],
      }],
      total_duplicates: 1,
    } as unknown as Awaited<ReturnType<typeof api.findDuplicateChannels>>);
    vi.spyOn(api, 'bulkMergeChannels').mockResolvedValue(
      { merged: 1, failed: 0 } as unknown as Awaited<ReturnType<typeof api.bulkMergeChannels>>,
    );
  });

  it('refuses the merge with the acknowledgement untickled, button or no button', async () => {
    render(<FindDuplicatesModal isEditMode onClose={vi.fn()} onMerged={vi.fn()} />);

    unGate(await screen.findByRole('button', { name: /Merge 1 Group/ }));

    await waitFor(() =>
      expect(screen.getByText(/Acknowledge that this merge applies immediately/))
        .toBeInTheDocument());
    expect(api.bulkMergeChannels).not.toHaveBeenCalled();
  });
});

describe('CSVImportModal', () => {
  beforeEach(() => {
    vi.spyOn(api, 'parseCSVPreview').mockResolvedValue({
      rows: [{ name: 'Alpha' }],
      errors: [],
      warnings: [],
    } as unknown as Awaited<ReturnType<typeof api.parseCSVPreview>>);
    vi.spyOn(api, 'importChannelsFromCSV').mockResolvedValue({
      channels_created: 1, groups_created: 0, streams_linked: 0, warnings: [], errors: [],
    } as unknown as Awaited<ReturnType<typeof api.importChannelsFromCSV>>);
  });

  it('refuses the import with the acknowledgement untickled, button or no button', async () => {
    const onSuccess = vi.fn();
    const { container } = render(
      <CSVImportModal isOpen onClose={vi.fn()} onSuccess={onSuccess} />,
    );

    const input = container.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, {
      target: { files: [new File(['name\nAlpha\n'], 'channels.csv', { type: 'text/csv' })] },
    });
    await screen.findByTestId('csv-import-submit');

    unGate(screen.getByTestId('csv-import-submit'));

    await waitFor(() =>
      expect(screen.getByText(/Acknowledge that this import applies immediately/))
        .toBeInTheDocument());
    expect(api.importChannelsFromCSV).not.toHaveBeenCalled();
    expect(onSuccess).not.toHaveBeenCalled();
  });
});
