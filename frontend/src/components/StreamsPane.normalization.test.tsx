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
import { render, screen, waitFor } from '@testing-library/react';
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

/**
 * Two stream groups whose streams resolve to the SAME name once `US: ` comes
 * off. In `separate` mode the create runs once per group and each run groups
 * only its own streams, so this is TWO channels in two target groups — the
 * case a single global grouping map merged into one.
 */
const CROSS_GROUP_STREAMS: Stream[] = [
  { ...makeStream(1, 'US: CNN HD'), channel_group_name: 'US | News' },
  { ...makeStream(2, 'CNN'), channel_group_name: 'UK | News' },
];
const CROSS_GROUP_STREAM_GROUPS: StreamGroupInfo[] = [
  { name: 'US | News', count: 1 },
  { name: 'UK | News', count: 1 },
];
// Hoisted so the prop keeps a stable identity: the trigger effect lists it as
// a dependency and a fresh literal per render re-opens the modal forever.
const CROSS_GROUP_NAMES = ['US | News', 'UK | News'];

/** Strips a leading `US: ` — a stand-in for a configured normalization rule. */
function stripUsPrefixHandler(counter?: { calls: number }, gate?: Promise<void>) {
  return http.post('/api/normalization/normalize', async ({ request }) => {
    if (counter) counter.calls += 1;
    const body = (await request.json()) as { texts: string[] };
    if (gate) await gate;
    return HttpResponse.json({
      results: body.texts.map((original) => {
        const normalized = original.replace(/^US:\s*/, '');
        return { original, normalized, changed: normalized !== original };
      }),
    });
  });
}

/** A promise the test releases by hand, so a resolution can be held in flight. */
function makeGate() {
  let release!: () => void;
  const promise = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { promise, release };
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

/**
 * Opens the dialog on TWO stream groups, which lands in `separate` mode — the
 * mode `openBulkCreateModalForMultipleGroups` defaults to. Clears the trigger
 * prop on handled, exactly as `App.tsx` does.
 */
function MultiGroupHarness({
  onBulkCreateFromGroup,
  defaultNormalizeOnCreate = true,
}: {
  onBulkCreateFromGroup: NonNullable<React.ComponentProps<typeof StreamsPane>['onBulkCreateFromGroup']>;
  defaultNormalizeOnCreate?: boolean;
}) {
  const [groupNames, setGroupNames] = useState<string[] | null>(CROSS_GROUP_NAMES);
  return (
    <StreamsPane
      streams={CROSS_GROUP_STREAMS}
      providers={[]}
      streamGroups={CROSS_GROUP_STREAM_GROUPS}
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
      externalTriggerGroupNames={groupNames}
      externalTriggerTargetGroupId={TARGET_GROUP_ID}
      onBulkCreateFromGroup={onBulkCreateFromGroup}
      onCreateChannel={vi.fn()}
      onExternalTriggerHandled={() => setGroupNames(null)}
    />
  );
}

function renderMultiGroupDialog(
  onBulkCreateFromGroup: ReturnType<typeof vi.fn> = vi.fn(),
  defaultNormalizeOnCreate = true,
) {
  render(
    <NotificationProvider>
      <MultiGroupHarness
        onBulkCreateFromGroup={
          onBulkCreateFromGroup as unknown as NonNullable<
            React.ComponentProps<typeof StreamsPane>['onBulkCreateFromGroup']
          >
        }
        defaultNormalizeOnCreate={defaultNormalizeOnCreate}
      />
    </NotificationProvider>,
  );
  return { onBulkCreateFromGroup };
}

async function openNormalizationSection(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByText('Normalization Rules'));
}

/**
 * Manual entry's Create button, once the typed name has been resolved.
 *
 * The button is disabled until then (bead `enhancedchannelmanager-e9e5o`): the
 * resolution is debounced, and a click inside that window used to submit a name
 * the dialog had not yet resolved.
 */
async function enabledManualCreateButton() {
  const button = await screen.findByRole('button', { name: /Create Channel/i });
  await waitFor(() => expect(button).toBeEnabled());
  return button;
}

