/**
 * The Create Channels dialog's "Normalization Rules" control tells the truth
 * about what it does (bead `enhancedchannelmanager-e9e5o`).
 *
 * The toggle used to drive only the preview: names were normalized whatever
 * its state, and the flag it stored on the staged operation was dropped before
 * the wire payload. It is now a real control, which creates a second problem
 * this file also covers — once "unnormalized name" can mean *the operator
 * asked for it*, a normalization failure must not look the same. Both the
 * dialog preview and the post-create message distinguish the two.
 */
import { describe, it, expect, vi, beforeAll, afterEach, afterAll } from 'vitest';
import { useState } from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { StreamsPane } from './StreamsPane';
import { NotificationProvider } from '../contexts/NotificationContext';
import { server } from '../test/mocks/server';
import type { Stream, StreamGroupInfo, Channel, ChannelGroup } from '../types';

const TARGET_GROUP_ID = 1;

const STREAMS: Stream[] = [
  {
    id: 1,
    name: 'US: CNN HD',
    url: 'http://example.com/1.m3u8',
    m3u_account: 1,
    logo_url: null,
    tvg_id: null,
    channel_group: null,
    channel_group_name: 'US | News',
    is_custom: false,
  },
];
const STREAM_GROUPS: StreamGroupInfo[] = [{ name: 'US | News', count: 1 }];
const CHANNEL_GROUPS: ChannelGroup[] = [
  { id: TARGET_GROUP_ID, name: 'News Channels', channel_count: 0 },
];
const CHANNELS: Channel[] = [];
const TRIGGER_STREAM_IDS = [1];

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

/**
 * Clears `externalTriggerStreamIds` on handled, exactly as `App.tsx` does —
 * the trigger effect lists the prop among its dependencies and re-opens the
 * modal forever if the harness never clears it.
 */
function BulkCreateHarness({
  onBulkCreateFromGroup,
  manualEntry = false,
}: {
  onBulkCreateFromGroup: NonNullable<React.ComponentProps<typeof StreamsPane>['onBulkCreateFromGroup']>;
  manualEntry?: boolean;
}) {
  const [streamIds, setStreamIds] = useState<number[] | null>(manualEntry ? null : TRIGGER_STREAM_IDS);
  const [manual, setManual] = useState(manualEntry);
  return (
    <StreamsPane
      streams={STREAMS}
      providers={[]}
      streamGroups={STREAM_GROUPS}
      searchTerm=""
      onSearchChange={vi.fn()}
      providerFilter={null}
      onProviderFilterChange={vi.fn()}
      groupFilter={null}
      onGroupFilterChange={vi.fn()}
      loading={false}
      channels={CHANNELS}
      channelGroups={CHANNEL_GROUPS}
      isEditMode
      externalTriggerStreamIds={streamIds}
      externalTriggerManualEntry={manual}
      externalTriggerTargetGroupId={TARGET_GROUP_ID}
      externalTriggerStartingNumber={200}
      onBulkCreateFromGroup={onBulkCreateFromGroup}
      onCreateChannel={vi.fn()}
      onExternalTriggerHandled={() => {
        setStreamIds(null);
        setManual(false);
      }}
    />
  );
}

function renderDialog(
  onBulkCreateFromGroup: ReturnType<typeof vi.fn> = vi.fn(),
  manualEntry = false,
) {
  render(
    <NotificationProvider>
      <BulkCreateHarness
        onBulkCreateFromGroup={
          onBulkCreateFromGroup as unknown as NonNullable<
            React.ComponentProps<typeof StreamsPane>['onBulkCreateFromGroup']
          >
        }
        manualEntry={manualEntry}
      />
    </NotificationProvider>,
  );
  return { onBulkCreateFromGroup };
}

async function openNormalizationSection(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByText('Normalization Rules'));
}

describe('Create Channels "Normalization Rules" control', () => {
  it('passes the toggle state to the create call, so it can decide whether names are normalized', async () => {
    server.use(
      http.post('/api/normalization/normalize', () =>
        HttpResponse.json({
          results: [{ original: 'US: CNN HD', normalized: 'CNN', changed: true }],
        })
      )
    );
    const user = userEvent.setup();
    const { onBulkCreateFromGroup } = renderDialog();

    await openNormalizationSection(user);
    await user.click(screen.getByRole('checkbox', { name: /Apply normalization rules/i }));
    await user.click(screen.getByRole('button', { name: /Create 1 Channel/i }));

    // The `normalize` argument is the last positional parameter.
    const call = onBulkCreateFromGroup.mock.calls[0];
    expect(call[call.length - 1]).toBe(true);
  });

  it('tells the operator when normalization was asked for and the engine could not be reached', async () => {
    const user = userEvent.setup();
    renderDialog(vi.fn().mockResolvedValue({ normalizationFailed: true }));

    await user.click(await screen.findByRole('button', { name: /Create 1 Channel/i }));

    expect(await screen.findByText('Normalization failed')).toBeInTheDocument();
    expect(screen.getByText(/raw provider names/i)).toBeInTheDocument();
  });

  it('stays quiet when the create reports no normalization failure', async () => {
    const user = userEvent.setup();
    renderDialog(vi.fn().mockResolvedValue({ normalizationFailed: false }));

    await user.click(await screen.findByRole('button', { name: /Create 1 Channel/i }));

    expect(screen.queryByText('Normalization failed')).not.toBeInTheDocument();
  });

  it('distinguishes a failed preview from a rule set that changes nothing', async () => {
    server.use(
      http.post('/api/normalization/normalize', () =>
        HttpResponse.json({ detail: 'boom' }, { status: 500 })
      )
    );
    const user = userEvent.setup();
    renderDialog();

    await openNormalizationSection(user);
    await user.click(screen.getByRole('checkbox', { name: /Apply normalization rules/i }));

    expect(
      await screen.findByText(/Could not reach the normalization engine/i)
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/No names will change/i)
    ).not.toBeInTheDocument();
  });

  it('reports a preview that genuinely changes nothing as exactly that', async () => {
    server.use(
      http.post('/api/normalization/normalize', () =>
        HttpResponse.json({
          results: [{ original: 'US: CNN HD', normalized: 'US: CNN HD', changed: false }],
        })
      )
    );
    const user = userEvent.setup();
    renderDialog();

    await openNormalizationSection(user);
    await user.click(screen.getByRole('checkbox', { name: /Apply normalization rules/i }));

    expect(await screen.findByText(/No names will change/i)).toBeInTheDocument();
    expect(
      screen.queryByText(/Could not reach the normalization engine/i)
    ).not.toBeInTheDocument();
  });

  it('does not offer the control in manual entry, which has no provider name to normalize', async () => {
    renderDialog(vi.fn(), true);

    expect(await screen.findByPlaceholderText('Enter channel name')).toBeInTheDocument();
    expect(screen.queryByText('Normalization Rules')).not.toBeInTheDocument();
  });
});
