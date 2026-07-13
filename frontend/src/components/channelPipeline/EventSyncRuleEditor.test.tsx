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
import { render, screen, waitFor, within } from '@testing-library/react';
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
import type { ProviderGroupScopeRow } from '../../services/api';

beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  resetMockDataStore();
});
afterAll(() => server.close());

/** Stub GET /api/providers/group-settings/by-provider with (provider, group)
 * junction rows (bead 38dzi). The picker groups rows by channel_group_id; a
 * single row per group renders as a flat WHOLE-GROUP (m3u_account_id null)
 * option with data-testid `psg-<role>-<groupId>-any`. */
function stubJunctions(rows: ProviderGroupScopeRow[]) {
  server.use(
    http.get('/api/providers/group-settings/by-provider', () =>
      HttpResponse.json(rows)
    )
  );
}

/** Convenience: one single-provider junction per group ('Provider A', id 1,
 * enabled) with the given auto-sync flag. Use `stubJunctionsFull` for tests
 * that need to control the `enabled` flag itself (bead x82s3). */
function stubGroupSettings(autoSyncByGroupId: Record<number, boolean>) {
  stubJunctions(
    Object.entries(autoSyncByGroupId).map(([groupId, autoSync]) => ({
      m3u_account_id: 1,
      m3u_account_name: 'Provider A',
      channel_group_id: Number(groupId),
      auto_channel_sync: autoSync,
      enabled: true,
      stream_count: 10,
    }))
  );
}

/** One single-provider junction per group with explicit `enabled` +
 * `auto_channel_sync` (bead x82s3 — enabled-groups filter). */
function stubGroupSettingsFull(
  settings: Record<number, { enabled: boolean; auto_channel_sync: boolean }>
) {
  stubJunctions(
    Object.entries(settings).map(([groupId, s]) => ({
      m3u_account_id: 1,
      m3u_account_name: 'Provider A',
      channel_group_id: Number(groupId),
      auto_channel_sync: s.auto_channel_sync,
      enabled: s.enabled,
      stream_count: 10,
    }))
  );
}

function seedGroups() {
  mockDataStore.channelGroups.push(
    createMockChannelGroup({ id: 1, name: 'Master Events' }),
    createMockChannelGroup({ id: 2, name: 'Secondary Events' })
  );
}