describe('Create Channels "Normalization Rules" control', () => {
  it('hands the create the resolution the preview produced, rather than a flag to resolve again', async () => {
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
    await user.click(await screen.findByRole('button', { name: /Create 1 Channel/i }));

    // The resolution is the last positional parameter. It used to be the
    // toggle's boolean, which the create then answered a SECOND time — two
    // independent answers to one question, free to disagree.
    const call = onBulkCreateFromGroup.mock.calls[0];
    const resolution = call[call.length - 1] as {
      names: Map<string, string>;
      normalizationFailed: boolean;
    };
    expect(resolution.names.get('US: CNN HD')).toBe('CNN');
    expect(resolution.normalizationFailed).toBe(false);
  });

  it('hands the create the identity resolution when the toggle is off', async () => {
    server.use(stripUsPrefixHandler());
    const user = userEvent.setup();
    const { onBulkCreateFromGroup } = renderDialog();

    await user.click(await screen.findByRole('button', { name: /Create 1 Channel/i }));

    const call = onBulkCreateFromGroup.mock.calls[0];
    const resolution = call[call.length - 1] as {
      names: Map<string, string>;
      normalizationFailed: boolean;
    };
    expect(resolution.names.get('US: CNN HD')).toBe('US: CNN HD');
    expect(resolution.normalizationFailed).toBe(false);
  });

  it('makes no second normalization request when the create is submitted (pin)', async () => {
    const counter = { calls: 0 };
    server.use(stripUsPrefixHandler(counter));
    const user = userEvent.setup();
    renderDialog(vi.fn(), { streams: COLLAPSING_STREAMS, defaultNormalizeOnCreate: true });

    await screen.findByRole('button', { name: /Create 1 Channels/i });
    const afterPreview = counter.calls;
    expect(afterPreview).toBeGreaterThan(0);

    await user.click(screen.getByRole('button', { name: /Create 1 Channels/i }));

    // PIN. The bulk path's second resolution lives in `App.tsx`, which is a
    // stub here, so this cannot go red at this layer — the red proof that the
    // create consumes the preview is the resolution-passing test above, and
    // the manual-entry equivalent below. This guards against a resolve call
    // being added back inside `doBulkCreate`.
    expect(counter.calls).toBe(afterPreview);
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

  it('counts the groups independently in separate mode, because the create runs once per group', async () => {
    // Group boundaries survive submission in `separate` mode: each group is a
    // separate `onBulkCreateFromGroup` call that groups only its own streams.
    // The preview built ONE global grouping map, so two groups whose names
    // resolve to the same key were merged into a single promised channel while
    // submission staged two — in two target groups, on two channel numbers.
    server.use(stripUsPrefixHandler());
    renderMultiGroupDialog();

    // Wait on the resolver's own output before reading the count: the raw
    // names ALSO give 2, so asserting the count straight away would pass
    // against the unresolved state and prove nothing.
    await screen.findByText(/1 name will change/i);
    expect(screen.getByRole('button', { name: /Create 2 Channels/i })).toBeInTheDocument();
  });

  it('merges the same two groups into one channel when they are combined into a single run (pin)', async () => {
    // PIN on the counterpart, which was already correct: `single` mode really
    // does hand both groups to one call, which groups them together, so here
    // the merge is right. The preview has to model whichever mode is
    // selected, not one of them.
    server.use(stripUsPrefixHandler());
    const user = userEvent.setup();
    renderMultiGroupDialog();

    await screen.findByText(/1 name will change/i);
    await user.click(screen.getByRole('radio', { name: /Combine into single channel group/i }));

    expect(await screen.findByRole('button', { name: /Create 1 Channels/i })).toBeInTheDocument();
  });

  it('stages one channel per group when two groups resolve to the same name', async () => {
    // PIN on already-correct submission behaviour: this is the side the
    // preview had to be brought into line with.
    server.use(stripUsPrefixHandler());
    const user = userEvent.setup();
    const { onBulkCreateFromGroup } = renderMultiGroupDialog();

    await screen.findByText(/1 name will change/i);
    const groupStartInputs = screen.getAllByPlaceholderText('Auto');
    await user.type(groupStartInputs[0], '100');
    await user.click(screen.getByRole('button', { name: /Create 2 Channels/i }));

    expect(onBulkCreateFromGroup).toHaveBeenCalledTimes(2);
    expect(onBulkCreateFromGroup.mock.calls[0][0]).toHaveLength(1);
    expect(onBulkCreateFromGroup.mock.calls[1][0]).toHaveLength(1);
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
 * "Not resolved yet" is a state of its own, distinct from "resolved to
 * itself" (bead `enhancedchannelmanager-e9e5o`).
 *
 * The stats used to substitute the raw name for any name the resolver had not
 * answered for yet, so during the debounce plus the network round trip the
 * dialog rendered a full, confident, WRONG channel count — and that count fed
 * the conflict check and the push-down plan. The Create button was enabled
 * throughout, so clicking inside the window planned two channels and staged
 * one. No surface may render or plan off a provisional value.
 */
describe('Create Channels before the names are resolved', () => {
  it('refuses to create while the resolution is in flight, instead of planning off raw names', async () => {
    const gate = makeGate();
    server.use(stripUsPrefixHandler(undefined, gate.promise));
    const user = userEvent.setup();
    const onCheckConflicts = vi.fn().mockReturnValue(0);
    const { onBulkCreateFromGroup } = renderDialog(vi.fn(), {
      streams: COLLAPSING_STREAMS,
      defaultNormalizeOnCreate: true,
      onCheckConflicts,
    });

    const pending = await screen.findByRole('button', { name: /Resolving names/i });
    expect(pending).toBeDisabled();
    // The provisional two-channel promise is not made at all.
    expect(screen.queryByRole('button', { name: /Create 2 Channels/i })).toBeNull();

    await user.click(pending);
    expect(onCheckConflicts).not.toHaveBeenCalled();
    expect(onBulkCreateFromGroup).not.toHaveBeenCalled();

    gate.release();

    // Once resolved, the count is the one submission will use.
    await user.click(await screen.findByRole('button', { name: /Create 1 Channels/i }));
    expect(onCheckConflicts).toHaveBeenCalledWith(200, 1);
    expect(onBulkCreateFromGroup).toHaveBeenCalledTimes(1);
  });

  it('does not preview channels off names it has not resolved', async () => {
    const gate = makeGate();
    server.use(stripUsPrefixHandler(undefined, gate.promise));
    renderDialog(vi.fn(), {
      streams: COLLAPSING_STREAMS,
      defaultNormalizeOnCreate: true,
    });

    await screen.findByRole('button', { name: /Resolving names/i });
    const preview = screen.getByText('Channels (first 10)');
    const list = preview.parentElement!.querySelector('.preview-list')!;
    expect(list.textContent).not.toContain('US: CNN HD');

    gate.release();
    await screen.findByRole('button', { name: /Create 1 Channels/i });
    expect(list.textContent).toContain('CNN');
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
    await user.click(await enabledManualCreateButton());

    expect(onCreateChannel).toHaveBeenCalled();
    expect(onCreateChannel.mock.calls[0][0]).toBe('CNN');
  });

  it('resolves the typed name ONCE — the create consumes the preview instead of asking again', async () => {
    const counter = { calls: 0 };
    server.use(stripUsPrefixHandler(counter));
    const user = userEvent.setup();
    const onCreateChannel = vi.fn().mockResolvedValue(undefined);
    renderDialog(vi.fn(), {
      manualEntry: true,
      defaultNormalizeOnCreate: true,
      onCreateChannel,
    });

    await user.type(await screen.findByPlaceholderText('Enter channel name'), 'US: CNN');
    await waitFor(() => expect(counter.calls).toBeGreaterThan(0));
    const afterPreview = counter.calls;

    await user.click(await enabledManualCreateButton());

    expect(onCreateChannel.mock.calls[0][0]).toBe('CNN');
    expect(counter.calls).toBe(afterPreview);
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
    await user.click(await enabledManualCreateButton());

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
    await user.click(await enabledManualCreateButton());

    // The channel is still created, under the name as typed...
    expect(onCreateChannel.mock.calls[0][0]).toBe('US: CNN');
    // ...and the operator is told the rules did not run, so an unchanged name
    // does not read as "the rules matched nothing".
    expect(await screen.findByText('Normalization failed')).toBeInTheDocument();
  });
});
