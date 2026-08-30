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
import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { server, resetMockDataStore } from '../../test/mocks/server';
import { ActionEditor } from './ActionEditor';
import { RuleBuilder } from './RuleBuilder';
import { WHOLE_CHANNEL_NUMBER_RULE_MESSAGE } from '../../utils/channelNumber';
import type { Action, ChannelPipelineRule } from '../../types/channelPipeline';

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

/**
 * The save seam (bead `enhancedchannelmanager-ay3iq`).
 *
 * Refusing the entry at the field is only half the contract. `ActionEditor`
 * holds the typed text in component state, so a refused entry leaves the action
 * carrying NO start at all. `RuleBuilder.validate()` cannot see that text or its
 * message either, so it used to read the action as perfectly valid and save it.
 * The operator was shown a red error, saved anyway, and got automatic numbering
 * (Create Channel) or the group's current lowest (Sort Group): a different
 * result from the one they asked for, arrived at silently.
 *
 * These tests therefore drive the REAL `RuleBuilder`, not a harness around
 * `ActionEditor`. A test that stops at `onChange` output asserts the very state
 * transformation that enables the defect, and would stay green through it.
 *
 * Both save routes are covered, because they are separate entry points into
 * `handleSave`: the footer Save button, and the Enter key, which bubbles from
 * any input in the form to the builder's `onKeyDown`.
 */