/** seedGroups() plus a third group (id 3) for the enabled-groups-filter tests. */
function seedGroupsWithDisabled() {
  seedGroups();
  mockDataStore.channelGroups.push(createMockChannelGroup({ id: 3, name: 'Disabled Group' }));
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
    it('preserves a value below the 0.80 default on blur (operator-authoritative)', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={vi.fn()} onCancel={vi.fn()} />);

      await user.click(screen.getByText('Score threshold'));
      const input = screen.getByLabelText(/attach threshold/i);
      await user.clear(input);
      await user.type(input, '0.5');
      await user.tab();

      // 0.80 is the default, not a hard floor — the operator's 0.50 stands.
      expect(input).toHaveValue(0.5);
    });

    it('clamps a value above 1.0 down to 1.00 on blur', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={vi.fn()} onCancel={vi.fn()} />);

      await user.click(screen.getByText('Score threshold'));
      const input = screen.getByLabelText(/attach threshold/i);
      await user.clear(input);
      await user.type(input, '1.5');
      await user.tab();

      expect(input).toHaveValue(1);
    });

    it('clamps the threshold to the [0,1] bounds in the saved config', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      const onSave = vi.fn();
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={onSave} onCancel={vi.fn()} />);

      await user.click(screen.getByText('Score threshold'));
      const input = screen.getByLabelText(/attach threshold/i);
      await user.clear(input);
      // A sub-default value is honored (only out-of-[0,1] values are clamped).
      await user.type(input, '0.65');
      await user.click(screen.getByRole('button', { name: 'Save' }));

      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      expect(onSave.mock.calls[0][0].event_sync_config.attach_threshold).toBe(0.65);
    });
  });

  describe('ignore time window toggle (krkm4)', () => {
    it('disables the time-window input and saves enforce_time_window=false when checked', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      const onSave = vi.fn();
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={onSave} onCancel={vi.fn()} />);

      await user.click(screen.getByText('Time tuning'));
      const windowInput = screen.getByLabelText(/time window \(minutes\)/i);
      expect(windowInput).not.toBeDisabled();

      await user.click(screen.getByTestId('event-sync-ignore-time-window'));
      expect(windowInput).toBeDisabled();

      await user.click(screen.getByRole('button', { name: 'Save' }));
      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      expect(
        onSave.mock.calls[0][0].event_sync_config.enforce_time_window
      ).toBe(false);
    });

    it('omits enforce_time_window when left enforced (absent means true)', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      const onSave = vi.fn();
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={onSave} onCancel={vi.fn()} />);

      await user.click(screen.getByRole('button', { name: 'Save' }));
      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      expect(
        onSave.mock.calls[0][0].event_sync_config
      ).not.toHaveProperty('enforce_time_window');
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

  describe('master-group self-attach (bead 6xxmp)', () => {
    it('saves with an EMPTY secondary list when the flag is on (bead 3ux85)', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      const onSave = vi.fn();
      const rule = {
        ...EXISTING_RULE,
        event_sync_config: {
          ...EXISTING_RULE.event_sync_config!,
          secondary_group_ids: [],
          include_master_group_streams: true,
        },
      };
      render(<EventSyncRuleEditor rule={rule} onSave={onSave} onCancel={vi.fn()} />);

      // No secondary selected + flag on => save is allowed.
      await user.click(screen.getByRole('button', { name: 'Save' }));
      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      const cfg = onSave.mock.calls[0][0].event_sync_config;
      expect(cfg.secondary).toEqual([]);
      expect(cfg).not.toHaveProperty('secondary_group_ids');
      expect(cfg.include_master_group_streams).toBe(true);
    });

    it('blocks save with an empty secondary list when the flag is OFF', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      const onSave = vi.fn();
      const rule = {
        ...EXISTING_RULE,
        event_sync_config: {
          ...EXISTING_RULE.event_sync_config!,
          secondary_group_ids: [],
        },
      };
      render(<EventSyncRuleEditor rule={rule} onSave={onSave} onCancel={vi.fn()} />);

      await user.click(screen.getByRole('button', { name: 'Save' }));
      expect(onSave).not.toHaveBeenCalled();
    });

    it('defaults OFF and omits include_master_group_streams when never set', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      const onSave = vi.fn();
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={onSave} onCancel={vi.fn()} />);

      await user.click(screen.getByText('Scope extension'));
      expect(
        screen.getByTestId('event-sync-include-master-group-streams')
      ).not.toBeChecked();

      await user.click(screen.getByRole('button', { name: 'Save' }));
      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      expect(onSave.mock.calls[0][0].event_sync_config).not.toHaveProperty(
        'include_master_group_streams'
      );
    });

    it('emits include_master_group_streams: true when the operator checks the box', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      const onSave = vi.fn();
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={onSave} onCancel={vi.fn()} />);

      await user.click(screen.getByText('Scope extension'));
      await user.click(
        screen.getByTestId('event-sync-include-master-group-streams')
      );
      await user.click(screen.getByRole('button', { name: 'Save' }));

      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      expect(
        onSave.mock.calls[0][0].event_sync_config.include_master_group_streams
      ).toBe(true);
    });

    it('initializes checked from a stored true and round-trips it', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      const onSave = vi.fn();
      const rule = {
        ...EXISTING_RULE,
        event_sync_config: {
          ...EXISTING_RULE.event_sync_config!,
          include_master_group_streams: true,
        },
      };
      render(<EventSyncRuleEditor rule={rule} onSave={onSave} onCancel={vi.fn()} />);

      await user.click(screen.getByText('Scope extension'));
      expect(
        screen.getByTestId('event-sync-include-master-group-streams')
      ).toBeChecked();

      await user.click(screen.getByRole('button', { name: 'Save' }));
      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      expect(
        onSave.mock.calls[0][0].event_sync_config.include_master_group_streams
      ).toBe(true);
    });
  });

  describe('parse-master-from-stream opt-in', () => {
    it('defaults OFF and omits the key when never set', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      const onSave = vi.fn();
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={onSave} onCancel={vi.fn()} />);

      await user.click(screen.getByText('Scope extension'));
      expect(
        screen.getByTestId('event-sync-parse-master-from-stream')
      ).not.toBeChecked();

      await user.click(screen.getByRole('button', { name: 'Save' }));
      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      expect(onSave.mock.calls[0][0].event_sync_config).not.toHaveProperty(
        'parse_master_from_stream'
      );
    });

    it('emits parse_master_from_stream: true when checked', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      const onSave = vi.fn();
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={onSave} onCancel={vi.fn()} />);

      await user.click(screen.getByText('Scope extension'));
      await user.click(
        screen.getByTestId('event-sync-parse-master-from-stream')
      );
      await user.click(screen.getByRole('button', { name: 'Save' }));

      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      expect(
        onSave.mock.calls[0][0].event_sync_config.parse_master_from_stream
      ).toBe(true);
    });
  });

  describe('assume-current-date opt-in (dateless listings)', () => {
    it('defaults OFF and omits assume_current_date when never set', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      const onSave = vi.fn();
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={onSave} onCancel={vi.fn()} />);

      await user.click(screen.getByText('Date handling'));
      expect(
        screen.getByTestId('event-sync-assume-current-date')
      ).not.toBeChecked();

      await user.click(screen.getByRole('button', { name: 'Save' }));
      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      expect(onSave.mock.calls[0][0].event_sync_config).not.toHaveProperty(
        'assume_current_date'
      );
    });

    it('emits assume_current_date: true when checked', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      const onSave = vi.fn();
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={onSave} onCancel={vi.fn()} />);

      await user.click(screen.getByText('Date handling'));
      await user.click(screen.getByTestId('event-sync-assume-current-date'));
      await user.click(screen.getByRole('button', { name: 'Save' }));

      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      expect(
        onSave.mock.calls[0][0].event_sync_config.assume_current_date
      ).toBe(true);
    });
  });

  describe('auto-run opt-in (ti939.3.1)', () => {
    it('defaults OFF and omits auto_run for a rule that never had the key', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      const onSave = vi.fn();
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={onSave} onCancel={vi.fn()} />);

      await user.click(screen.getByText('Automation'));
      expect(screen.getByTestId('event-sync-auto-run')).not.toBeChecked();

      await user.click(screen.getByRole('button', { name: 'Save' }));
      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      expect(onSave.mock.calls[0][0].event_sync_config).not.toHaveProperty('auto_run');
    });

    it('emits auto_run: true when the operator checks the box', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      const onSave = vi.fn();
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={onSave} onCancel={vi.fn()} />);

      await user.click(screen.getByText('Automation'));
      await user.click(screen.getByTestId('event-sync-auto-run'));
      await user.click(screen.getByRole('button', { name: 'Save' }));

      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      expect(onSave.mock.calls[0][0].event_sync_config.auto_run).toBe(true);
    });

    it('initializes checked from a stored auto_run: true and round-trips it untouched', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      const onSave = vi.fn();
      const rule = {
        ...EXISTING_RULE,
        event_sync_config: {
          ...EXISTING_RULE.event_sync_config!,
          auto_run: true,
        },
      };
      render(<EventSyncRuleEditor rule={rule} onSave={onSave} onCancel={vi.fn()} />);

      await user.click(screen.getByText('Automation'));
      expect(screen.getByTestId('event-sync-auto-run')).toBeChecked();

      await user.click(screen.getByRole('button', { name: 'Save' }));
      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      expect(onSave.mock.calls[0][0].event_sync_config.auto_run).toBe(true);
    });

    it('preserves a stored explicit auto_run: false on an untouched save (z4y4a round-trip)', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      const onSave = vi.fn();
      const rule = {
        ...EXISTING_RULE,
        event_sync_config: {
          ...EXISTING_RULE.event_sync_config!,
          auto_run: false,
        },
      };
      render(<EventSyncRuleEditor rule={rule} onSave={onSave} onCancel={vi.fn()} />);

      await user.click(screen.getByRole('button', { name: 'Save' }));
      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      expect(onSave.mock.calls[0][0].event_sync_config.auto_run).toBe(false);
    });

    it('turning a stored auto_run: true OFF saves an explicit false', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      const onSave = vi.fn();
      const rule = {
        ...EXISTING_RULE,
        event_sync_config: {
          ...EXISTING_RULE.event_sync_config!,
          auto_run: true,
        },
      };
      render(<EventSyncRuleEditor rule={rule} onSave={onSave} onCancel={vi.fn()} />);

      await user.click(screen.getByText('Automation'));
      await user.click(screen.getByTestId('event-sync-auto-run'));
      await user.click(screen.getByRole('button', { name: 'Save' }));

      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      expect(onSave.mock.calls[0][0].event_sync_config.auto_run).toBe(false);
    });

    it('explains the unattended behavior honestly (default off, notifications, breaker)', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={vi.fn()} onCancel={vi.fn()} />);

      await user.click(screen.getByText('Automation'));
      expect(
        screen.getByText(/enable it only after you trust/i)
      ).toBeInTheDocument();
      expect(screen.getByText(/warning notifications/i)).toBeInTheDocument();
      expect(screen.getByText(/circuit breaker/i)).toBeInTheDocument();
      expect(screen.getByText(/attaches on the next run/i)).toBeInTheDocument();
    });
  });

  describe('inline auto-sync status (picker; toggles ONLY via the confirmed fix)', () => {
    it('shows an inline mismatch + Fix when the master scope has auto-sync OFF', async () => {
      seedGroups();
      stubGroupSettings({ 1: false, 2: false });
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={vi.fn()} onCancel={vi.fn()} />);

      // The migrated master scope (whole group 1) renders checked with an OFF
      // junction — a master needs it ON, so the picker shows the inline fix.
      const fix = await screen.findByRole('button', {
        name: /turn auto-sync ON for Provider A/i,
      });
      expect(fix).toBeInTheDocument();
      expect(screen.getByText(/a master needs it ON/i)).toBeInTheDocument();
    });

    it('shows an OK sync badge (no Fix) when the master scope has auto-sync ON', async () => {
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={vi.fn()} onCancel={vi.fn()} />);

      const master = await screen.findByTestId('psg-master');
      expect(await within(master).findByText(/Auto-sync ON ✓/)).toBeInTheDocument();
      expect(
        within(master).queryByRole('button', { name: /turn auto-sync/i })
      ).toBeNull();
    });

    it('shows an inline mismatch + Fix when a secondary scope has auto-sync ON', async () => {
      seedGroups();
      stubGroupSettings({ 1: true, 2: true });
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={vi.fn()} onCancel={vi.fn()} />);

      const secondary = await screen.findByTestId('psg-secondary');
      expect(
        await within(secondary).findByRole('button', {
          name: /turn auto-sync OFF for Provider A/i,
        })
      ).toBeInTheDocument();
      expect(within(secondary).getByText(/a secondary needs it OFF/i)).toBeInTheDocument();
    });
  });

  describe('guided auto-sync fix (ti939.3.4 — confirmed, never a side effect)', () => {
    /** Stub the toggle endpoint, recording every request body. */
    function stubToggleEndpoint(calls: unknown[]) {
      server.use(
        http.post('/api/m3u/accounts/1/group-auto-sync-toggle', async ({ request }) => {
          const body = await request.json();
          calls.push(body);
          return HttpResponse.json({
            changed: true,
            channel_group_id: (body as { channel_group_id: number }).channel_group_id,
            group_name: 'Secondary Events',
            account_id: 1,
            account_name: 'Provider A',
            auto_channel_sync: (body as { auto_channel_sync: boolean }).auto_channel_sync,
          });
        })
      );
    }

    it('the picker Fix affordance only OPENS the confirmation dialog — nothing is written yet', async () => {
      const user = userEvent.setup();
      const calls: unknown[] = [];
      seedGroups();
      stubGroupSettings({ 1: true, 2: true });
      stubToggleEndpoint(calls);
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={vi.fn()} onCancel={vi.fn()} />);

      await user.click(
        await screen.findByRole('button', { name: /turn auto-sync OFF for Provider A/i })
      );

      // Dialog states exactly what will change and why, including the
      // consequence and the snapshot-restore recovery note.
      const dialog = screen.getByRole('alertdialog');
      expect(dialog).toHaveTextContent('Secondary Events');
      expect(dialog).toHaveTextContent('Provider A');
      expect(dialog).toHaveTextContent(/stop creating duplicate channels/i);
      expect(dialog).toHaveTextContent(/may be removed by Dispatcharr/i);
      expect(dialog).toHaveTextContent(/snapshot restore does .*not.* revert/i);
      expect(dialog).toHaveTextContent(/journal entry is the recovery breadcrumb/i);
      expect(calls).toHaveLength(0);
    });

    it('Cancel closes the dialog without calling the toggle API', async () => {
      const user = userEvent.setup();
      const calls: unknown[] = [];
      seedGroups();
      stubGroupSettings({ 1: true, 2: true });
      stubToggleEndpoint(calls);
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={vi.fn()} onCancel={vi.fn()} />);

      await user.click(
        await screen.findByRole('button', { name: /turn auto-sync OFF for Provider A/i })
      );
      const dialog = screen.getByRole('alertdialog');
      await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));

      expect(screen.queryByRole('alertdialog')).toBeNull();
      expect(calls).toHaveLength(0);
    });

    it('Confirm sends confirm:true for the OFF direction and the inline fix clears after refetch', async () => {
      const user = userEvent.setup();
      const calls: unknown[] = [];
      seedGroups();
      stubGroupSettings({ 1: true, 2: true });
      stubToggleEndpoint(calls);
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={vi.fn()} onCancel={vi.fn()} />);

      await user.click(
        await screen.findByRole('button', { name: /turn auto-sync OFF for Provider A/i })
      );
      // The refetch after the confirmed toggle sees the FIXED junction.
      stubGroupSettings({ 1: true, 2: false });
      await user.click(screen.getByTestId('autosync-fix-confirm'));

      await waitFor(() => expect(calls).toHaveLength(1));
      expect(calls[0]).toEqual({
        channel_group_id: 2,
        auto_channel_sync: false,
        confirm: true,
      });
      // The inline mismatch fix clears — the editor refetched the junctions.
      await waitFor(() =>
        expect(
          screen.queryByRole('button', { name: /turn auto-sync OFF for Provider A/i })
        ).toBeNull()
      );
      expect(screen.queryByRole('alertdialog')).toBeNull();
    });

    it('Confirm sends confirm:true for the ON direction (master fix)', async () => {
      const user = userEvent.setup();
      const calls: unknown[] = [];
      seedGroups();
      stubGroupSettings({ 1: false, 2: false });
      stubToggleEndpoint(calls);
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={vi.fn()} onCancel={vi.fn()} />);

      await user.click(
        await screen.findByRole('button', { name: /turn auto-sync ON for Provider A/i })
      );
      const dialog = screen.getByRole('alertdialog');
      expect(dialog).toHaveTextContent(/begin creating and managing channels/i);
      stubGroupSettings({ 1: true, 2: false });
      await user.click(screen.getByTestId('autosync-fix-confirm'));

      await waitFor(() => expect(calls).toHaveLength(1));
      expect(calls[0]).toEqual({
        channel_group_id: 1,
        auto_channel_sync: true,
        confirm: true,
      });
      await waitFor(() =>
        expect(
          screen.queryByRole('button', { name: /turn auto-sync ON for Provider A/i })
        ).toBeNull()
      );
    });

    it('saving a rule NEVER calls the toggle endpoint, even with a mismatch showing', async () => {
      const user = userEvent.setup();
      const calls: unknown[] = [];
      seedGroups();
      stubGroupSettings({ 1: false, 2: true });
      stubToggleEndpoint(calls);
      const onSave = vi.fn();
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={onSave} onCancel={vi.fn()} />);

      await screen.findByRole('button', { name: /turn auto-sync ON for Provider A/i });
      await user.click(screen.getByRole('button', { name: 'Save' }));

      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      expect(calls).toHaveLength(0);
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
      // bead 38dzi: the editor emits the nested provider-scoped shape; the
      // legacy flat master_group_id/secondary_group_ids migrate to
      // whole-group scopes (m3u_account_id null) and the flat keys are gone.
      expect(saved.event_sync_config).toEqual({
        master: { group_id: 1, m3u_account_id: null },
        secondary: [{ group_id: 2, m3u_account_id: null }],
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

      // Deselect one of the built-ins; the remaining built-ins are emitted.
      await user.click(screen.getByRole('checkbox', { name: /month-first date \(built-in\)/i }));
      await user.click(screen.getByRole('button', { name: 'Save' }));

      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      const config = onSave.mock.calls[0][0].event_sync_config;
      const names = config.patterns.map((p: { name: string }) => p.name);
      expect(names).toContain('slot-title-day-first-date');
      expect(names).not.toContain('slot-title-month-first-date');
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

  describe('API-authored multi-pattern round-trip (z4y4a)', () => {
    /** A hand-/API-authored config the UI cannot fully express: two shared
     * custom patterns (the editor edits only the first) plus a two-pattern
     * per-group override list (the editor edits only patterns[0]). */
    const API_CONFIG = {
      master_group_id: 1,
      secondary_group_ids: [2],
      patterns: [
        {
          name: 'api-primary',
          title_pattern: '^(?P<title>.+?)\\s*@',
          time_pattern: '(?P<hour>\\d{1,2}):(?P<minute>\\d{2})',
          date_pattern: '(?P<day>\\d{1,2})\\s+(?P<month>[A-Za-z]{3,9})',
        },
        // Nameless second shared pattern — no editor control can express it.
        { title_pattern: '^(?P<title>.+)$' },
      ],
      group_patterns: {
        '2': [
          { name: 'g2-first', title_pattern: 'x(?P<title>.+)' },
          { name: 'g2-extra', title_pattern: 'y(?P<title>.+)' },
        ],
      },
      time_window_minutes: 45,
      attach_threshold: 0.9,
      enabled: true,
    };

    const apiRule: Partial<ChannelPipelineRule> = {
      ...EXISTING_RULE,
      event_sync_config: API_CONFIG,
    };

    it('survives open → save byte-identically when nothing was edited', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      const onSave = vi.fn();
      render(<EventSyncRuleEditor rule={apiRule} onSave={onSave} onCancel={vi.fn()} />);

      await user.click(screen.getByRole('button', { name: 'Save' }));

      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      const saved = onSave.mock.calls[0][0].event_sync_config;
      // The group scopes migrate to the nested provider-scoped shape (the
      // flat keys are gone), but everything else round-trips content-identically...
      expect(saved.master).toEqual({ group_id: 1, m3u_account_id: null });
      expect(saved.secondary).toEqual([{ group_id: 2, m3u_account_id: null }]);
      expect(saved).not.toHaveProperty('master_group_id');
      expect(saved).not.toHaveProperty('secondary_group_ids');
      expect(saved.time_window_minutes).toBe(45);
      expect(saved.attach_threshold).toBe(0.9);
      // ...and the arrays the UI cannot express are passed through VERBATIM
      // (same objects — not a re-built lossy approximation).
      expect(saved.patterns).toBe(API_CONFIG.patterns);
      expect(saved.group_patterns['2']).toBe(API_CONFIG.group_patterns['2']);
      expect(JSON.stringify(saved.patterns)).toBe(JSON.stringify(API_CONFIG.patterns));
      expect(JSON.stringify(saved.group_patterns)).toBe(
        JSON.stringify(API_CONFIG.group_patterns)
      );
    });

    it('does NOT silently re-add built-ins to an all-custom config', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      const onSave = vi.fn();
      render(<EventSyncRuleEditor rule={apiRule} onSave={onSave} onCancel={vi.fn()} />);

      // The built-in checkboxes reflect the saved selection: none selected.
      expect(screen.getByRole('checkbox', { name: /day-first date \(built-in\)/i }))
        .not.toBeChecked();
      expect(screen.getByRole('checkbox', { name: /month-first date \(built-in\)/i }))
        .not.toBeChecked();

      await user.click(screen.getByRole('button', { name: 'Save' }));

      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      const saved = onSave.mock.calls[0][0].event_sync_config;
      const savedNames = saved.patterns.map((p: { name?: string }) => p.name);
      expect(savedNames).not.toContain('slot-title-day-first-date');
      expect(savedNames).not.toContain('slot-title-month-first-date');
      expect(saved.patterns).toHaveLength(2);
    });

    it('preserves the inexpressible extras (and their names) when the editable first custom IS edited', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      const onSave = vi.fn();
      render(<EventSyncRuleEditor rule={apiRule} onSave={onSave} onCancel={vi.fn()} />);

      // Edit the first custom shared pattern's title regex.
      await user.click(screen.getByText('Custom shared pattern (regex fallback)'));
      const titleInput = screen.getAllByLabelText('Title pattern')
        .find(el => el.id.includes('-custom-title'))!;
      await user.clear(titleInput);
      await user.type(titleInput, '^EDITED (?P<title>.+)$');
      await user.click(screen.getByRole('button', { name: 'Save' }));

      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      const saved = onSave.mock.calls[0][0].event_sync_config;
      // Edited first custom keeps its API-authored name; the trailing extra
      // survives untouched; nothing re-ordered around it, no built-ins added.
      expect(saved.patterns).toEqual([
        {
          name: 'api-primary',
          title_pattern: '^EDITED (?P<title>.+)$',
          time_pattern: '(?P<hour>\\d{1,2}):(?P<minute>\\d{2})',
          date_pattern: '(?P<day>\\d{1,2})\\s+(?P<month>[A-Za-z]{3,9})',
        },
        { title_pattern: '^(?P<title>.+)$' },
      ]);
      // Untouched group_patterns still round-trip verbatim.
      expect(saved.group_patterns).toEqual(API_CONFIG.group_patterns);
    });

    it('preserves per-group extras when the editable override is edited', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      const onSave = vi.fn();
      render(<EventSyncRuleEditor rule={apiRule} onSave={onSave} onCancel={vi.fn()} />);

      await user.click(screen.getByText('Per-group pattern overrides'));
      // Open the secondary group's override editor and change its title.
      await user.click(screen.getByText(/Secondary Events/, { selector: 'summary' }));
      const overrideTitle = screen.getAllByLabelText('Title pattern')
        .find(el => el.id.includes('-ov-2-'))!;
      await user.clear(overrideTitle);
      await user.type(overrideTitle, 'z(?P<title>.+)');
      await user.click(screen.getByRole('button', { name: 'Save' }));

      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      const saved = onSave.mock.calls[0][0].event_sync_config;
      expect(saved.group_patterns['2']).toEqual([
        { name: 'g2-first', title_pattern: 'z(?P<title>.+)' },
        { name: 'g2-extra', title_pattern: 'y(?P<title>.+)' },
      ]);
      // Untouched shared patterns still round-trip verbatim.
      expect(saved.patterns).toEqual(API_CONFIG.patterns);
    });

    it('surfaces the preserved inexpressible patterns with a read-only indicator', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      render(<EventSyncRuleEditor rule={apiRule} onSave={vi.fn()} onCancel={vi.fn()} />);

      await user.click(screen.getByText('Custom shared pattern (regex fallback)'));
      const sharedIndicator = screen.getByTestId('custom-shared-extras');
      expect(sharedIndicator).toHaveTextContent(/preserved as saved/i);

      await user.click(screen.getByText('Per-group pattern overrides'));
      await user.click(screen.getByText(/Secondary Events/, { selector: 'summary' }));
      const groupIndicator = screen.getByTestId('group-override-extras-2');
      expect(groupIndicator).toHaveTextContent(/preserved as saved/i);
      expect(groupIndicator).toHaveTextContent('g2-extra');
    });

    it('a UI-authored single-custom config still saves exactly as before (no extras machinery)', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      const onSave = vi.fn();
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={onSave} onCancel={vi.fn()} />);

      await user.click(screen.getByText('Custom shared pattern (regex fallback)'));
      const customTitle = screen.getAllByLabelText('Title pattern')
        .find(el => el.id.includes('-custom-title'))!;
      await user.type(customTitle, '^(?P<title>.+?)\\s*@');
      await user.click(screen.getByRole('button', { name: 'Save' }));

      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      const saved = onSave.mock.calls[0][0].event_sync_config;
      // Built-in defaults still selected + one new custom → shipped verbatim
      // then the UI-named custom, exactly the pre-z4y4a shape.
      const names = saved.patterns.map((p: { name?: string }) => p.name);
      expect(names).toEqual([
        'slot-title-day-first-date',
        'slot-title-month-first-date',
        'slot-title-numeric-date',
        'custom-shared',
      ]);
      expect(saved.patterns[3].title_pattern).toBe('^(?P<title>.+?)\\s*@');
    });
  });

  describe('dummy EPG profile reference (ti939.3.3)', () => {
    function stubDummyProfiles() {
      server.use(
        http.get('/api/dummy-epg/profiles', () =>
          HttpResponse.json([
            { id: 7, name: 'Events EPG', enabled: true },
            { id: 8, name: 'Old EPG', enabled: false },
          ])
        )
      );
    }

    it('preserves an API-set dummy_epg_profile_id on an untouched save', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      const onSave = vi.fn();
      render(
        <EventSyncRuleEditor
          rule={{
            ...EXISTING_RULE,
            event_sync_config: {
              ...EXISTING_RULE.event_sync_config!,
              dummy_epg_profile_id: 7,
            },
          }}
          onSave={onSave}
          onCancel={vi.fn()}
        />
      );

      await user.click(screen.getByRole('button', { name: 'Save' }));

      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      const saved = onSave.mock.calls[0][0].event_sync_config;
      expect(saved.dummy_epg_profile_id).toBe(7);
    });

    it('selecting a profile under Advanced emits it; the default omits the key', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      stubDummyProfiles();
      const onSave = vi.fn();
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={onSave} onCancel={vi.fn()} />);

      await user.click(screen.getByText('Guide data'));
      // Open the profile picker (shows the None placeholder) and pick one.
      await user.click(
        screen.getByRole('button', { name: /none — no automatic guide data/i })
      );
      await user.click(await screen.findByRole('option', { name: 'Events EPG' }));
      await user.click(screen.getByRole('button', { name: 'Save' }));

      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      const saved = onSave.mock.calls[0][0].event_sync_config;
      expect(saved.dummy_epg_profile_id).toBe(7);
    });

    it('omits the key entirely when no profile is selected (absent means off)', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      const onSave = vi.fn();
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={onSave} onCancel={vi.fn()} />);

      await user.click(screen.getByRole('button', { name: 'Save' }));

      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      const saved = onSave.mock.calls[0][0].event_sync_config;
      expect(saved).not.toHaveProperty('dummy_epg_profile_id');
    });
  });

  it('has NO apply or attach control anywhere (Phase 1A hard constraint)', async () => {
    seedGroups();
    stubGroupSettings({ 1: true, 2: false });
    render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={vi.fn()} onCancel={vi.fn()} />);

    const master = await screen.findByTestId('psg-master');
    await within(master).findByText(/Auto-sync ON ✓/);
    expect(screen.queryByRole('button', { name: /apply|attach/i })).toBeNull();
  });

  describe('enabled-groups filter (bead x82s3)', () => {
    /** Group 3 ('Disabled Group') has `enabled: false` — hidden by default. */
    function stubThreeGroups() {
      stubGroupSettingsFull({
        1: { enabled: true, auto_channel_sync: true },
        2: { enabled: true, auto_channel_sync: false },
        3: { enabled: false, auto_channel_sync: false },
      });
    }

    it('defaults to hiding disabled junctions from both the master and secondary pickers', async () => {
      seedGroupsWithDisabled();
      stubThreeGroups();
      render(<EventSyncRuleEditor onSave={vi.fn()} onCancel={vi.fn()} />);

      // Secondary picker: disabled group's junction absent, enabled present.
      expect(await screen.findByTestId('psg-secondary-1-any')).toBeInTheDocument();
      expect(screen.getByTestId('psg-secondary-2-any')).toBeInTheDocument();
      expect(screen.queryByTestId('psg-secondary-3-any')).toBeNull();

      // Master picker: same filtering.
      expect(screen.getByTestId('psg-master-1-any')).toBeInTheDocument();
      expect(screen.getByTestId('psg-master-2-any')).toBeInTheDocument();
      expect(screen.queryByTestId('psg-master-3-any')).toBeNull();
    });

    it('reveals disabled junctions in both pickers when "Show all groups" is checked', async () => {
      const user = userEvent.setup();
      seedGroupsWithDisabled();
      stubThreeGroups();
      render(<EventSyncRuleEditor onSave={vi.fn()} onCancel={vi.fn()} />);

      await screen.findByTestId('psg-secondary-1-any');
      expect(screen.queryByTestId('psg-secondary-3-any')).toBeNull();

      await user.click(
        screen.getByRole('checkbox', { name: /show all groups/i })
      );

      expect(await screen.findByTestId('psg-secondary-3-any')).toBeInTheDocument();
      expect(screen.getByTestId('psg-master-3-any')).toBeInTheDocument();
    });

    it('keeps an already-selected but disabled master group visible, selected, and round-tripped on save', async () => {
      const user = userEvent.setup();
      seedGroupsWithDisabled();
      // The rule's master group (1) is itself disabled.
      stubGroupSettingsFull({
        1: { enabled: false, auto_channel_sync: true },
        2: { enabled: true, auto_channel_sync: false },
        3: { enabled: false, auto_channel_sync: false },
      });
      const onSave = vi.fn();
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={onSave} onCancel={vi.fn()} />);

      // The migrated whole-group master scope stays visible (round-trip guard)
      // with a disabled hint, and checked.
      const masterRow = await screen.findByTestId('psg-master-1-any');
      expect(masterRow).toBeChecked();
      expect(masterRow.closest('label')).toHaveTextContent('(disabled)');

      await user.click(screen.getByRole('button', { name: 'Save' }));
      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      expect(onSave.mock.calls[0][0].event_sync_config.master).toEqual({
        group_id: 1,
        m3u_account_id: null,
      });
    });

    it('keeps an already-checked but disabled secondary group visible, checked, and round-tripped on save', async () => {
      const user = userEvent.setup();
      seedGroupsWithDisabled();
      stubThreeGroups();
      const rule: Partial<ChannelPipelineRule> = {
        ...EXISTING_RULE,
        event_sync_config: {
          ...EXISTING_RULE.event_sync_config!,
          secondary_group_ids: [2, 3],
        },
      };
      const onSave = vi.fn();
      render(<EventSyncRuleEditor rule={rule} onSave={onSave} onCancel={vi.fn()} />);

      const disabledRow = await screen.findByTestId('psg-secondary-3-any');
      expect(disabledRow).toBeChecked();
      expect(disabledRow.closest('label')).toHaveTextContent('(disabled)');

      await user.click(screen.getByRole('button', { name: 'Save' }));
      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      expect(onSave.mock.calls[0][0].event_sync_config.secondary).toEqual([
        { group_id: 2, m3u_account_id: null },
        { group_id: 3, m3u_account_id: null },
      ]);
    });

    it('round-trips a selected group with no provider junction (shown as a chip, kept on save)', async () => {
      const user = userEvent.setup();
      seedGroupsWithDisabled();
      // Group 3 has NO junction row at all, so it is not a selectable picker
      // option — but the secondary selection chip still surfaces it and the
      // scope survives on save (no data loss).
      stubGroupSettingsFull({
        1: { enabled: true, auto_channel_sync: true },
        2: { enabled: true, auto_channel_sync: false },
      });
      const rule: Partial<ChannelPipelineRule> = {
        ...EXISTING_RULE,
        event_sync_config: {
          ...EXISTING_RULE.event_sync_config!,
          secondary_group_ids: [2, 3],
        },
      };
      const onSave = vi.fn();
      render(<EventSyncRuleEditor rule={rule} onSave={onSave} onCancel={vi.fn()} />);

      const secondary = await screen.findByTestId('psg-secondary');
      expect(within(secondary).getByText(/Group 3 · Any provider/)).toBeInTheDocument();

      await user.click(screen.getByRole('button', { name: 'Save' }));
      await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
      expect(onSave.mock.calls[0][0].event_sync_config.secondary).toEqual([
        { group_id: 2, m3u_account_id: null },
        { group_id: 3, m3u_account_id: null },
      ]);
    });

    it('composes the enabled filter with the picker search on the secondary list', async () => {
      const user = userEvent.setup();
      seedGroupsWithDisabled();
      stubThreeGroups();
      render(<EventSyncRuleEditor onSave={vi.fn()} onCancel={vi.fn()} />);

      await user.click(screen.getByRole('checkbox', { name: /show all groups/i }));
      await screen.findByTestId('psg-secondary-3-any');

      await user.type(screen.getByLabelText('Filter secondary groups'), 'Secondary');

      expect(screen.getByTestId('psg-secondary-2-any')).toBeInTheDocument();
      expect(screen.queryByTestId('psg-secondary-3-any')).toBeNull();
      expect(screen.queryByTestId('psg-secondary-1-any')).toBeNull();
    });
  });

  describe('UX redesign (bead dvzrf): 3-phase spine, intent, subgroups', () => {
    it('renders the three phase headings and the Advanced subgroups by purpose', async () => {
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={vi.fn()} onCancel={vi.fn()} />);

      // Phase spine headings.
      expect(await screen.findByText('Scope', { selector: 'h2' })).toBeInTheDocument();
      expect(screen.getByText('Matching', { selector: 'h2' })).toBeInTheDocument();
      expect(screen.getByText('Behavior', { selector: 'h2' })).toBeInTheDocument();

      // The flat Advanced wall is gone; the flags are grouped into labeled
      // subgroups by purpose.
      expect(screen.queryByText('Advanced', { selector: 'summary' })).toBeNull();
      for (const label of [
        'Time tuning',
        'Score threshold',
        'Date handling',
        'Per-group pattern overrides',
        'Automation',
        'Scope extension',
        'Guide data',
      ]) {
        expect(screen.getByText(label, { selector: 'summary' })).toBeInTheDocument();
      }
    });

    it('shows a plain-language rule-intent sentence derived from the current config', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={vi.fn()} onCancel={vi.fn()} />);

      const intent = await screen.findByTestId('event-sync-intent');
      // Default (enforced time window, manual runs, default threshold).
      expect(intent).toHaveTextContent(/Attach streams from 1 secondary group to master Master Events/i);
      expect(intent).toHaveTextContent(/title \+ start time within ±30 min/i);
      expect(intent).toHaveTextContent(/Runs only when you run it manually\./i);

      // Turning on Ignore-time-window flips the matching clause to the risky
      // (bold) phrasing, and auto-run flips the run clause.
      await user.click(screen.getByText('Time tuning'));
      await user.click(screen.getByTestId('event-sync-ignore-time-window'));
      expect(intent).toHaveTextContent(/title only \(time ignored\)/i);

      await user.click(screen.getByText('Automation'));
      await user.click(screen.getByTestId('event-sync-auto-run'));
      expect(intent).toHaveTextContent(/Runs automatically after each M3U refresh\./i);
    });

    it('badges a subgroup with the count of non-default flags it holds', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={vi.fn()} onCancel={vi.fn()} />);

      // Automation starts at its default — no badge.
      const automation = screen.getByText('Automation', { selector: 'summary' });
      expect(within(automation).queryByText(/changed/i)).toBeNull();

      await user.click(automation);
      await user.click(screen.getByTestId('event-sync-auto-run'));
      expect(within(automation).getByText('1 changed')).toBeInTheDocument();
    });
  });

  describe('Test Patterns collapse (bead dvzrf / F4)', () => {
    /** The batch preview endpoint the Test Patterns panel calls; return one
     * non-matching row so the run registers a parse failure. */
    function stubBatchParseFailure() {
      server.use(
        http.post('/api/dummy-epg/preview/batch', () =>
          HttpResponse.json([{ matched: false, groups: {}, event_sync_start_valid: false }])
        )
      );
    }

    it('is collapsed by default so it does not compete with the Preview rail', async () => {
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={vi.fn()} onCancel={vi.fn()} />);

      const details = await screen.findByTestId('event-sync-test-patterns-details');
      expect(details).not.toHaveAttribute('open');
    });

    it('auto-expands when a test run turns up parse failures', async () => {
      const user = userEvent.setup();
      seedGroups();
      stubGroupSettings({ 1: true, 2: false });
      stubBatchParseFailure();
      render(<EventSyncRuleEditor rule={EXISTING_RULE} onSave={vi.fn()} onCancel={vi.fn()} />);

      const details = await screen.findByTestId('event-sync-test-patterns-details');
      expect(details).not.toHaveAttribute('open');

      await user.type(screen.getByLabelText('Sample stream names'), 'Totally Unparseable Name');
      await user.click(screen.getByRole('button', { name: /test patterns/i }));

      await waitFor(() => expect(details).toHaveAttribute('open'));
    });
  });
});
