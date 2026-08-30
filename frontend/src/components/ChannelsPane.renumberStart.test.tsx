/**
 * Renumber start numbers are whole numbers, and a fractional entry is REFUSED
 * (bead `enhancedchannelmanager-j3pyx`).
 *
 * Every renumber dialog in this pane seeds a sequential run from one typed
 * number. They all used to read that field with `parseInt` and guard `>= 1`, so
 * a typed `1.5` became `1`: the preview said "Channels will be numbered 1 - 12",
 * the confirm button stayed live, and the channels were renumbered from a
 * number nobody had asked for. Nothing anywhere said the value had changed.
 *
 * The canonical channel-number contract (bead `enhancedchannelmanager-ic884.1`)
 * settled the principle for the fields that accept tenths: out-of-contract
 * input is refused with a clear sentence, never silently altered. These fields
 * are whole-number-only by design, so they refuse rather than widen.
 *
 * What each test pins is the three-way agreement that the defect broke: the
 * message is shown, the preview is NOT shown, and the confirm button cannot
 * act. A test that only checked the message would still pass with a live button
 * previewing a run beginning at 1.
 *
 * Like the other ChannelsPane suites, this renders the pane directly with the
 * minimal prop set. ChannelsPane has no general-purpose test suite.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ChannelsPane } from './ChannelsPane';
import { NotificationProvider } from '../contexts/NotificationContext';
import { WHOLE_CHANNEL_NUMBER_RULE_MESSAGE } from '../utils/channelNumber';
import type { Channel, ChannelGroup, ChannelListFilterSettings } from '../types';

const GROUP_ID = 10;
const OTHER_GROUP_ID = 20;

const groups: ChannelGroup[] = [
  { id: GROUP_ID, name: 'Sports', channel_count: 3 },
  { id: OTHER_GROUP_ID, name: 'News', channel_count: 1 },
];

function makeFilters(): ChannelListFilterSettings {
  return {
    showEmptyGroups: true,
    showNewlyCreatedGroups: true,
    showProviderGroups: true,
    showManualGroups: true,
    showAutoChannelGroups: true,
  };
}

function makeChannel(id: number, name: string, channelNumber: number, groupId: number): Channel {
  return {
    id,
    channel_number: channelNumber,
    name,
    channel_group_id: groupId,
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

const CHANNELS: Channel[] = [
  makeChannel(1, 'Alpha', 10, GROUP_ID),
  makeChannel(2, 'Bravo', 11, GROUP_ID),
  makeChannel(3, 'Charlie', 12, GROUP_ID),
  makeChannel(4, 'Delta', 100, OTHER_GROUP_ID),
];

interface StagedUpdate {
  channelId: number;
  data: Partial<Channel>;
}

function renderPane(selectedIds: number[] = []) {
  const staged: StagedUpdate[] = [];
  render(
    <NotificationProvider>
      <ChannelsPane
        channelGroups={groups}
        channels={CHANNELS}
        streams={[]}
        providers={[]}
        selectedChannelId={null}
        onChannelSelect={vi.fn()}
        onChannelUpdate={vi.fn()}
        onChannelDrop={vi.fn()}
        onBulkStreamDrop={vi.fn()}
        onChannelReorder={vi.fn()}
        onCreateChannel={vi.fn()}
        onDeleteChannel={vi.fn()}
        searchTerm=""
        onSearchChange={vi.fn()}
        selectedGroups={[GROUP_ID, OTHER_GROUP_ID]}
        onSelectedGroupsChange={vi.fn()}
        loading={false}
        autoRenameChannelNumber={false}
        isEditMode
        selectedChannelIds={new Set(selectedIds)}
        onClearChannelSelection={vi.fn()}
        onStartBatch={vi.fn()}
        onEndBatch={vi.fn()}
        onStageUpdateChannel={(channelId, data) => {
          staged.push({ channelId, data });
        }}
        channelListFilters={makeFilters()}
        onChannelListFiltersChange={vi.fn()}
      />
    </NotificationProvider>,
  );
  return staged;
}

/** Open the group-header "Group actions" menu and pick "Sort & Renumber". */
async function openSortAndRenumber(user: ReturnType<typeof userEvent.setup>) {
  const header = Array.from(document.querySelectorAll('.group-header')).find((el) =>
    el.querySelector('.group-name')?.textContent?.includes('Sports'),
  );
  expect(header, 'no group header for "Sports"').toBeTruthy();
  await user.click(header!.querySelector<HTMLButtonElement>('.group-menu-btn')!);
  await user.click(screen.getByRole('button', { name: /Sort & Renumber/ }));

  const dialog = document.querySelector('.sort-renumber-dialog') as HTMLElement;
  expect(dialog, 'Sort & Renumber dialog did not open').not.toBeNull();
  return dialog;
}

