/**
 * The two starting-number entries in the action editor are whole numbers, and a
 * fractional entry is REFUSED (beads `enhancedchannelmanager-ay3iq`,
 * `enhancedchannelmanager-j3pyx`).
 *
 * Create Channel's "Starting from..." writes the entry into a `min-max` range
 * (`"100-99999"`), and Sort Group's "Starting Channel Number" seeds a run that
 * counts up by one. Both used to read their field with `parseInt`, so a typed
 * `1.5` became `1` with nothing anywhere saying the value had changed.
 *
 * For Create Channel the truncation did a second thing: the executor reads a
 * range as two WHOLE numbers, so `backend/channel_pipeline_schema.py` refuses a
 * range naming a tenth with an operator-facing sentence of its own. `parseInt`
 * ran first and turned every such entry into a whole range, so that rejection
 * could never be reached from the UI: a refusal nobody could see.
 *
 * What each test pins is that the entry is refused rather than altered: the
 * message is shown, the field still holds what the operator typed, and the
 * action carries NO start rather than one nobody asked for. A test that only
 * checked the message would still pass while `1.5` was quietly stored as `1`.
 */
import { useState } from 'react';
import { describe, it, expect, beforeAll, afterAll, afterEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { server, resetMockDataStore } from '../../test/mocks/server';
import { ActionEditor } from './ActionEditor';
import { WHOLE_CHANNEL_NUMBER_RULE_MESSAGE } from '../../utils/channelNumber';
import type { Action } from '../../types/channelPipeline';

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  resetMockDataStore();
});
afterAll(() => server.close());

/**
 * Render the editor the way RuleBuilder does: the parent owns the action and
 * feeds every change straight back in. A spy-only `onChange` would leave the
 * action frozen at its initial value and hide exactly the disagreement these
 * tests are about. The returned array is every action the editor has produced,
 * newest last.
 */
function renderActionEditor(initial: Action): Action[] {
  const calls: Action[] = [];

  function Harness() {
    const [action, setAction] = useState<Action>(initial);
    return (
      <ActionEditor
        action={action}
        onChange={(next) => {
          calls.push(next);
          setAction(next);
        }}
        onRemove={() => {}}
      />
    );
  }

  render(<Harness />);
  return calls;
}

async function retype(
  user: ReturnType<typeof userEvent.setup>,
  input: HTMLInputElement,
  value: string,
) {
  await user.clear(input);
  await user.type(input, value);
}

function lastAction(calls: Action[]): Action {
  expect(calls.length).toBeGreaterThan(0);
  return calls[calls.length - 1];
}

describe('Create Channel "Starting from..." channel number', () => {
  const CREATE_CHANNEL: Action = {
    type: 'create_channel',
    name_template: '{stream_name}',
    group_id: 1,
    channel_number: '100-99999',
  };

  it('refuses a fractional start instead of numbering from its integer part', async () => {
    const user = userEvent.setup();
    const calls = renderActionEditor(CREATE_CHANNEL);

    const input = screen.getByLabelText('Starting channel number') as HTMLInputElement;
    await retype(user, input, '1.5');

    expect(screen.getByRole('alert')).toHaveTextContent(WHOLE_CHANNEL_NUMBER_RULE_MESSAGE);
    // The entry survives on screen. A refusal that also erased what was typed
    // would be its own silent alteration.
    expect(input).toHaveValue(1.5);
    // And nothing bogus is stored. `1-99999` here is the defect itself; the
    // previous `100-99999` would be just as wrong, since the operator has
    // asked for neither.
    expect(lastAction(calls).channel_number).toBeUndefined();
  });

  it('refuses a start below 1 rather than storing a range from 0', async () => {
    const user = userEvent.setup();
    const calls = renderActionEditor(CREATE_CHANNEL);

    const input = screen.getByLabelText('Starting channel number') as HTMLInputElement;
    await retype(user, input, '0');

    expect(screen.getByRole('alert')).toHaveTextContent(WHOLE_CHANNEL_NUMBER_RULE_MESSAGE);
    expect(lastAction(calls).channel_number).toBeUndefined();
  });

  it('still stores a whole start as the range the executor reads', async () => {
    const user = userEvent.setup();
    const calls = renderActionEditor(CREATE_CHANNEL);

    const input = screen.getByLabelText('Starting channel number') as HTMLInputElement;
    await retype(user, input, '250');

    expect(screen.queryByRole('alert')).toBeNull();
    expect(lastAction(calls).channel_number).toBe('250-99999');
  });

  it('says nothing while the field is empty, and stores no range', async () => {
    const user = userEvent.setup();
    const calls = renderActionEditor(CREATE_CHANNEL);

    const input = screen.getByLabelText('Starting channel number') as HTMLInputElement;
    await user.clear(input);

    expect(screen.queryByRole('alert')).toBeNull();
    expect(lastAction(calls).channel_number).toBeUndefined();
  });
});

describe('Sort Group starting number', () => {
  const SORT_GROUP: Action = { type: 'sort_group', order: 'asc' };

  it('refuses a fractional start instead of renumbering from its integer part', async () => {
    const user = userEvent.setup();
    const calls = renderActionEditor(SORT_GROUP);

    const input = screen.getByLabelText('Starting channel number') as HTMLInputElement;
    await retype(user, input, '1.5');

    expect(screen.getByRole('alert')).toHaveTextContent(WHOLE_CHANNEL_NUMBER_RULE_MESSAGE);
    expect(input).toHaveValue(1.5);
    expect(lastAction(calls).starting_number).toBeUndefined();
  });

  it('refuses a start below 1 rather than storing it', async () => {
    const user = userEvent.setup();
    const calls = renderActionEditor(SORT_GROUP);

    const input = screen.getByLabelText('Starting channel number') as HTMLInputElement;
    await retype(user, input, '0');

    expect(screen.getByRole('alert')).toHaveTextContent(WHOLE_CHANNEL_NUMBER_RULE_MESSAGE);
    expect(lastAction(calls).starting_number).toBeUndefined();
  });

  it('still stores a whole start', async () => {
    const user = userEvent.setup();
    const calls = renderActionEditor(SORT_GROUP);

    const input = screen.getByLabelText('Starting channel number') as HTMLInputElement;
    await retype(user, input, '20');

    expect(screen.queryByRole('alert')).toBeNull();
    expect(lastAction(calls).starting_number).toBe(20);
  });

  it('says nothing while the field is blank, which is the auto behaviour', async () => {
    const user = userEvent.setup();
    const calls = renderActionEditor({ ...SORT_GROUP, starting_number: 12 });

    const input = screen.getByLabelText('Starting channel number') as HTMLInputElement;
    await user.clear(input);

    expect(screen.queryByRole('alert')).toBeNull();
    expect(lastAction(calls).starting_number).toBeUndefined();
  });
});
