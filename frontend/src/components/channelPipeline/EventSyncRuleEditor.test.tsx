/**
 * Tests for the Event Sync rule editor (bead ti939.1.5 — Phase 1A).
 *
 * Pins: the attach-threshold input clamp (>= 0.80 hard floor), live
 * auto-sync guidance (guidance only — this phase never toggles Dispatcharr
 * settings), the placeholder conditions/actions save convention, the
 * omit-patterns-when-builtin-defaults behavior, and the absence of any
 * apply/attach control.
 */
import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import {
  server,
  resetMockDataStore,
  mockDataStore,
  createMockChannelGroup,
} from '../../test/mocks/server';
import { EventSyncRuleEditor } from './EventSyncRuleEditor';
import type { ChannelPipelineRule } from '../../types/channelPipeline';

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  resetMockDataStore();
});
afterAll(() => server.close());

/** Stub GET /api/providers/group-settings with per-group auto-sync flags. */
function stubGroupSettings(autoSyncByGroupId: Record<number, boolean>) {
  server.use(
    http.get('/api/providers/group-settings', () =>
      HttpResponse.json(
        Object.fromEntries(
          Object.entries(autoSyncByGroupId).map(([groupId, autoSync]) => [
            groupId,
            {
              channel_group: Number(groupId),
              enabled: true,
              auto_channel_sync: autoSync,
              auto_sync_channel_start: null,
              m3u_account_id: 1,
              m3u_account_name: 'Provider A',
            },
          ])
        )
      )
    )
  );
}

function seedGroups() {
  mockDataStore.channelGroups.push(
    createMockChannelGroup({ id: 1, name: 'Master Events' }),
    createMockChannelGroup({ id: 2, name: 'Secondary Events' })
  );
}

const EXISTING_RULE: Partial<ChannelPipelineRule> = {
  id: 5,
  name: 'PPV Events',
  enabled: true,
  conditions: [{ type: 'always' }],
  actions: [{ type: 'skip' }],
  event_sync_config: {
    master_group_id: 1,
    secondary_group_ids: [2],
    time_window_minutes: 30,
    attach_threshold: 0.8,
    enabled: true,
  },
};