/** Open the floating selection bar's "Renumber" dialog. */
async function openMassRenumber(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: 'Renumber' }));
  const dialog = document.querySelector('.mass-renumber-dialog') as HTMLElement;
  expect(dialog, 'Renumber dialog did not open').not.toBeNull();
  return dialog;
}

/** Open the pane toolbar's "Renumber All Groups" dialog. */
async function openRenumberAllGroups(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getAllByRole('button', { name: 'More actions' })[0]);
  await user.click(screen.getByRole('menuitem', { name: /Renumber All Groups/ }));

  const heading = screen.getByRole('heading', { name: /Renumber All Groups/ });
  const dialog = heading.closest('.modal-container') as HTMLElement;
  expect(dialog, 'Renumber All Groups dialog did not open').not.toBeNull();
  return dialog;
}

async function retype(
  user: ReturnType<typeof userEvent.setup>,
  input: HTMLInputElement,
  value: string,
) {
  await user.clear(input);
  await user.type(input, value);
}

describe('Sort & Renumber starting number', () => {
  it('refuses a fractional start instead of renumbering from its integer part', async () => {
    const user = userEvent.setup();
    const staged = renderPane();
    const dialog = await openSortAndRenumber(user);

    const input = within(dialog).getByLabelText('Starting Channel Number') as HTMLInputElement;
    await retype(user, input, '1.5');

    expect(within(dialog).getByRole('alert')).toHaveTextContent(WHOLE_CHANNEL_NUMBER_RULE_MESSAGE);
    // The preview is the second half of the defect: it announced a run the
    // operation would never produce.
    expect(within(dialog).queryByText(/Channels will be numbered/)).toBeNull();

    const confirm = within(dialog).getByRole('button', { name: /^Sort & Renumber$/ });
    expect(confirm).toBeDisabled();
    await user.click(confirm);
    expect(staged).toEqual([]);
  });

  it('still renumbers from a whole start', async () => {
    const user = userEvent.setup();
    const staged = renderPane();
    const dialog = await openSortAndRenumber(user);

    const input = within(dialog).getByLabelText('Starting Channel Number') as HTMLInputElement;
    await retype(user, input, '20');

    expect(within(dialog).queryByRole('alert')).toBeNull();
    expect(within(dialog).getByText(/Channels will be numbered 20 – 22/)).toBeTruthy();

    await user.click(within(dialog).getByRole('button', { name: /^Sort & Renumber$/ }));
    expect(staged.map((s) => s.data.channel_number)).toEqual([20, 21, 22]);
  });
});

describe('Renumber (selection) starting number', () => {
  it('refuses a fractional start instead of renumbering from its integer part', async () => {
    const user = userEvent.setup();
    const staged = renderPane([1, 2, 3]);
    const dialog = await openMassRenumber(user);

    const input = within(dialog).getByLabelText('Starting Channel Number') as HTMLInputElement;
    await retype(user, input, '1.5');

    expect(within(dialog).getByRole('alert')).toHaveTextContent(WHOLE_CHANNEL_NUMBER_RULE_MESSAGE);
    expect(within(dialog).queryByText(/Channels will be numbered/)).toBeNull();

    const confirm = within(dialog).getByRole('button', { name: /^Renumber$/ });
    expect(confirm).toBeDisabled();
    await user.click(confirm);
    expect(staged).toEqual([]);
  });

  it('still renumbers from a whole start', async () => {
    const user = userEvent.setup();
    const staged = renderPane([1, 2, 3]);
    const dialog = await openMassRenumber(user);

    const input = within(dialog).getByLabelText('Starting Channel Number') as HTMLInputElement;
    await retype(user, input, '30');

    expect(within(dialog).queryByRole('alert')).toBeNull();
    await user.click(within(dialog).getByRole('button', { name: /^Renumber$/ }));
    expect(staged.map((s) => s.data.channel_number)).toEqual([30, 31, 32]);
  });
});