describe('the RuleBuilder save seam refuses a rule whose start was refused', () => {
  /** A rule that saves cleanly, so only the starting-number entry is in play. */
  function ruleWith(actions: Action[]): Partial<ChannelPipelineRule> {
    return {
      name: 'Numbering rule',
      conditions: [{ type: 'always' }],
      actions,
    };
  }

  const CREATE_CHANNEL: Action = {
    type: 'create_channel',
    name_template: '{stream_name}',
    group_id: 1,
    channel_number: '100-99999',
  };
  const SORT_GROUP: Action = { type: 'sort_group', order: 'asc' };

  it('does not save a Create Channel action when Enter follows a fractional start', async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    render(
      <RuleBuilder
        rule={ruleWith([CREATE_CHANNEL]) as ChannelPipelineRule}
        onSave={onSave}
        onCancel={() => {}}
      />
    );

    const input = screen.getByLabelText('Starting channel number') as HTMLInputElement;
    await retype(user, input, '1.5');
    // Enter inside the field is the route Codex named: it bubbles to the
    // builder's key handler, which calls handleSave directly.
    await user.keyboard('{Enter}');

    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByText(WHOLE_CHANNEL_NUMBER_RULE_MESSAGE)).toBeInTheDocument();
  });

  it('does not save a Create Channel action when Save follows a fractional start', async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    render(
      <RuleBuilder
        rule={ruleWith([CREATE_CHANNEL]) as ChannelPipelineRule}
        onSave={onSave}
        onCancel={() => {}}
      />
    );

    const input = screen.getByLabelText('Starting channel number') as HTMLInputElement;
    await retype(user, input, '1.5');
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByText(WHOLE_CHANNEL_NUMBER_RULE_MESSAGE)).toBeInTheDocument();
  });

  it('does not save a Sort Group action when Save follows a fractional start', async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    render(
      <RuleBuilder
        rule={ruleWith([SORT_GROUP]) as ChannelPipelineRule}
        onSave={onSave}
        onCancel={() => {}}
      />
    );

    const input = screen.getByLabelText('Starting channel number') as HTMLInputElement;
    await retype(user, input, '1.5');
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByText(WHOLE_CHANNEL_NUMBER_RULE_MESSAGE)).toBeInTheDocument();
  });

  it('does not save a Sort Group action when Enter follows a fractional start', async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    render(
      <RuleBuilder
        rule={ruleWith([SORT_GROUP]) as ChannelPipelineRule}
        onSave={onSave}
        onCancel={() => {}}
      />
    );

    const input = screen.getByLabelText('Starting channel number') as HTMLInputElement;
    await retype(user, input, '1.5');
    await user.keyboard('{Enter}');

    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByText(WHOLE_CHANNEL_NUMBER_RULE_MESSAGE)).toBeInTheDocument();
  });

  it('saves once the refused entry is corrected, with the corrected start', async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    render(
      <RuleBuilder
        rule={ruleWith([CREATE_CHANNEL]) as ChannelPipelineRule}
        onSave={onSave}
        onCancel={() => {}}
      />
    );

    const input = screen.getByLabelText('Starting channel number') as HTMLInputElement;
    await retype(user, input, '1.5');
    await user.click(screen.getByRole('button', { name: /^save$/i }));
    expect(onSave).not.toHaveBeenCalled();

    await retype(user, input, '250');
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(onSave.mock.calls[0][0].actions[0].channel_number).toBe('250-99999');
    expect(screen.queryByText(WHOLE_CHANNEL_NUMBER_RULE_MESSAGE)).toBeNull();
  });

  it('saves a rule whose refused entry belongs to a field the action no longer shows', async () => {
    // Switching Channel Numbering back to Auto removes the field, and with it
    // the entry. A refusal that outlived its own field would block a save the
    // operator has no way to unblock: nothing red is left on screen to fix.
    const user = userEvent.setup();
    const onSave = vi.fn();
    render(
      <RuleBuilder
        rule={ruleWith([CREATE_CHANNEL]) as ChannelPipelineRule}
        onSave={onSave}
        onCancel={() => {}}
      />
    );

    const input = screen.getByLabelText('Starting channel number') as HTMLInputElement;
    await retype(user, input, '1.5');

    const numberingTrigger = screen
      .getByText('Channel Numbering')
      .closest('.action-field')
      ?.querySelector('.custom-select-trigger') as HTMLButtonElement;
    await user.click(numberingTrigger);
    await user.click(screen.getByRole('option', { name: /auto \(sequential/i }));

    expect(screen.queryByText(WHOLE_CHANNEL_NUMBER_RULE_MESSAGE)).toBeNull();
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    expect(onSave.mock.calls[0][0].actions[0].channel_number).toBeUndefined();
  });

  /**
   * Editing the action LIST must not discard a refusal (bead
   * `enhancedchannelmanager-ay3iq`, found by external adversarial review).
   *
   * The registry is keyed per editor INSTANCE, so it is only as stable as the
   * instances are. While the list was keyed by array index, deleting an earlier
   * action re-pointed every surviving editor at a different action: React kept
   * the index-0 instance and unmounted the LAST one, so the unmounted instance
   * released the only refusal entry while the surviving instance kept its
   * mount-derived text from the DELETED action. The red error vanished, Save
   * went through, and the surviving Create Channel action carried no
   * `channel_number` at all: automatic numbering, silently, which is the exact
   * result the refusal exists to prevent.
   *
   * The same substitution corrupts a reorder, so both list edits are pinned
   * here. There is no duplicate control in this editor.
   */
  const SKIP: Action = { type: 'skip' };

  it('still refuses the save after the action above the refused one is deleted', async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    render(
      <RuleBuilder
        rule={ruleWith([SKIP, CREATE_CHANNEL]) as ChannelPipelineRule}
        onSave={onSave}
        onCancel={() => {}}
      />
    );

    const input = screen.getByLabelText('Starting channel number') as HTMLInputElement;
    await retype(user, input, '1.5');
    expect(screen.getByText(WHOLE_CHANNEL_NUMBER_RULE_MESSAGE)).toBeInTheDocument();

    await user.click(screen.getAllByRole('button', { name: 'Remove action' })[0]);

    // The refused entry is still on screen, on the action that still exists.
    expect(screen.getByText(WHOLE_CHANNEL_NUMBER_RULE_MESSAGE)).toBeInTheDocument();
    expect(
      (screen.getByLabelText('Starting channel number') as HTMLInputElement).value
    ).toBe('1.5');

    await user.click(screen.getByRole('button', { name: /^save$/i }));

    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByText(WHOLE_CHANNEL_NUMBER_RULE_MESSAGE)).toBeInTheDocument();
  });

  it('still refuses the save after the refused action is moved up the list', async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    render(
      <RuleBuilder
        rule={ruleWith([SKIP, CREATE_CHANNEL]) as ChannelPipelineRule}
        onSave={onSave}
        onCancel={() => {}}
      />
    );

    const input = screen.getByLabelText('Starting channel number') as HTMLInputElement;
    await retype(user, input, '1.5');

    // The Create Channel action is second, so its own card carries "Move up".
    const moveUps = screen.getAllByRole('button', { name: 'Move up' });
    await user.click(moveUps[moveUps.length - 1]);

    expect(screen.getByText(WHOLE_CHANNEL_NUMBER_RULE_MESSAGE)).toBeInTheDocument();
    expect(
      (screen.getByLabelText('Starting channel number') as HTMLInputElement).value
    ).toBe('1.5');

    await user.click(screen.getByRole('button', { name: /^save$/i }));

    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByText(WHOLE_CHANNEL_NUMBER_RULE_MESSAGE)).toBeInTheDocument();
  });

  /**
   * Blocking the save is only half of what the operator needs: the point of
   * blocking is to put them on the entry they have to fix. `handleSave` routes
   * back to the Logic step, but `setCurrentStep` only SCHEDULES that render, so
   * focusing in the same tick aimed at an input still inside a `hidden`
   * container, which the browser refuses to focus. Pressing Save from a later
   * step left focus on the Save button with the red error off screen.
   */
  it('focuses the refused entry when Save is pressed from a later step', async () => {
    const user = userEvent.setup();
    const onSave = vi.fn();
    render(
      <RuleBuilder
        rule={ruleWith([CREATE_CHANNEL]) as ChannelPipelineRule}
        onSave={onSave}
        onCancel={() => {}}
      />
    );

    const input = screen.getByLabelText('Starting channel number') as HTMLInputElement;
    await retype(user, input, '1.5');

    await user.click(screen.getByTestId('rule-step-3'));
    await user.click(screen.getByRole('button', { name: /^save$/i }));

    expect(onSave).not.toHaveBeenCalled();
    await waitFor(() =>
      expect(screen.getByLabelText('Starting channel number')).toHaveFocus()
    );
  });
});