describe('EventSyncRuleEditor', () => {
  describe('attach threshold clamp', () => {
    it('clamps a value below the 0.80 floor back to 0.80 on blur', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={vi.fn()} onCancel={vi.fn()} />);

      await user.click(screen.getByText('Advanced'));
      const input = screen.getByLabelText(/attach threshold/i);
      await user.clear(input);
      await user.type(input, '0.5');
      await user.tab();

      expect(input).toHaveValue(0.8);
    });

    it('clamps a value above 1.0 down to 1.00 on blur', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={vi.fn()} onCancel={vi.fn()} />);

      await user.click(screen.getByText('Advanced'));
      const input = screen.getByLabelText(/attach threshold/i);
      await user.clear(input);
      await user.type(input, '1.5');
      await user.tab();

      expect(input).toHaveValue(1);
    });

    it('clamps the threshold in the saved config even without blur', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      const onSave = vi.fn();
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={onSave} onCancel={vi.fn()} />);

      await user.click(screen.getByText('Advanced'));
      const input = screen.getByLabelText(/attach threshold/i);
      await user.clear(input);
      await user.type(input, '0.2');
      await user.click(screen.getByRole('button', { name: 'Save' }));

      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      expect(onSave.mock.calls[0][0].event_sync_config.attach_threshold).toBe(0.8);
    });
  });

  describe('max_attach_per_run pass-through (ti939.2.1)', () => {
    it('preserves an API-set attach cap across a UI edit save', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      const onSave = vi.fn();
      const rule = {
        ...EXISTING_RULE,
        event_sync_config: {
          ...EXISTING_RULE.event_sync_config!,
          max_attach_per_run: 25,
        },
      };
      render(<EventSyncRuleEditor rule={rule} onSave={onSave} onCancel={vi.fn()} />);

      await user.click(screen.getByRole('button', { name: 'Save' }));

      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      expect(onSave.mock.calls[0][0].event_sync_config.max_attach_per_run).toBe(25);
    });

    it('omits the cap when the rule never had one (backend default applies)', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      const onSave = vi.fn();
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={onSave} onCancel={vi.fn()} />);

      await user.click(screen.getByRole('button', { name: 'Save' }));

      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      expect(onSave.mock.calls[0][0].event_sync_config).not.toHaveProperty(
        'max_attach_per_run'
      );
    });
  });

  describe('live auto-sync guidance (never toggles Dispatcharr settings)', () => {
    it('warns with enable-it-yourself guidance when the master group has auto-sync OFF', async () => {
      seedGroups();
      stubGroupSettings({ 1: false, 2: false });
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={vi.fn()} onCancel={vi.fn()} />);

      const warning = await screen.findByTestId('master-autosync-warning');
      expect(warning).toHaveTextContent(/auto-sync is/i);
      expect(warning).toHaveTextContent('ECM never toggles this setting for you');
    });

    it('shows an OK status when the master group has auto-sync ON', async () => {
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={vi.fn()} onCancel={vi.fn()} />);

      expect(
        await screen.findByText(/auto-sync is on — dispatcharr owns this group/i)
      ).toBeInTheDocument();
      expect(screen.queryByTestId('master-autosync-warning')).toBeNull();
    });

    it('warns with disable-it-yourself guidance when a secondary group has auto-sync ON', async () => {
      seedGroups();
      stubGroupSettings({ 1: true, 2: true });
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={vi.fn()} onCancel={vi.fn()} />);

      const warning = await screen.findByTestId('secondary-autosync-warning');
      expect(warning).toHaveTextContent('Secondary Events');
      expect(warning).toHaveTextContent('ECM never toggles this setting for you');
    });
  });

  describe('saving', () => {
    it('saves placeholder conditions/actions and omits patterns when the built-in defaults are selected', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      const onSave = vi.fn();
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={onSave} onCancel={vi.fn()} />);

      await user.click(screen.getByRole('button', { name: 'Save' }));

      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      const saved = onSave.mock.calls[0][0];
      expect(saved.name).toBe('PPV Events');
      expect(saved.conditions).toEqual([{ type: 'always' }]);
      expect(saved.actions).toEqual([{ type: 'skip' }]);
      expect(saved.event_sync_config).toEqual({
        master_group_id: 1,
        secondary_group_ids: [2],
        time_window_minutes: 30,
        attach_threshold: 0.8,
        enabled: true,
      });
      // Built-in default selection → no patterns key (backend defaults apply)
      expect(saved.event_sync_config).not.toHaveProperty('patterns');
    });

    it('sends the selected patterns explicitly when the selection differs from the defaults', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      const onSave = vi.fn();
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={onSave} onCancel={vi.fn()} />);

      // Deselect one of the two built-ins
      await user.click(screen.getByRole('checkbox', { name: /month-first date \(built-in\)/i }));
      await user.click(screen.getByRole('button', { name: 'Save' }));

      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      const config = onSave.mock.calls[0][0].event_sync_config;
      expect(config.patterns).toHaveLength(1);
      expect(config.patterns[0].name).toBe('slot-title-day-first-date');
      expect(config.patterns[0].title_pattern).toContain('(?P<title>');
    });

    it('blocks saving without a name', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      const onSave = vi.fn();
      render(
        <EventSyncRuleEditor
          rule={{ ...EXISTING_RULE, name: '' }}
          onSave={onSave}
          onCancel={vi.fn()}
        />
      );

      await user.click(screen.getByRole('button', { name: 'Save' }));

      expect(onSave).not.toHaveBeenCalled();
      expect(screen.getByRole('alert')).toHaveTextContent('Name is required');
    });
  });

  it('has NO apply or attach control anywhere (Phase 1A hard constraint)', async () => {
    seedGroups();
    stubGroupSettings({ 1: true, 2: false });
    render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={vi.fn()} onCancel={vi.fn()} />);

    await screen.findByText(/auto-sync is on — dispatcharr owns this group/i);
    expect(screen.queryByRole('button', { name: /apply|attach/i })).toBeNull();
  });
});