describe('Move to group custom starting number', () => {
  it('refuses a fractional start, shows the rule, and keeps Move dead', async () => {
    const user = userEvent.setup();
    const staged = renderPane([1]);

    await user.click(screen.getByRole('button', { name: 'More selection actions' }));
    await user.click(document.querySelector('[data-submenu-trigger="move"]') as HTMLElement);
    const chooser = document.getElementById('selection-bar-move-chooser') as HTMLElement;
    await user.click(within(chooser).getByText('News'));

    const dialog = document.querySelector('.cross-group-move-dialog') as HTMLElement;
    expect(dialog, 'cross-group move dialog did not open').not.toBeNull();
    await user.click(
      within(dialog)
        .getByText('Custom starting number')
        .closest('label')!
        .querySelector('input[type="radio"]') as HTMLInputElement,
    );
    await user.type(dialog.querySelector('.custom-number-input-inline') as HTMLInputElement, '1.5');

    // This dialog surfaces a refusal through its own blocked-reason line, which
    // already existed for the empty-group case (bead enhancedchannelmanager-gddai).
    expect(dialog.querySelector('.move-numbering-blocked')?.textContent).toBe(
      WHOLE_CHANNEL_NUMBER_RULE_MESSAGE,
    );
    expect(dialog.querySelector('.custom-number-range-inline')).toBeNull();

    const move = within(dialog).getByRole('button', { name: /^Move Channel$/ });
    expect(move).toBeDisabled();
    await user.click(move);
    expect(staged).toEqual([]);
  });
});

describe('Renumber All Groups starting numbers', () => {
  it('refuses a fractional overall start instead of renumbering from its integer part', async () => {
    const user = userEvent.setup();
    const staged = renderPane();
    const dialog = await openRenumberAllGroups(user);

    const input = within(dialog).getByLabelText('Starting Channel Number') as HTMLInputElement;
    await retype(user, input, '1.5');

    expect(within(dialog).getByRole('alert')).toHaveTextContent(WHOLE_CHANNEL_NUMBER_RULE_MESSAGE);

    const confirm = within(dialog).getByRole('button', { name: /Renumber All/ });
    expect(confirm).toBeDisabled();
    await user.click(confirm);
    expect(staged).toEqual([]);
  });

  it('refuses a fractional PER-GROUP override, which had its own copy of the rule', async () => {
    const user = userEvent.setup();
    const staged = renderPane();
    const dialog = await openRenumberAllGroups(user);

    // Per-group override inputs carry this title; the overall start does not.
    const overrides = within(dialog).getAllByTitle(
      'Custom starting number for this group',
    ) as HTMLInputElement[];
    await retype(user, overrides[0], '1.5');

    expect(within(dialog).getByRole('alert')).toHaveTextContent(WHOLE_CHANNEL_NUMBER_RULE_MESSAGE);

    const confirm = within(dialog).getByRole('button', { name: /Renumber All/ });
    expect(confirm).toBeDisabled();
    await user.click(confirm);
    expect(staged).toEqual([]);
  });

  it('still renumbers from a whole per-group override', async () => {
    const user = userEvent.setup();
    const staged = renderPane();
    const dialog = await openRenumberAllGroups(user);

    const overrides = within(dialog).getAllByTitle(
      'Custom starting number for this group',
    ) as HTMLInputElement[];
    await retype(user, overrides[0], '50');

    expect(within(dialog).queryByRole('alert')).toBeNull();
    await user.click(within(dialog).getByRole('button', { name: /Renumber All/ }));

    // Sports renumbers from the override; News continues from where it ended.
    const byId = new Map(staged.map((s) => [s.channelId, s.data.channel_number]));
    expect(byId.get(1)).toBe(50);
    expect(byId.get(2)).toBe(51);
    expect(byId.get(3)).toBe(52);
    expect(byId.get(4)).toBe(53);
  });
});
