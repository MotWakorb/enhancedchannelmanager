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
      // The whole config round-trips content-identically...
      expect(saved).toEqual(API_CONFIG);
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

      await user.click(screen.getByText('Advanced'));
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

      await user.click(screen.getByText('Advanced'));
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
        'custom-shared',
      ]);
      expect(saved.patterns[2].title_pattern).toBe('^(?P<title>.+?)\\s*@');
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
