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

function makeStream(id: number, name: string): Stream {
  return {
    id,
    name,
    url: `http://example.com/${id}.m3u8`,
    m3u_account: 1,
    logo_url: null,
    tvg_id: null,
    channel_group: null,
    channel_group_name: 'US | News',
    is_custom: false,
  };
}

const STREAMS: Stream[] = [makeStream(1, 'US: CNN HD')];
const STREAM_GROUPS: StreamGroupInfo[] = [{ name: 'US | News', count: 1 }];
const CHANNEL_GROUPS: ChannelGroup[] = [
  { id: TARGET_GROUP_ID, name: 'News Channels', channel_count: 0 },
];
const CHANNELS: Channel[] = [];

/**
 * Two streams that COLLAPSE to one channel once the `US: ` prefix is stripped
 * and quality suffixes come off — the case the reviewer named. With
 * normalization on this is one channel; with it off it is two, on two channel
 * numbers, and that difference has to be visible in the dialog rather than
 * appearing only after the operator commits.
 */
const COLLAPSING_STREAMS: Stream[] = [
  makeStream(1, 'US: CNN HD'),
  makeStream(2, 'CNN'),
];

/** Strips a leading `US: ` — a stand-in for a configured normalization rule. */
function stripUsPrefixHandler() {
  return http.post('/api/normalization/normalize', async ({ request }) => {
    const body = (await request.json()) as { texts: string[] };
    return HttpResponse.json({
      results: body.texts.map((original) => {
        const normalized = original.replace(/^US:\s*/, '');
        return { original, normalized, changed: normalized !== original };
      }),
    });
  });
}

