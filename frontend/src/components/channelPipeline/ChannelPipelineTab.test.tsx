/**
 * TDD Tests for ChannelPipelineTab component.
 *
 * These tests define the expected behavior of the main channel pipeline tab BEFORE implementation.
 */
import type * as React from 'react';
import { describe, it, expect, beforeAll, afterAll, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import {
  server,
  mockDataStore,
  resetMockDataStore,
  createMockChannelPipelineRule,
  createMockChannelPipelineExecution,
} from '../../test/mocks/server';
import { ChannelPipelineTab } from './ChannelPipelineTab';
import { NotificationProvider } from '../../contexts/NotificationContext';
import { AuthProvider } from '../../hooks/useAuth';

const renderWithProviders = (ui: React.JSX.Element) =>
  render(
    <AuthProvider>
      <NotificationProvider>{ui}</NotificationProvider>
    </AuthProvider>
  );

function expectDialogLabelledByVisibleHeading(dialog: HTMLElement, expectedName: string) {
  const titleId = dialog.getAttribute('aria-labelledby');
  expect(titleId).toBeTruthy();

  const title = document.getElementById(titleId!);
  expect(title).toBeVisible();
  expect(title).toHaveTextContent(expectedName);
  expect(title).toHaveAttribute('id', titleId);
}

// Setup MSW server
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  resetMockDataStore();
});
afterAll(() => server.close());

