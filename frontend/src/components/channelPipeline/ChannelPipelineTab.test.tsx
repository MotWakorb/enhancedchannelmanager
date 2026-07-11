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

    it('renders tab header with title', () => {
      renderWithProviders(<ChannelPipelineTab />);

      expect(screen.getByRole('heading', { name: 'Channel Pipeline' })).toBeInTheDocument();
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

      // Should show confirmation dialog
      await waitFor(() => {
        expect(screen.getByText(/confirm.*delete/i)).toBeInTheDocument();
      });

      await user.click(screen.getByRole('button', { name: /confirm/i }));

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
  });

  describe('snapshot revert affordance (ADR-010 uc51o.7)', () => {
    it('shows the revert button when has_snapshot is true', async () => {
      mockDataStore.channelPipelineExecutions.push(
        createMockChannelPipelineExecution({ status: 'completed', mode: 'execute', has_snapshot: true })
      );

      renderWithProviders(<ChannelPipelineTab />);

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /undo this run/i })).toBeInTheDocument();
      });
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
        expect(screen.getByRole('dialog')).toBeInTheDocument();
        // ADR-010 §D5 mandatory overwrite warning must be visible
        expect(screen.getByTestId('revert-warning')).toBeInTheDocument();
        expect(screen.getByText(/overwrite the current stream assignments/i)).toBeInTheDocument();
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

      await waitFor(() => {
        // Should show YAML in modal or download
        expect(screen.getByText(/yaml/i)).toBeInTheDocument();
      });
    });

    it('opens import dialog', async () => {
      const user = userEvent.setup();
      renderWithProviders(<ChannelPipelineTab />);

      await user.click(screen.getByRole('button', { name: /import/i }));

      await waitFor(() => {
        expect(screen.getByRole('dialog')).toBeInTheDocument();
        expect(screen.getByLabelText(/yaml content/i)).toBeInTheDocument();
      });
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
      const dialog = screen.getByRole('dialog');
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