beforeAll(() => server.listen({ onUnhandledRequest: 'bypass' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

/**
 * Clears `externalTriggerStreamIds` on handled, exactly as `App.tsx` does —
 * the trigger effect lists the prop among its dependencies and re-opens the
 * modal forever if the harness never clears it.
 */
interface HarnessOptions {
  manualEntry?: boolean;
  streams?: Stream[];
  defaultNormalizeOnCreate?: boolean;
  onCreateChannel?: ReturnType<typeof vi.fn>;
  onCheckConflicts?: ReturnType<typeof vi.fn>;
}

function BulkCreateHarness({
  onBulkCreateFromGroup,
  manualEntry = false,
  streams = STREAMS,
  defaultNormalizeOnCreate = false,
  onCreateChannel,
  onCheckConflicts,
}: HarnessOptions & {
  onBulkCreateFromGroup: NonNullable<React.ComponentProps<typeof StreamsPane>['onBulkCreateFromGroup']>;
}) {
  const [streamIds, setStreamIds] = useState<number[] | null>(
    manualEntry ? null : streams.map((s) => s.id),
  );
  const [manual, setManual] = useState(manualEntry);
  return (
    <StreamsPane
      streams={streams}
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
      defaultNormalizeOnCreate={defaultNormalizeOnCreate}
      externalTriggerStreamIds={streamIds}
      externalTriggerManualEntry={manual}
      externalTriggerTargetGroupId={TARGET_GROUP_ID}
      externalTriggerStartingNumber={200}
      onBulkCreateFromGroup={onBulkCreateFromGroup}
      onCreateChannel={(onCreateChannel ?? vi.fn()) as NonNullable<
        React.ComponentProps<typeof StreamsPane>['onCreateChannel']
      >}
      onCheckConflicts={onCheckConflicts as React.ComponentProps<typeof StreamsPane>['onCheckConflicts']}
      onExternalTriggerHandled={() => {
        setStreamIds(null);
        setManual(false);
      }}
    />
  );
}

function renderDialog(
  onBulkCreateFromGroup: ReturnType<typeof vi.fn> = vi.fn(),
  options: HarnessOptions = {},
) {
  render(
    <NotificationProvider>
      <BulkCreateHarness
        onBulkCreateFromGroup={
          onBulkCreateFromGroup as unknown as NonNullable<
            React.ComponentProps<typeof StreamsPane>['onBulkCreateFromGroup']
          >
        }
        {...options}
      />
    </NotificationProvider>,
  );
  return { onBulkCreateFromGroup, ...options };
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

});

/**
 * The resolved name is not only what a channel is CALLED — it is the key the
 * bulk-create path merges streams on, so it decides how many channels there
 * are and therefore which numbers they claim. The dialog's count used to be
 * computed from the RAW `stream.name` whatever the toggle said, while
 * submission used the resolved name. On the default path (normalization on)
 * the dialog could promise two channels and stage one.
 */
describe('Create Channels channel count', () => {
  it('counts the channels it will create off the resolved names, not the raw ones', async () => {
    server.use(stripUsPrefixHandler());
    renderDialog(vi.fn(), {
      streams: COLLAPSING_STREAMS,
      defaultNormalizeOnCreate: true,
    });

    // "US: CNN HD" and "CNN" both resolve to "CNN", so this is ONE channel.
    expect(await screen.findByRole('button', { name: /Create 1 Channels/i })).toBeInTheDocument();
    expect(await screen.findByText(/1 duplicate.? merged/i)).toBeInTheDocument();
  });

  it('moves the count when the operator turns normalization off, rather than hiding the change', async () => {
    server.use(stripUsPrefixHandler());
    const user = userEvent.setup();
    renderDialog(vi.fn(), {
      streams: COLLAPSING_STREAMS,
      defaultNormalizeOnCreate: true,
    });

    expect(await screen.findByRole('button', { name: /Create 1 Channels/i })).toBeInTheDocument();

    await openNormalizationSection(user);
    await user.click(screen.getByRole('checkbox', { name: /Apply normalization rules/i }));

    // Raw names differ, so these are two separate channels on two numbers.
    expect(await screen.findByRole('button', { name: /Create 2 Channels/i })).toBeInTheDocument();
  });

  it('sizes the channel-number conflict check off the resolved count too', async () => {
    server.use(stripUsPrefixHandler());
    const user = userEvent.setup();
    const onCheckConflicts = vi.fn().mockReturnValue(0);
    renderDialog(vi.fn(), {
      streams: COLLAPSING_STREAMS,
      defaultNormalizeOnCreate: true,
      onCheckConflicts,
    });

    await user.click(await screen.findByRole('button', { name: /Create 1 Channels/i }));

    expect(onCheckConflicts).toHaveBeenCalledWith(200, 1);
  });

  it('previews the resolved name, so the listed channel is the one that gets created', async () => {
    server.use(stripUsPrefixHandler());
    renderDialog(vi.fn(), {
      streams: COLLAPSING_STREAMS,
      defaultNormalizeOnCreate: true,
    });

    // Wait for the resolution to land before reading the list.
    await screen.findByRole('button', { name: /Create 1 Channels/i });

    const preview = screen.getByText('Channels (first 10)');
    const list = preview.parentElement!.querySelector('.preview-list')!;
    expect(list.textContent).toContain('CNN');
    expect(list.textContent).not.toContain('US: CNN');
  });
});

/**
 * PO override: manual entry keeps the control (bead
 * `enhancedchannelmanager-e9e5o`). It was hidden because the create path never
 * consulted it and the preview could never populate — but a control removed is
 * not a control made honest, and the operator loses a capability they had. It
 * is restored and WIRED: the preview reads the typed name, and the toggle
 * decides the name the channel is created with.
 */
describe('Manual entry "Normalization Rules" control', () => {
  it('offers the control, and previews the name the operator typed', async () => {
    server.use(stripUsPrefixHandler());
    const user = userEvent.setup();
    renderDialog(vi.fn(), { manualEntry: true, defaultNormalizeOnCreate: true });

    await user.type(await screen.findByPlaceholderText('Enter channel name'), 'US: CNN');
    await openNormalizationSection(user);

    expect(await screen.findByText('CNN')).toBeInTheDocument();
    expect(screen.getByText('US: CNN')).toBeInTheDocument();
  });

  it('creates the channel with the normalized name when the toggle is on', async () => {
    server.use(stripUsPrefixHandler());
    const user = userEvent.setup();
    const onCreateChannel = vi.fn().mockResolvedValue(undefined);
    renderDialog(vi.fn(), {
      manualEntry: true,
      defaultNormalizeOnCreate: true,
      onCreateChannel,
    });

    await user.type(await screen.findByPlaceholderText('Enter channel name'), 'US: CNN');
    await user.click(screen.getByRole('button', { name: /Create Channel/i }));

    expect(onCreateChannel).toHaveBeenCalled();
    expect(onCreateChannel.mock.calls[0][0]).toBe('CNN');
  });

  it('creates the channel with the literal text when the toggle is off', async () => {
    server.use(stripUsPrefixHandler());
    const user = userEvent.setup();
    const onCreateChannel = vi.fn().mockResolvedValue(undefined);
    renderDialog(vi.fn(), {
      manualEntry: true,
      defaultNormalizeOnCreate: false,
      onCreateChannel,
    });

    await user.type(await screen.findByPlaceholderText('Enter channel name'), 'US: CNN');
    await user.click(screen.getByRole('button', { name: /Create Channel/i }));

    expect(onCreateChannel).toHaveBeenCalled();
    expect(onCreateChannel.mock.calls[0][0]).toBe('US: CNN');
  });

  it('says so when normalization was asked for and the engine could not be reached', async () => {
    server.use(
      http.post('/api/normalization/normalize', () =>
        HttpResponse.json({ detail: 'boom' }, { status: 500 })
      )
    );
    const user = userEvent.setup();
    const onCreateChannel = vi.fn().mockResolvedValue(undefined);
    renderDialog(vi.fn(), {
      manualEntry: true,
      defaultNormalizeOnCreate: true,
      onCreateChannel,
    });

    await user.type(await screen.findByPlaceholderText('Enter channel name'), 'US: CNN');
    await user.click(screen.getByRole('button', { name: /Create Channel/i }));

    // The channel is still created, under the name as typed...
    expect(onCreateChannel.mock.calls[0][0]).toBe('US: CNN');
    // ...and the operator is told the rules did not run, so an unchanged name
    // does not read as "the rules matched nothing".
    expect(await screen.findByText('Normalization failed')).toBeInTheDocument();
  });
});