describe('ChannelPipelineTab', () => {
  describe('rendering', () => {
    it('renders the channel pipeline tab container', () => {
      renderWithProviders(<ChannelPipelineTab />);

      expect(screen.getByTestId('channel-pipeline-tab')).toBeInTheDocument();
    });

    it('does not repeat the route title inside the tab body', () => {
      renderWithProviders(<ChannelPipelineTab />);

      expect(screen.queryByRole('heading', { name: 'Channel Pipeline' })).not.toBeInTheDocument();
    });

    it('renders rules section and execution section', () => {
      renderWithProviders(<ChannelPipelineTab />);

      expect(screen.getByRole('heading', { name: /^rules$/i })).toBeInTheDocument();
      expect(screen.getByRole('heading', { name: /execution/i })).toBeInTheDocument();
    });

    it('renders action buttons', () => {
      renderWithProviders(<ChannelPipelineTab />);

      expect(screen.getByRole('button', { name: /create rule/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /^run$/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /dry run/i })).toBeInTheDocument();
    });

    it('labels the header debug bundle action "Pipeline Debug Bundle" to disambiguate from the whole-app bundle in Settings (bead 09x38.15 item 4)', () => {
      renderWithProviders(<ChannelPipelineTab />);

      expect(screen.getByRole('button', { name: /pipeline debug bundle/i })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /^debug bundle$/i })).not.toBeInTheDocument();
    });
  });

  describe('rules list', () => {
    it('displays list of rules', async () => {
      mockDataStore.channelPipelineRules.push(
        createMockChannelPipelineRule({ name: 'Rule 1' }),
        createMockChannelPipelineRule({ name: 'Rule 2' }),
        createMockChannelPipelineRule({ name: 'Rule 3' })
      );

      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => {
        expect(screen.getByText('Rule 1')).toBeInTheDocument();
        expect(screen.getByText('Rule 2')).toBeInTheDocument();
        expect(screen.getByText('Rule 3')).toBeInTheDocument();
      });
    });

    it('shows empty state when no rules exist', async () => {
      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => {
        expect(screen.getByText(/no rules/i)).toBeInTheDocument();
      });
    });

    it('Event Sync badge tooltip reflects auto_run state (bead xeli0)', async () => {
      mockDataStore.channelPipelineRules.push(
        createMockChannelPipelineRule({
          name: 'AutoRun ES',
          event_sync_config: {
            master_group_id: 1, secondary_group_ids: [2], auto_run: true,
          },
        }),
        createMockChannelPipelineRule({
          name: 'Manual ES',
          event_sync_config: {
            master_group_id: 1, secondary_group_ids: [2], auto_run: false,
          },
        })
      );

      renderWithProviders(<ChannelPipelineTab />);
      await waitFor(() => expect(screen.getByText('AutoRun ES')).toBeInTheDocument());

      const autoRow = screen.getByText('AutoRun ES').closest('tr');
      const manualRow = screen.getByText('Manual ES').closest('tr');
      expect(
        within(autoRow!).getByText('Event Sync').getAttribute('title')
      ).toContain('automatically after each M3U refresh');
      expect(
        within(manualRow!).getByText('Event Sync').getAttribute('title')
      ).toContain('never on unattended refresh');
    });

    it('shows rule enabled/disabled status', async () => {
      mockDataStore.channelPipelineRules.push(
        createMockChannelPipelineRule({ name: 'Enabled Rule', enabled: true }),
        createMockChannelPipelineRule({ name: 'Disabled Rule', enabled: false })
      );

      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => {
        const enabledRow = screen.getByText('Enabled Rule').closest('tr');
        const disabledRow = screen.getByText('Disabled Rule').closest('tr');

        // Look for status badges specifically
        const enabledBadge = within(enabledRow!).getByText('Enabled');
        const disabledBadge = within(disabledRow!).getByText('Disabled');

        expect(enabledBadge).toHaveClass('badge', 'badge-sm', 'badge-uppercase', 'badge-success');
        expect(disabledBadge).toHaveClass('badge', 'badge-sm', 'badge-uppercase');
      });
    });

    it('shows rule priority', async () => {
      mockDataStore.channelPipelineRules.push(
        createMockChannelPipelineRule({ name: 'High Priority', priority: 1 }),
        createMockChannelPipelineRule({ name: 'Low Priority', priority: 100 })
      );

      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => {
        const highRow = screen.getByText('High Priority').closest('tr');
        expect(within(highRow!).getByText('1')).toBeInTheDocument();
      });
    });

    it('shows rule match count', async () => {
      mockDataStore.channelPipelineRules.push(
        createMockChannelPipelineRule({ name: 'Popular Rule', match_count: 150 })
      );

      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => {
        // Match count appears in multiple places; just verify at least one exists
        const matches = screen.getAllByText('150');
        expect(matches.length).toBeGreaterThan(0);
      });
    });

    it('sorts rules by priority by default', async () => {
      mockDataStore.channelPipelineRules.push(
        createMockChannelPipelineRule({ name: 'Third', priority: 30 }),
        createMockChannelPipelineRule({ name: 'First', priority: 10 }),
        createMockChannelPipelineRule({ name: 'Second', priority: 20 })
      );

      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => {
        const rows = screen.getAllByRole('row').slice(1); // Skip header row
        expect(within(rows[0]).getByText('First')).toBeInTheDocument();
        expect(within(rows[1]).getByText('Second')).toBeInTheDocument();
        expect(within(rows[2]).getByText('Third')).toBeInTheDocument();
      });
    });
  });

  describe('rule actions', () => {
    it('allows toggling rule enabled state', async () => {
      const user = userEvent.setup();
      const rule = createMockChannelPipelineRule({ name: 'Test Rule', enabled: true });
      mockDataStore.channelPipelineRules.push(rule);

      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => {
        expect(screen.getByText('Test Rule')).toBeInTheDocument();
      });

      const toggleButton = screen.getByRole('button', { name: /toggle.*enabled/i });
      await user.click(toggleButton);

      await waitFor(() => {
        expect(screen.getByText(/disabled/i)).toBeInTheDocument();
      });
    });

    it('allows editing a rule', async () => {
      const user = userEvent.setup();
      const rule = createMockChannelPipelineRule({ name: 'Editable Rule' });
      mockDataStore.channelPipelineRules.push(rule);

      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => {
        expect(screen.getByText('Editable Rule')).toBeInTheDocument();
      });

      // Click the edit button (exact match to avoid toggle button)
      await user.click(screen.getByRole('button', { name: 'Edit' }));

      // Should open rule builder modal
      await waitFor(() => {
        expect(screen.getByRole('dialog')).toBeInTheDocument();
        expect(screen.getByLabelText(/rule name/i)).toHaveValue('Editable Rule');
      });
    });

    it('allows deleting a rule', async () => {
      const user = userEvent.setup();
      const rule = createMockChannelPipelineRule({ name: 'Deletable Rule' });
      mockDataStore.channelPipelineRules.push(rule);

      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => {
        expect(screen.getByText('Deletable Rule')).toBeInTheDocument();
      });

      await user.click(screen.getByRole('button', { name: /delete/i }));

      const deleteDialog = await screen.findByRole('dialog', { name: 'Confirm Delete' });
      expectDialogLabelledByVisibleHeading(deleteDialog, 'Confirm Delete');

      await user.click(within(deleteDialog).getByRole('button', { name: /confirm/i }));

      await waitFor(() => {
        expect(screen.queryByText('Deletable Rule')).not.toBeInTheDocument();
      });
    });

    it('allows duplicating a rule', async () => {
      const user = userEvent.setup();
      const rule = createMockChannelPipelineRule({ name: 'Original Rule' });
      mockDataStore.channelPipelineRules.push(rule);

      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => {
        expect(screen.getByText('Original Rule')).toBeInTheDocument();
      });

      await user.click(screen.getByRole('button', { name: /duplicate/i }));

      await waitFor(() => {
        expect(screen.getByText(/Original Rule.*Copy/)).toBeInTheDocument();
      });
    });
  });

  describe('create rule', () => {
    it('opens the rule-kind chooser, then the rule builder for a standard rule', async () => {
      const user = userEvent.setup();
      renderWithProviders(<ChannelPipelineTab />);

      await user.click(screen.getByRole('button', { name: /create rule/i }));

      // Kind chooser first (epic ti939): standard vs event sync
      await waitFor(() => {
        expect(screen.getByRole('dialog')).toBeInTheDocument();
        expect(screen.getByTestId('rule-kind-chooser')).toBeInTheDocument();
      });
      await user.click(screen.getByRole('button', { name: /standard rule/i }));

      await waitFor(() => {
        expect(screen.getByLabelText(/rule name/i)).toHaveValue('');
      });
    });

    it('opens the event sync editor when the Event Sync kind is chosen', async () => {
      const user = userEvent.setup();
      renderWithProviders(<ChannelPipelineTab />);

      await user.click(screen.getByRole('button', { name: /create rule/i }));
      await user.click(await screen.findByRole('button', { name: /event sync rule/i }));

      await waitFor(() => {
        expect(screen.getByTestId('event-sync-editor')).toBeInTheDocument();
      });
      // Phase 1A hard constraint: no apply/attach control anywhere
      expect(screen.queryByRole('button', { name: /apply|attach/i })).toBeNull();
    });

    it('adds new rule to list after creation', async () => {
      const user = userEvent.setup();
      renderWithProviders(<ChannelPipelineTab />);

      await user.click(screen.getByRole('button', { name: /create rule/i }));
      await user.click(await screen.findByRole('button', { name: /standard rule/i }));

      // Fill in the form
      await user.type(screen.getByLabelText(/rule name/i), 'Brand New Rule');

      // Add condition (defaults to Stream Name Contains) and fill value
      await user.click(screen.getByRole('button', { name: /add condition/i }));
      await user.type(screen.getByPlaceholderText(/enter text/i), 'test');

      // Add action (adds blank action) and select Skip type
      await user.click(screen.getByRole('button', { name: /add action/i }));
      await user.click(screen.getByRole('combobox', { name: /action type/i }));
      await user.click(screen.getByRole('option', { name: /skip/i }));

      // Save
      await user.click(screen.getByRole('button', { name: /save/i }));

      await waitFor(() => {
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
        expect(screen.getByText('Brand New Rule')).toBeInTheDocument();
      });
    });
  });

  describe('run pipeline', () => {
    it('runs pipeline in execute mode', async () => {
      const user = userEvent.setup();
      mockDataStore.channelPipelineRules.push(
        createMockChannelPipelineRule({ name: 'Active Rule', enabled: true })
      );

      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => {
        expect(screen.getByText('Active Rule')).toBeInTheDocument();
      });

      await user.click(screen.getByRole('button', { name: /^run$/i }));

      await waitFor(() => {
        // Should show execution result banner with "Created X channels"
        expect(screen.getByText(/execution complete/i)).toBeInTheDocument();
        expect(screen.getByText(/created.*channels/i)).toBeInTheDocument();
      });
    });

    it('runs pipeline in dry-run mode', async () => {
      const user = userEvent.setup();
      mockDataStore.channelPipelineRules.push(
        createMockChannelPipelineRule({ name: 'Active Rule', enabled: true })
      );

      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => {
        expect(screen.getByText('Active Rule')).toBeInTheDocument();
      });

      await user.click(screen.getByRole('button', { name: /dry.*run/i }));

      await waitFor(() => {
        expect(screen.getByText(/dry.*run complete/i)).toBeInTheDocument();
        expect(screen.getByText(/would create/i)).toBeInTheDocument();
      });
    });

    it('shows loading state during execution', async () => {
      const user = userEvent.setup();
      mockDataStore.channelPipelineRules.push(
        createMockChannelPipelineRule({ enabled: true })
      );

      // Override the run handler to delay the response long enough for the test to observe
      server.use(
        http.post('/api/channel-pipeline/run', async () => {
          await new Promise(resolve => setTimeout(resolve, 1000));
          return HttpResponse.json({
            success: true,
            execution_id: 1,
            mode: 'execute',
            duration_seconds: 1.5,
            streams_evaluated: 100,
            streams_matched: 5,
            channels_created: 3,
            channels_updated: 0,
            groups_created: 0,
            streams_merged: 0,
            streams_skipped: 0,
            created_entities: [],
            modified_entities: [],
          });
        })
      );

      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /^run$/i })).toBeInTheDocument();
      });

      await user.click(screen.getByRole('button', { name: /^run$/i }));

      // Should show loading indicator while running
      // The button text changes to "Running..." with a spinning icon
      await waitFor(() => {
        expect(screen.getByText(/running\.\.\./i)).toBeInTheDocument();
      });
    });

    it('disables run buttons when no enabled rules exist', async () => {
      mockDataStore.channelPipelineRules.push(
        createMockChannelPipelineRule({ enabled: false })
      );

      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /^run$/i })).toBeDisabled();
        expect(screen.getByRole('button', { name: /dry.*run/i })).toBeDisabled();
      });
    });

    // Note: Per-rule selection with checkboxes is not implemented.
    // The run pipeline executes all enabled rules.
  });

  describe('event_sync per-rule run (utswf)', () => {
    // Capture the bodies POSTed to the run endpoint so we can assert on
    // dry_run / rule_ids without depending on the poll internals.
    const installRunCapture = (): Array<{ dry_run?: boolean; rule_ids?: number[] }> => {
      const bodies: Array<{ dry_run?: boolean; rule_ids?: number[] }> = [];
      server.use(
        http.post('/api/channel-pipeline/run', async ({ request }) => {
          const body = (await request.json()) as { dry_run?: boolean; rule_ids?: number[] };
          bodies.push(body);
          const execution = createMockChannelPipelineExecution({
            mode: body.dry_run ? 'dry_run' : 'execute',
            status: 'completed',
            channels_created: 0,
            streams_matched: 2,
          });
          mockDataStore.channelPipelineExecutions.unshift(execution);
          return HttpResponse.json(
            { execution_id: execution.id, status: 'running', message: 'started' },
            { status: 202 }
          );
        })
      );
      return bodies;
    };

    const pushEventSyncRule = (
      name: string,
      extraConfig: Record<string, unknown> = {}
    ) => {
      const rule = createMockChannelPipelineRule({
        name,
        enabled: true,
        event_sync_config: {
          master_group_id: 1, secondary_group_ids: [2], auto_run: false,
          ...extraConfig,
        },
      });
      mockDataStore.channelPipelineRules.push(rule);
      return rule;
    };

    it('renders per-rule Run and Test affordances on an event_sync row', async () => {
      const rule = pushEventSyncRule('ES Rule');
      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => expect(screen.getByText('ES Rule')).toBeInTheDocument());
      const row = screen.getByText('ES Rule').closest('tr')!;

      expect(within(row).getByRole('button', { name: `Run ${rule.name}` })).toBeInTheDocument();
      expect(within(row).getByRole('button', { name: `Test ${rule.name}` })).toBeInTheDocument();
    });

    it('Test (dry run) on an event_sync row runs immediately with dry_run=true and no confirm', async () => {
      const user = userEvent.setup();
      const bodies = installRunCapture();
      const rule = pushEventSyncRule('ES Rule');
      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => expect(screen.getByText('ES Rule')).toBeInTheDocument());
      const row = screen.getByText('ES Rule').closest('tr')!;

      await user.click(within(row).getByRole('button', { name: `Test ${rule.name}` }));

      await waitFor(() => expect(bodies).toHaveLength(1));
      expect(bodies[0]).toEqual({ dry_run: true, rule_ids: [rule.id] });
      // Dry run never surfaces the live-run confirm.
      expect(screen.queryByTestId('event-sync-run-confirm')).not.toBeInTheDocument();
    });

    it('Run on an event_sync row opens a confirm and does not run until confirmed', async () => {
      const user = userEvent.setup();
      const bodies = installRunCapture();
      const rule = pushEventSyncRule('ES Rule');
      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => expect(screen.getByText('ES Rule')).toBeInTheDocument());
      const row = screen.getByText('ES Rule').closest('tr')!;

      await user.click(within(row).getByRole('button', { name: `Run ${rule.name}` }));

      // Confirm appears; nothing has run yet.
      expect(await screen.findByTestId('event-sync-run-confirm')).toBeInTheDocument();
      expect(bodies).toHaveLength(0);

      await user.click(screen.getByTestId('event-sync-run-confirm-btn'));

      await waitFor(() => expect(bodies).toHaveLength(1));
      expect(bodies[0]).toEqual({ dry_run: false, rule_ids: [rule.id] });
    });

    it('cancelling the event_sync run confirm does not run', async () => {
      const user = userEvent.setup();
      const bodies = installRunCapture();
      const rule = pushEventSyncRule('ES Rule');
      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => expect(screen.getByText('ES Rule')).toBeInTheDocument());
      const row = screen.getByText('ES Rule').closest('tr')!;

      await user.click(within(row).getByRole('button', { name: `Run ${rule.name}` }));
      await user.click(await screen.findByRole('button', { name: /cancel/i }));

      await waitFor(() =>
        expect(screen.queryByTestId('event-sync-run-confirm')).not.toBeInTheDocument()
      );
      expect(bodies).toHaveLength(0);
    });

    it('Test on an event_sync row with refresh_providers_before_run routes through a confirm that warns Test is not zero-write (bead y8yby)', async () => {
      const user = userEvent.setup();
      const bodies = installRunCapture();
      const rule = pushEventSyncRule('ES Refresh Rule', {
        refresh_providers_before_run: true,
      });
      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => expect(screen.getByText('ES Refresh Rule')).toBeInTheDocument());
      const row = screen.getByText('ES Refresh Rule').closest('tr')!;

      await user.click(within(row).getByRole('button', { name: `Test ${rule.name}` }));

      // A confirm appears (Test is no longer zero-write) and nothing has run.
      expect(await screen.findByTestId('event-sync-run-confirm')).toBeInTheDocument();
      expect(
        screen.getByTestId('event-sync-test-refresh-warning')
      ).toBeInTheDocument();
      expect(bodies).toHaveLength(0);

      await user.click(screen.getByTestId('event-sync-run-confirm-btn'));

      await waitFor(() => expect(bodies).toHaveLength(1));
      // Still a dry run — the refresh happens server-side, no attaches.
      expect(bodies[0]).toEqual({ dry_run: true, rule_ids: [rule.id] });
    });

    it('Run confirm on a refresh-before-run rule notes the pre-refresh (bead y8yby)', async () => {
      const user = userEvent.setup();
      installRunCapture();
      const rule = pushEventSyncRule('ES Refresh Rule', {
        refresh_providers_before_run: true,
      });
      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => expect(screen.getByText('ES Refresh Rule')).toBeInTheDocument());
      const row = screen.getByText('ES Refresh Rule').closest('tr')!;

      await user.click(within(row).getByRole('button', { name: `Run ${rule.name}` }));

      expect(await screen.findByTestId('event-sync-run-confirm')).toBeInTheDocument();
      expect(
        screen.getByTestId('event-sync-run-refresh-note')
      ).toBeInTheDocument();
    });

    it('a standard rule Run runs directly with no confirm (parity)', async () => {
      const user = userEvent.setup();
      const bodies = installRunCapture();
      const rule = createMockChannelPipelineRule({ name: 'Std Rule', enabled: true });
      mockDataStore.channelPipelineRules.push(rule);
      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => expect(screen.getByText('Std Rule')).toBeInTheDocument());
      const row = screen.getByText('Std Rule').closest('tr')!;

      await user.click(within(row).getByRole('button', { name: `Run ${rule.name}` }));

      await waitFor(() => expect(bodies).toHaveLength(1));
      expect(bodies[0]).toEqual({ dry_run: false, rule_ids: [rule.id] });
      expect(screen.queryByTestId('event-sync-run-confirm')).not.toBeInTheDocument();
    });

    it('disables the per-rule Run/Test buttons while a run is in flight', async () => {
      const user = userEvent.setup();
      // Delay the run so the in-flight (running) state is observable.
      server.use(
        http.post('/api/channel-pipeline/run', async () => {
          await new Promise(resolve => setTimeout(resolve, 500));
          const execution = createMockChannelPipelineExecution({
            mode: 'dry_run', status: 'completed', channels_created: 0,
          });
          mockDataStore.channelPipelineExecutions.unshift(execution);
          return HttpResponse.json(
            { execution_id: execution.id, status: 'running', message: 'started' },
            { status: 202 }
          );
        })
      );
      const rule = pushEventSyncRule('ES Rule');
      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => expect(screen.getByText('ES Rule')).toBeInTheDocument());
      const row = screen.getByText('ES Rule').closest('tr')!;

      // Fire the confirm-free dry run, then assert both icons go disabled.
      await user.click(within(row).getByRole('button', { name: `Test ${rule.name}` }));

      await waitFor(() => {
        expect(within(row).getByRole('button', { name: `Run ${rule.name}` })).toBeDisabled();
        expect(within(row).getByRole('button', { name: `Test ${rule.name}` })).toBeDisabled();
      });
    });

    it('event_sync run completion points the operator to Execution History', async () => {
      const user = userEvent.setup();
      installRunCapture();
      const rule = pushEventSyncRule('ES Rule');
      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => expect(screen.getByText('ES Rule')).toBeInTheDocument());
      const row = screen.getByText('ES Rule').closest('tr')!;

      await user.click(within(row).getByRole('button', { name: `Run ${rule.name}` }));
      await user.click(screen.getByTestId('event-sync-run-confirm-btn'));

      // Not "Created 0 channels" — event_sync feedback points to the details.
      // (Match the toast phrase specifically; the "Execution History" section
      // header is always present and would otherwise collide.)
      await waitFor(() => {
        expect(
          screen.getByText(/event sync run complete.*execution history for attach details/i)
        ).toBeInTheDocument();
      });
      expect(screen.queryByText(/created 0 channels/i)).not.toBeInTheDocument();
    });
  });

  describe('execution history', () => {
    it('displays execution history', async () => {
      mockDataStore.channelPipelineExecutions.push(
        createMockChannelPipelineExecution({ status: 'completed', channels_created: 5 }),
        createMockChannelPipelineExecution({ status: 'completed', channels_created: 3 })
      );

      renderWithProviders(<ChannelPipelineTab />);

      // Click to show history
      await waitFor(() => {
        const historySection = screen.getByText(/execution history/i);
        expect(historySection).toBeInTheDocument();
      });
    });

    it('shows execution status badges', async () => {
      mockDataStore.channelPipelineExecutions.push(
        createMockChannelPipelineExecution({ status: 'completed' }),
        createMockChannelPipelineExecution({ status: 'failed' }),
        createMockChannelPipelineExecution({ status: 'rolled_back' })
      );

      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => {
        expect(screen.getByText(/completed/i)).toBeInTheDocument();
        expect(screen.getByText(/failed/i)).toBeInTheDocument();
        expect(screen.getByText(/rolled.*back/i)).toBeInTheDocument();
      });
    });

    it('allows viewing execution details', async () => {
      const user = userEvent.setup();
      mockDataStore.channelPipelineExecutions.push(
        createMockChannelPipelineExecution({ streams_matched: 25, channels_created: 10 })
      );

      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /view details/i })).toBeInTheDocument();
      });

      await user.click(screen.getByRole('button', { name: /view details/i }));

      // Detail rows have label and value in separate elements
      await waitFor(() => {
        expect(screen.getByText(/streams matched/i)).toBeInTheDocument();
        expect(screen.getByText('25')).toBeInTheDocument();
        expect(screen.getByText(/channels created/i)).toBeInTheDocument();
        expect(screen.getByText('10')).toBeInTheDocument();
      });
    });

    it('surfaces disabled-normalization-group warnings in execution details', async () => {
      // enhancedchannelmanager-e8p1h: a run that referenced a disabled
      // normalization group must show a prominent, actionable warning so the
      // operator knows normalization silently applied nothing.
      const user = userEvent.setup();
      mockDataStore.channelPipelineExecutions.push(
        createMockChannelPipelineExecution({
          streams_matched: 25,
          channels_created: 0,
          warnings: [
            {
              rule_id: 7,
              rule_name: 'Movie Channels',
              disabled_groups: [
                { id: 2, name: 'Country Prefixes', missing: false },
              ],
            },
          ],
        }),
      );

      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /view details/i })).toBeInTheDocument();
      });
      await user.click(screen.getByRole('button', { name: /view details/i }));

      await waitFor(() => {
        expect(screen.getByText(/normalization applied no changes/i)).toBeInTheDocument();
        // Names the offending rule and disabled group, and tells them to enable it.
        expect(screen.getByText('Movie Channels')).toBeInTheDocument();
        expect(screen.getByText(/country prefixes/i)).toBeInTheDocument();
        expect(screen.getByText(/settings.*normalization/i)).toBeInTheDocument();
      });
    });

    it('does not show the normalization warning when there are no warnings', async () => {
      const user = userEvent.setup();
      mockDataStore.channelPipelineExecutions.push(
        createMockChannelPipelineExecution({ streams_matched: 25, channels_created: 10 }),
      );

      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /view details/i })).toBeInTheDocument();
      });
      await user.click(screen.getByRole('button', { name: /view details/i }));

      await waitFor(() => {
        expect(screen.getByText(/streams matched/i)).toBeInTheDocument();
      });
      expect(screen.queryByText(/normalization applied no changes/i)).toBeNull();
    });

    // y3m6o.1 review (Blocker 3): the run `warnings` array is heterogeneous.
    // A non_reversible_profile_changes warning has no rule_name/disabled_groups,
    // so the old code produced a FALSE "Normalization applied no changes:
    // undefined ..." toast and crashed the details renderer on disabled_groups.
    const NON_REVERSIBLE_MSG =
      'This run changed channel-profile membership on 4 channel(s). ' +
      'Channel-profile membership has no reversible previous state, so ' +
      'Rollback and Undo will NOT restore it.';

    const nonReversibleWarning = () => ({
      type: 'non_reversible_profile_changes' as const,
      count: 4,
      channel_ids: [1, 2, 3, 4],
      message: NON_REVERSIBLE_MSG,
    });

    const disabledNormWarning = () => ({
      type: 'disabled_normalization_group' as const,
      rule_id: 7,
      rule_name: 'Movie Channels',
      disabled_groups: [{ id: 2, name: 'Country Prefixes', missing: false }],
    });

    it('clean run with only a non_reversible warning: shows its own copy, not a false normalization toast', async () => {
      const user = userEvent.setup();
      mockDataStore.channelPipelineRules.push(
        createMockChannelPipelineRule({ name: 'Profile Rule', enabled: true }),
      );
      // Override run: clean `completed` run that only changed profile membership.
      server.use(
        http.post('/api/channel-pipeline/run', async () => {
          const execution = createMockChannelPipelineExecution({
            mode: 'execute',
            status: 'completed',
            channels_created: 0,
            streams_matched: 4,
            warnings: [nonReversibleWarning()],
          });
          mockDataStore.channelPipelineExecutions.unshift(execution);
          return HttpResponse.json(
            { execution_id: execution.id, status: 'running', message: 'started' },
            { status: 202 },
          );
        }),
      );

      renderWithProviders(<ChannelPipelineTab />);
      await waitFor(() => {
        expect(screen.getByText('Profile Rule')).toBeInTheDocument();
      });
      await user.click(screen.getByRole('button', { name: /^run$/i }));

      // The non-reversible disclosure toast (its own operator copy) appears.
      await waitFor(() => {
        expect(
          screen.getByText(/rollback and undo will not restore it/i),
        ).toBeInTheDocument();
      });
      // And NO false normalization toast (the bug: "...no changes: undefined").
      expect(
        screen.queryByText(/normalization applied no changes/i),
      ).toBeNull();
      expect(screen.queryByText(/undefined/i)).toBeNull();
    });

    it('hard-failed run with a disabled-norm warning: shows only the error toast, not the normalization toast (Should-Fix C)', async () => {
      const user = userEvent.setup();
      mockDataStore.channelPipelineRules.push(
        createMockChannelPipelineRule({ name: 'Norm Rule', enabled: true }),
      );
      // Override run: a HARD-FAILED run that also carries a disabled-norm warning.
      server.use(
        http.post('/api/channel-pipeline/run', async () => {
          const execution = createMockChannelPipelineExecution({
            mode: 'execute',
            status: 'failed',
            channels_created: 0,
            streams_matched: 0,
            warnings: [disabledNormWarning()],
          });
          mockDataStore.channelPipelineExecutions.unshift(execution);
          return HttpResponse.json(
            { execution_id: execution.id, status: 'running', message: 'started' },
            { status: 202 },
          );
        }),
      );

      renderWithProviders(<ChannelPipelineTab />);
      await waitFor(() => {
        expect(screen.getByText('Norm Rule')).toBeInTheDocument();
      });
      await user.click(screen.getByRole('button', { name: /^run$/i }));

      // The error toast surfaces (a hard-failed run reports "Pipeline failed").
      await waitFor(() => {
        expect(screen.getByText(/pipeline failed/i)).toBeInTheDocument();
      });
      // The "Normalization applied no changes" toast must NOT stack on top of a
      // hard failure — the guard restricts it to succeeded/completedWithErrors.
      expect(
        screen.queryByText(/normalization applied no changes/i),
      ).toBeNull();
    });

    it('expanded execution details with a non_reversible warning: renders its copy without crashing', async () => {
      const user = userEvent.setup();
      mockDataStore.channelPipelineExecutions.push(
        createMockChannelPipelineExecution({
          streams_matched: 4,
          channels_created: 0,
          status: 'completed',
          has_non_reversible_profile_changes: true,
          warnings: [nonReversibleWarning()],
        }),
      );

      renderWithProviders(<ChannelPipelineTab />);
      await waitFor(() => {
        expect(screen.getByRole('button', { name: /view details/i })).toBeInTheDocument();
      });
      await user.click(screen.getByRole('button', { name: /view details/i }));

      await waitFor(() => {
        // Its own disclosure copy (count + message), no disabled_groups crash.
        expect(
          screen.getByText(/channel-profile membership changed on 4 channels/i),
        ).toBeInTheDocument();
        expect(
          screen.getByText(/rollback and undo will not restore it/i),
        ).toBeInTheDocument();
      });
      // The normalization banner must NOT appear for a pure non_reversible run.
      expect(
        screen.queryByText(/normalization applied no changes/i),
      ).toBeNull();
    });

    it('mixed warnings in execution details: renders BOTH the normalization banner and the non_reversible disclosure', async () => {
      const user = userEvent.setup();
      mockDataStore.channelPipelineExecutions.push(
        createMockChannelPipelineExecution({
          streams_matched: 25,
          channels_created: 0,
          status: 'completed',
          has_non_reversible_profile_changes: true,
          warnings: [disabledNormWarning(), nonReversibleWarning()],
        }),
      );

      renderWithProviders(<ChannelPipelineTab />);
      await waitFor(() => {
        expect(screen.getByRole('button', { name: /view details/i })).toBeInTheDocument();
      });
      await user.click(screen.getByRole('button', { name: /view details/i }));

      await waitFor(() => {
        // Normalization variant still renders (rule + disabled group).
        expect(screen.getByText(/normalization applied no changes/i)).toBeInTheDocument();
        expect(screen.getByText('Movie Channels')).toBeInTheDocument();
        expect(screen.getByText(/country prefixes/i)).toBeInTheDocument();
        // Non-reversible variant also renders alongside it.
        expect(
          screen.getByText(/channel-profile membership changed on 4 channels/i),
        ).toBeInTheDocument();
      });
    });

    // enhancedchannelmanager-7wuhd: event_sync runs need an event_sync-aware
    // summary — the standard evaluated/matched/created counters are
    // structurally 0 for them and read as "nothing happened".
    const eventSyncSummary = (over = {}) => [{
      rule_id: 7,
      rule_name: 'NFL Sunday',
      secondary_streams: 12,
      attached: 3,
      already_attached: 6,
      ambiguous_skipped: 1,
      unmatched: 2,
      parse_failed: 0,
      attach_errors: 0,
      ...over,
    }];

    it('renders an event_sync-aware summary and drops Channels Created', async () => {
      const user = userEvent.setup();
      mockDataStore.channelPipelineExecutions.push(
        createMockChannelPipelineExecution({
          is_event_sync: true,
          streams_evaluated: 0,
          streams_matched: 0,
          channels_created: 0,
          event_sync_summary: eventSyncSummary(),
        }),
      );

      renderWithProviders(<ChannelPipelineTab />);
      await waitFor(() => {
        expect(screen.getByRole('button', { name: /view details/i })).toBeInTheDocument();
      });
      await user.click(screen.getByRole('button', { name: /view details/i }));

      await waitFor(() => {
        expect(screen.getByText(/secondary streams evaluated/i)).toBeInTheDocument();
      });
      // Scope to the details dialog — the compact list chip also carries
      // event_sync words (e.g. "already attached").
      const dialog = within(screen.getByRole('dialog', { name: 'Execution Details' }));
      expect(dialog.getByText(/^attached:?$/i)).toBeInTheDocument();
      expect(dialog.getByText(/^already attached:?$/i)).toBeInTheDocument();
      expect(dialog.getByText(/ambiguous/i)).toBeInTheDocument();
      expect(dialog.getByText(/^unmatched:?$/i)).toBeInTheDocument();
      // Standard counters are gone for a pure event_sync run.
      expect(dialog.queryByText(/channels created/i)).toBeNull();
      expect(dialog.queryByText(/streams matched/i)).toBeNull();
      // Exact match: the event_sync block's "Secondary Streams Evaluated:"
      // contains this substring, so only the standard label must be absent.
      expect(dialog.queryByText('Streams Evaluated:')).toBeNull();
    });

    it('shows the fully-in-sync success banner when everything is already attached', async () => {
      const user = userEvent.setup();
      mockDataStore.channelPipelineExecutions.push(
        createMockChannelPipelineExecution({
          is_event_sync: true,
          streams_evaluated: 0,
          streams_matched: 0,
          channels_created: 0,
          event_sync_summary: eventSyncSummary({
            attached: 0,
            already_attached: 9,
            ambiguous_skipped: 0,
            unmatched: 0,
            parse_failed: 0,
          }),
        }),
      );

      renderWithProviders(<ChannelPipelineTab />);
      await waitFor(() => {
        expect(screen.getByRole('button', { name: /view details/i })).toBeInTheDocument();
      });
      await user.click(screen.getByRole('button', { name: /view details/i }));

      await waitFor(() => {
        expect(screen.getByTestId('event-sync-fully-in-sync')).toBeInTheDocument();
      });
      expect(screen.getByText(/fully in sync/i)).toBeInTheDocument();
      expect(screen.getByText(/9 streams already attached/i)).toBeInTheDocument();
    });

    it('does not show the fully-in-sync banner when there is new work', async () => {
      const user = userEvent.setup();
      mockDataStore.channelPipelineExecutions.push(
        createMockChannelPipelineExecution({
          is_event_sync: true,
          event_sync_summary: eventSyncSummary({ attached: 2, already_attached: 3, ambiguous_skipped: 0, unmatched: 0, parse_failed: 0 }),
        }),
      );

      renderWithProviders(<ChannelPipelineTab />);
      await waitFor(() => {
        expect(screen.getByRole('button', { name: /view details/i })).toBeInTheDocument();
      });
      await user.click(screen.getByRole('button', { name: /view details/i }));

      await waitFor(() => {
        expect(screen.getByText(/secondary streams evaluated/i)).toBeInTheDocument();
      });
      expect(screen.queryByTestId('event-sync-fully-in-sync')).toBeNull();
    });

    it('uses "Would Attach" wording for a dry-run event_sync execution', async () => {
      const user = userEvent.setup();
      mockDataStore.channelPipelineExecutions.push(
        createMockChannelPipelineExecution({
          mode: 'dry_run',
          is_event_sync: true,
          event_sync_summary: eventSyncSummary(),
        }),
      );

      renderWithProviders(<ChannelPipelineTab />);
      await waitFor(() => {
        expect(screen.getByRole('button', { name: /view details/i })).toBeInTheDocument();
      });
      await user.click(screen.getByRole('button', { name: /view details/i }));

      await waitFor(() => {
        expect(screen.getByText(/secondary streams evaluated/i)).toBeInTheDocument();
      });
      const dialog = within(screen.getByRole('dialog', { name: 'Execution Details' }));
      expect(dialog.getByText(/^would attach:?$/i)).toBeInTheDocument();
      expect(dialog.queryByText(/^attached:?$/i)).toBeNull();
    });

    it('keeps the standard summary for a standard (non-event_sync) execution', async () => {
      const user = userEvent.setup();
      mockDataStore.channelPipelineExecutions.push(
        createMockChannelPipelineExecution({ streams_matched: 25, channels_created: 10 }),
      );

      renderWithProviders(<ChannelPipelineTab />);
      await waitFor(() => {
        expect(screen.getByRole('button', { name: /view details/i })).toBeInTheDocument();
      });
      await user.click(screen.getByRole('button', { name: /view details/i }));

      await waitFor(() => {
        expect(screen.getByText(/channels created/i)).toBeInTheDocument();
      });
      // No event_sync block for a standard run.
      expect(screen.queryByText(/secondary streams evaluated/i)).toBeNull();
      expect(screen.queryByTestId('event-sync-fully-in-sync')).toBeNull();
    });

    it('allows rolling back an execution', async () => {
      const user = userEvent.setup();
      mockDataStore.channelPipelineExecutions.push(
        createMockChannelPipelineExecution({ status: 'completed', mode: 'execute' })
      );

      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /rollback/i })).toBeInTheDocument();
      });

      await user.click(screen.getByRole('button', { name: /rollback/i }));

      // Confirm rollback
      await waitFor(() => {
        expect(screen.getByText(/confirm.*rollback/i)).toBeInTheDocument();
      });

      await user.click(screen.getByRole('button', { name: /confirm/i }));

      await waitFor(() => {
        expect(screen.getByText(/rolled.*back/i)).toBeInTheDocument();
      });
    });

    // y3m6o.1 review (Finding 3): rollback/undo must DISCLOSE that
    // channel-profile membership will not be restored when the run mutated it.
    it('discloses non-reversible profile changes in the rollback confirm', async () => {
      const user = userEvent.setup();
      mockDataStore.channelPipelineExecutions.push(
        createMockChannelPipelineExecution({
          status: 'completed_with_errors',
          mode: 'execute',
          has_non_reversible_profile_changes: true,
        })
      );

      renderWithProviders(<ChannelPipelineTab />);

      const rollbackBtn = await screen.findByRole('button', { name: /rollback/i });
      // Tooltip discloses membership will not be restored.
      expect(rollbackBtn.title).toMatch(/channel-profile membership.*not be restored/i);

      await user.click(rollbackBtn);

      await waitFor(() => {
        expect(screen.getByTestId('rollback-profile-disclosure')).toBeInTheDocument();
      });
      expect(screen.getByTestId('rollback-profile-disclosure').textContent)
        .toMatch(/channel-profile membership/i);
    });

    it('omits the profile disclosure when the run made no profile changes', async () => {
      const user = userEvent.setup();
      mockDataStore.channelPipelineExecutions.push(
        createMockChannelPipelineExecution({
          status: 'completed',
          mode: 'execute',
          has_non_reversible_profile_changes: false,
        })
      );

      renderWithProviders(<ChannelPipelineTab />);

      const rollbackBtn = await screen.findByRole('button', { name: /rollback/i });
      expect(rollbackBtn.title).not.toMatch(/channel-profile membership/i);

      await user.click(rollbackBtn);

      await waitFor(() => {
        expect(screen.getByText(/confirm.*rollback/i)).toBeInTheDocument();
      });
      expect(screen.queryByTestId('rollback-profile-disclosure')).toBeNull();
    });

    it('disables rollback for dry-run executions', async () => {
      mockDataStore.channelPipelineExecutions.push(
        createMockChannelPipelineExecution({ status: 'completed', mode: 'dry_run' })
      );

      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => {
        const rollbackBtn = screen.queryByRole('button', { name: /rollback/i });
        expect(rollbackBtn).toBeNull(); // No rollback for dry runs
      });
    });

    // bead 09x38.9 gave Rollback an explanatory tooltip. bead h2oxl then hid
    // Rollback on snapshot-backed runs (option (b)) — so on the runs where it
    // still renders there is neither a snapshot nor an "Undo this run" sibling,
    // and the tooltip explains it is the only restore available for a
    // no-snapshot run rather than cross-referencing a button that isn't there.
    it('gives the Rollback button a tooltip explaining it is the only restore for a no-snapshot run', async () => {
      mockDataStore.channelPipelineExecutions.push(
        createMockChannelPipelineExecution({ status: 'completed', mode: 'execute', has_snapshot: false })
      );

      renderWithProviders(<ChannelPipelineTab />);

      const rollbackBtn = await screen.findByRole('button', { name: /rollback/i });
      expect(rollbackBtn.title).toMatch(/this run's own recorded changes/i);
      expect(rollbackBtn.title).toMatch(/no pre-run snapshot/i);
      expect(rollbackBtn.title).toMatch(/only\s+restore available/i);
    });

    // bead h2oxl: Rollback (POST /rollback without confirm=true) 409s on
    // snapshot-backed runs, so option (b) hides it there — only "Undo this run"
    // (restore-snapshot?confirm=true) renders, which is the correct full-restore
    // action for a snapshot-backed run.
    it('hides Rollback on a snapshot-backed run and shows only "Undo this run"', async () => {
      mockDataStore.channelPipelineExecutions.push(
        createMockChannelPipelineExecution({ status: 'completed', mode: 'execute', has_snapshot: true })
      );

      renderWithProviders(<ChannelPipelineTab />);

      // Undo this run is the correct affordance on a snapshot-backed run.
      expect(await screen.findByRole('button', { name: /undo this run/i })).toBeInTheDocument();
      // Rollback would 409, so it must not render here.
      expect(screen.queryByRole('button', { name: /rollback/i })).toBeNull();
    });

    it('shows Rollback on a legacy no-snapshot run', async () => {
      mockDataStore.channelPipelineExecutions.push(
        createMockChannelPipelineExecution({ status: 'completed', mode: 'execute', has_snapshot: false })
      );

      renderWithProviders(<ChannelPipelineTab />);

      // /rollback works on legacy runs, so Rollback still renders...
      expect(await screen.findByRole('button', { name: /rollback/i })).toBeInTheDocument();
      // ...and there is no snapshot-restore action to offer.
      expect(screen.queryByRole('button', { name: /undo this run/i })).toBeNull();
    });

    it('does not call the rollback API when the confirm dialog is cancelled', async () => {
      const user = userEvent.setup();
      let rollbackCalled = false;
      server.use(
        http.post('/api/channel-pipeline/executions/:id/rollback', () => {
          rollbackCalled = true;
          return HttpResponse.json({ success: true, entities_removed: 0, entities_restored: 0 });
        })
      );

      mockDataStore.channelPipelineExecutions.push(
        createMockChannelPipelineExecution({ status: 'completed', mode: 'execute', channels_created: 2 })
      );

      renderWithProviders(<ChannelPipelineTab />);

      await user.click(await screen.findByRole('button', { name: /rollback/i }));

      await waitFor(() => {
        expect(screen.getByText(/confirm.*rollback/i)).toBeInTheDocument();
      });
      // The dialog explains the legacy-undo mechanism and (h2oxl) that this
      // run has no snapshot, not just a bare confirmation.
      expect(screen.getByText(/legacy per-run undo/i)).toBeInTheDocument();
      // Text spans a <strong> boundary ("no pre-run snapshot"), so match against
      // the dialog's full text content rather than a single node.
      expect(screen.getByRole('dialog', { name: 'Confirm Rollback' }).textContent).toMatch(/no pre-run snapshot/i);

      await user.click(screen.getByRole('button', { name: /cancel/i }));

      await waitFor(() => {
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      });

      expect(rollbackCalled).toBe(false);
    });
  });

  describe('snapshot revert affordance (ADR-010 uc51o.7)', () => {
    it('offers snapshot recovery for a failed planned run that has recovery evidence', async () => {
      mockDataStore.channelPipelineExecutions.push(
        createMockChannelPipelineExecution({ status: 'failed', mode: 'execute', has_snapshot: true })
      );

      renderWithProviders(<ChannelPipelineTab />);

      const recovery = await screen.findByRole('button', { name: /recover from snapshot/i });
      expect(recovery.title).toMatch(/failed run/i);
      expect(screen.getByText(/failed after some changes may have been applied/i)).toBeInTheDocument();
    });

    it('does not offer snapshot recovery for a failed run without evidence', async () => {
      mockDataStore.channelPipelineExecutions.push(
        createMockChannelPipelineExecution({ status: 'failed', mode: 'execute', has_snapshot: false })
      );
      renderWithProviders(<ChannelPipelineTab />);
      await screen.findByTestId('execution-item');
      expect(screen.queryByRole('button', { name: /recover from snapshot/i })).toBeNull();
    });

    it('shows the revert button when has_snapshot is true', async () => {
      mockDataStore.channelPipelineExecutions.push(
        createMockChannelPipelineExecution({ status: 'completed', mode: 'execute', has_snapshot: true })
      );

      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /undo this run/i })).toBeInTheDocument();
      });
    });

    // bead 09x38.9: the "Undo this run" tooltip must state it is a full
    // snapshot restore and contrast with the sibling Rollback action.
    it('gives the "Undo this run" button an explanatory tooltip distinguishing it from Rollback', async () => {
      mockDataStore.channelPipelineExecutions.push(
        createMockChannelPipelineExecution({ status: 'completed', mode: 'execute', has_snapshot: true })
      );

      renderWithProviders(<ChannelPipelineTab />);

      const undoBtn = await screen.findByRole('button', { name: /undo this run/i });
      expect(undoBtn.title).toMatch(/pre-run snapshot/i);
      expect(undoBtn.title).toMatch(/overwriting any changes made since/i);
      expect(undoBtn.title).toMatch(/unlike rollback/i);
    });

    it('hides the revert button when has_snapshot is false', async () => {
      mockDataStore.channelPipelineExecutions.push(
        createMockChannelPipelineExecution({ status: 'completed', mode: 'execute', has_snapshot: false })
      );

      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => {
        // Execution item should appear
        expect(screen.getByTestId('execution-item')).toBeInTheDocument();
        // But the revert button must not be rendered
        expect(screen.queryByRole('button', { name: /undo this run/i })).toBeNull();
      });
    });

    it('hides the revert button for dry-run executions even with a snapshot', async () => {
      // dry-run executions never get a snapshot (ADR-010 §D2), so this combo
      // should not arise in production — but the UI must still be safe.
      mockDataStore.channelPipelineExecutions.push(
        createMockChannelPipelineExecution({ status: 'completed', mode: 'dry_run', has_snapshot: true })
      );

      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => {
        expect(screen.queryByRole('button', { name: /undo this run/i })).toBeNull();
      });
    });

    it('opens the confirm dialog with the overwrite warning when revert is clicked', async () => {
      const user = userEvent.setup();
      mockDataStore.channelPipelineExecutions.push(
        createMockChannelPipelineExecution({ status: 'completed', mode: 'execute', has_snapshot: true })
      );

      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /undo this run/i })).toBeInTheDocument();
      });

      await user.click(screen.getByRole('button', { name: /undo this run/i }));

      await waitFor(() => {
        expect(screen.getByRole('dialog', { name: 'Undo This Run' })).toBeInTheDocument();
        // ADR-010 §D5 mandatory overwrite warning must be visible
        expect(screen.getByTestId('revert-warning')).toBeInTheDocument();
        expect(screen.getByText(/overwrite the current stream assignments/i)).toBeInTheDocument();
        // bead 09x38.9: the confirm copy must contrast with the Rollback action.
        expect(screen.getByText(/unlike rollback, this restores every affected channel/i)).toBeInTheDocument();
      });
    });

    it('does not call the restore API when the confirm dialog is cancelled', async () => {
      const user = userEvent.setup();
      let restoreCalled = false;
      server.use(
        http.post('/api/channel-pipeline/executions/:id/restore-snapshot', () => {
          restoreCalled = true;
          return HttpResponse.json({ success: true, removed_channels: 0, restored_channels: 0, failed_channels: [] });
        })
      );

      mockDataStore.channelPipelineExecutions.push(
        createMockChannelPipelineExecution({ status: 'completed', mode: 'execute', has_snapshot: true })
      );

      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /undo this run/i })).toBeInTheDocument();
      });

      await user.click(screen.getByRole('button', { name: /undo this run/i }));

      await waitFor(() => {
        expect(screen.getByRole('dialog')).toBeInTheDocument();
      });

      await user.click(screen.getByRole('button', { name: /cancel/i }));

      await waitFor(() => {
        expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
      });

      expect(restoreCalled).toBe(false);
    });

    it('calls restore-snapshot with confirm=true and shows result summary on confirm', async () => {
      const user = userEvent.setup();
      mockDataStore.channelPipelineExecutions.push(
        createMockChannelPipelineExecution({
          status: 'completed',
          mode: 'execute',
          has_snapshot: true,
          channels_created: 3,
        })
      );

      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /undo this run/i })).toBeInTheDocument();
      });

      await user.click(screen.getByRole('button', { name: /undo this run/i }));

      await waitFor(() => {
        expect(screen.getByTestId('revert-confirm-btn')).toBeInTheDocument();
      });

      await user.click(screen.getByTestId('revert-confirm-btn'));

      // After confirm: result summary appears
      await waitFor(() => {
        expect(screen.getByTestId('revert-result-stats')).toBeInTheDocument();
        expect(screen.getByTestId('revert-restored-count')).toBeInTheDocument();
      });
    });

    it('surfaces partial failures in the result summary — never shows as plain success', async () => {
      const user = userEvent.setup();
      server.use(
        http.post('/api/channel-pipeline/executions/:id/restore-snapshot', () => {
          return HttpResponse.json({
            success: false,
            removed_channels: 2,
            restored_channels: 8,
            failed_channels: [
              { id: 101, name: 'ESPN HD', error: 'Channel not found in Dispatcharr' },
              { id: 102, name: 'CNN', error: 'Stream 9999 no longer exists' },
            ],
          });
        })
      );

      mockDataStore.channelPipelineExecutions.push(
        createMockChannelPipelineExecution({ status: 'completed', mode: 'execute', has_snapshot: true })
      );

      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /undo this run/i })).toBeInTheDocument();
      });

      await user.click(screen.getByRole('button', { name: /undo this run/i }));
      await waitFor(() => screen.getByTestId('revert-confirm-btn'));
      await user.click(screen.getByTestId('revert-confirm-btn'));

      await waitFor(() => {
        // Partial-failure warning must be present
        expect(screen.getByTestId('revert-partial-failure')).toBeInTheDocument();
        // Failed channels list must appear
        expect(screen.getByTestId('revert-failed-channels')).toBeInTheDocument();
        expect(screen.getByText('ESPN HD')).toBeInTheDocument();
        expect(screen.getByText('CNN')).toBeInTheDocument();
        expect(screen.getByText('Channel not found in Dispatcharr')).toBeInTheDocument();
        // Counts rendered
        expect(screen.getByTestId('revert-restored-count').textContent).toBe('8');
        expect(screen.getByTestId('revert-failed-count').textContent).toBe('2');
      });
    });
  });

  describe('import/export', () => {
    it('shows import/export buttons', () => {
      renderWithProviders(<ChannelPipelineTab />);

      expect(screen.getByRole('button', { name: /import/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /export/i })).toBeInTheDocument();
    });

    it('exports rules as YAML', async () => {
      const user = userEvent.setup();
      mockDataStore.channelPipelineRules.push(
        createMockChannelPipelineRule({ name: 'Export Me' })
      );

      renderWithProviders(<ChannelPipelineTab />);

      await user.click(screen.getByRole('button', { name: /export/i }));

      const exportDialog = await screen.findByRole('dialog', { name: 'Export Rules (YAML)' });
      expectDialogLabelledByVisibleHeading(exportDialog, 'Export Rules (YAML)');
      expect(within(exportDialog).getByLabelText(/exported yaml/i)).toBeInTheDocument();
    });

    it('opens import dialog', async () => {
      const user = userEvent.setup();
      renderWithProviders(<ChannelPipelineTab />);

      await user.click(screen.getByRole('button', { name: /import/i }));

      await waitFor(() => {
        expect(screen.getByRole('dialog', { name: 'Import Rules' })).toBeInTheDocument();
        expect(screen.getByLabelText(/yaml content/i)).toBeInTheDocument();
      });
    });

    it('keeps both import and export textareas beneath the Channel Pipeline root', async () => {
      const user = userEvent.setup();
      renderWithProviders(<ChannelPipelineTab />);

      await user.click(screen.getByRole('button', { name: /^import$/i }));
      const importTextarea = await screen.findByLabelText(/yaml content/i);
      expect(importTextarea.closest('.channel-pipeline-tab')).not.toBeNull();

      await user.click(within(screen.getByRole('dialog')).getByRole('button', { name: /close/i }));
      await user.click(screen.getByRole('button', { name: /^export$/i }));
      const exportTextarea = await screen.findByLabelText(/exported yaml/i);
      expect(exportTextarea.closest('.channel-pipeline-tab')).not.toBeNull();
    });

    it('imports rules from YAML', async () => {
      const user = userEvent.setup();
      renderWithProviders(<ChannelPipelineTab />);

      // Open import dialog
      await user.click(screen.getByRole('button', { name: /^import$/i }));

      await waitFor(() => {
        expect(screen.getByLabelText(/yaml content/i)).toBeInTheDocument();
      });

      // Type YAML content into textarea
      const textarea = screen.getByLabelText(/yaml content/i);
      await user.type(textarea, 'rules:');

      // Click the Import button inside the dialog
      const dialog = screen.getByRole('dialog', { name: 'Import Rules' });
      const importButton = within(dialog).getByRole('button', { name: /^import$/i });
      await user.click(importButton);

      await waitFor(() => {
        expect(screen.getByText(/imported rules.*1 created/i)).toBeInTheDocument();
      });
    });
  });

  describe('error handling', () => {
    it('shows error message when fetch fails', async () => {
      server.use(
        http.get('/api/channel-pipeline/rules', () => {
          return new HttpResponse(null, { status: 500 });
        })
      );

      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => {
        expect(screen.getByText(/failed to load/i)).toBeInTheDocument();
      });
    });

    it('shows retry button on error', async () => {
      server.use(
        http.get('/api/channel-pipeline/rules', () => {
          return new HttpResponse(null, { status: 500 });
        })
      );

      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument();
      });
    });

    it('shows error toast when run fails', async () => {
      mockDataStore.channelPipelineRules.push(
        createMockChannelPipelineRule({ enabled: true })
      );

      server.use(
        http.post('/api/channel-pipeline/run', () => {
          return new HttpResponse(
            JSON.stringify({ detail: 'Pipeline failed' }),
            { status: 500 }
          );
        })
      );

      const user = userEvent.setup();
      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /^run$/i })).toBeInTheDocument();
      });

      await user.click(screen.getByRole('button', { name: /^run$/i }));

      await waitFor(() => {
        expect(screen.getByText(/pipeline failed/i)).toBeInTheDocument();
      });
    });
  });

  describe('loading states', () => {
    it('shows loading skeleton while fetching rules', async () => {
      renderWithProviders(<ChannelPipelineTab />);

      expect(screen.getByTestId('rules-skeleton')).toBeInTheDocument();

      await waitFor(() => {
        expect(screen.queryByTestId('rules-skeleton')).not.toBeInTheDocument();
      });
    });
  });

  describe('filters and search', () => {
    it('allows filtering rules by enabled status', async () => {
      const user = userEvent.setup();
      mockDataStore.channelPipelineRules.push(
        createMockChannelPipelineRule({ name: 'Rule One', enabled: true }),
        createMockChannelPipelineRule({ name: 'Rule Two', enabled: false })
      );

      renderWithProviders(<ChannelPipelineTab />);

      // Both rules should be visible initially
      await waitFor(() => {
        expect(screen.getByText('Rule One')).toBeInTheDocument();
        expect(screen.getByText('Rule Two')).toBeInTheDocument();
      });

      // Filter to enabled only
      await user.click(screen.getByRole('button', { name: /filter/i }));
      await user.click(screen.getByText(/enabled only/i));

      await waitFor(() => {
        expect(screen.getByText('Rule One')).toBeInTheDocument();
        expect(screen.queryByText('Rule Two')).not.toBeInTheDocument();
      });
    });

    it('allows searching rules by name', async () => {
      const user = userEvent.setup();
      mockDataStore.channelPipelineRules.push(
        createMockChannelPipelineRule({ name: 'ESPN Rule' }),
        createMockChannelPipelineRule({ name: 'FOX Rule' })
      );

      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => {
        expect(screen.getByText('ESPN Rule')).toBeInTheDocument();
      });

      await user.type(screen.getByPlaceholderText(/search/i), 'ESPN');

      await waitFor(() => {
        expect(screen.getByText('ESPN Rule')).toBeInTheDocument();
        expect(screen.queryByText('FOX Rule')).not.toBeInTheDocument();
      });
    });
  });

  describe('drag and drop reordering', () => {
    it('allows reordering rules by drag and drop', async () => {
      mockDataStore.channelPipelineRules.push(
        createMockChannelPipelineRule({ name: 'First', priority: 1 }),
        createMockChannelPipelineRule({ name: 'Second', priority: 2 }),
        createMockChannelPipelineRule({ name: 'Third', priority: 3 })
      );

      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => {
        expect(screen.getByText('First')).toBeInTheDocument();
      });

      // Verify drag handles are present
      const dragHandles = screen.getAllByTestId('drag-handle');
      expect(dragHandles).toHaveLength(3);
    });
  });

  /**
   * GH #755 — on an instance with more rules than uvicorn's
   * `--limit-concurrency` (`backend/entrypoint.sh`, default 100), copying a
   * rule fired one `PUT /rules/{id}` per rule at once. Most came back 503, the
   * operator got an error toast, and the copy stayed pinned to the bottom of
   * the list until the page was reloaded.
   *
   * The 120-rule fixture is part of the guard: below the limit the burst never
   * failed, so a small fixture cannot reproduce the reported failure.
   */
  describe('GH #755 rule copy at scale', () => {
    const RULE_COUNT = 120;

    const ruleNamesInOrder = () =>
      screen.getAllByTestId('rule-row').map(row => row.querySelector('.rule-name')?.textContent);

    it('shows the copy directly after the original without a page reload', async () => {
      const user = userEvent.setup();
      const perRuleWrites: string[] = [];
      server.events.on('request:start', ({ request }) => {
        const path = new URL(request.url).pathname;
        if (request.method === 'PUT' && /\/channel-pipeline\/rules\/\d+$/.test(path)) {
          perRuleWrites.push(path);
        }
      });

      for (let i = 0; i < RULE_COUNT; i++) {
        mockDataStore.channelPipelineRules.push(
          createMockChannelPipelineRule({ name: `Bulk Rule ${String(i).padStart(3, '0')}`, priority: i })
        );
      }

      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => {
        expect(screen.getByText('Bulk Rule 000')).toBeInTheDocument();
      });

      const originalRow = screen.getAllByTestId('rule-row')[1];
      await user.click(within(originalRow).getByRole('button', { name: /duplicate/i }));

      await waitFor(() => {
        expect(screen.getByText(/Bulk Rule 001 \(Copy\)/)).toBeInTheDocument();
      });

      // The list itself must be right — not just after a refresh.
      const names = ruleNamesInOrder();
      expect(names[names.indexOf('Bulk Rule 001') + 1]).toBe('Bulk Rule 001 (Copy)');
      expect(names[names.length - 1]).not.toBe('Bulk Rule 001 (Copy)');

      // ...and it must not have taken a write per rule to get there.
      expect(perRuleWrites).toHaveLength(0);

      server.events.removeAllListeners('request:start');
    });
  });

  describe('keyboard navigation', () => {
    it('supports keyboard navigation in rules list', async () => {
      mockDataStore.channelPipelineRules.push(
        createMockChannelPipelineRule({ name: 'Rule 1' }),
        createMockChannelPipelineRule({ name: 'Rule 2' })
      );

      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => {
        expect(screen.getByText('Rule 1')).toBeInTheDocument();
      });

      // Rule rows should be focusable (tabIndex=0)
      const rows = screen.getAllByTestId('rule-row');
      expect(rows.length).toBe(2);
      expect(rows[0]).toHaveAttribute('tabindex', '0');

      // Focus the first row directly and verify it works
      rows[0].focus();
      expect(document.activeElement).toBe(rows[0]);
    });
  });

  describe('responsive layout', () => {
    it('renders mobile-friendly layout', () => {
      // Mock mobile viewport
      Object.defineProperty(window, 'innerWidth', { value: 375 });
      window.dispatchEvent(new Event('resize'));

      renderWithProviders(<ChannelPipelineTab />);

      expect(screen.getByTestId('channel-pipeline-tab')).toHaveClass('mobile');
    });
  });

  describe('statistics summary', () => {
    it('shows summary statistics', async () => {
      mockDataStore.channelPipelineRules.push(
        createMockChannelPipelineRule({ enabled: true, match_count: 50 }),
        createMockChannelPipelineRule({ enabled: true, match_count: 30 }),
        createMockChannelPipelineRule({ enabled: false, match_count: 20 })
      );

      renderWithProviders(<ChannelPipelineTab />);

      // Statistics are displayed as value and label in separate elements
      await waitFor(() => {
        const statsContainer = document.querySelector('.channel-pipeline-stats');
        expect(statsContainer).toBeInTheDocument();
        // Check that stat values exist within the stats container
        const statValues = statsContainer!.querySelectorAll('.stat-value');
        const values = Array.from(statValues).map(el => el.textContent);
        expect(values).toContain('3');   // 3 rules total
        expect(values).toContain('2');   // 2 enabled
        expect(values).toContain('100'); // 100 total matches
      });
    });
  });
});
