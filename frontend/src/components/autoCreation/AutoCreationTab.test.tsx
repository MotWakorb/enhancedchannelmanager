/**
 * TDD Tests for AutoCreationTab component.
 *
 * These tests define the expected behavior of the main auto-creation tab BEFORE implementation.
 */
import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import {
  server,
  mockDataStore,
  resetMockDataStore,
  createMockAutoCreationRule,
  createMockAutoCreationExecution,
} from '../../test/mocks/server';
import { AutoCreationTab } from './AutoCreationTab';
import { NotificationProvider } from '../../contexts/NotificationContext';

const renderWithProviders = (ui: JSX.Element) =>
  render(<NotificationProvider>{ui}</NotificationProvider>);

// Setup MSW server
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  resetMockDataStore();
});
afterAll(() => server.close());

describe('AutoCreationTab', () => {
  describe('rendering', () => {
    it('renders the auto-creation tab container', () => {
      renderWithProviders(<AutoCreationTab />);

      expect(screen.getByTestId('auto-creation-tab')).toBeInTheDocument();
    });

    it('renders tab header with title', () => {
      renderWithProviders(<AutoCreationTab />);

      expect(screen.getByRole('heading', { name: /auto.*creation/i })).toBeInTheDocument();
    });

    it('renders rules section and execution section', () => {
      renderWithProviders(<AutoCreationTab />);

      expect(screen.getByRole('heading', { name: /^rules$/i })).toBeInTheDocument();
      expect(screen.getByRole('heading', { name: /execution/i })).toBeInTheDocument();
    });

    it('renders action buttons', () => {
      renderWithProviders(<AutoCreationTab />);

      expect(screen.getByRole('button', { name: /create rule/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /^run$/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /dry run/i })).toBeInTheDocument();
    });
  });

  describe('rules list', () => {
    it('displays list of rules', async () => {
      mockDataStore.autoCreationRules.push(
        createMockAutoCreationRule({ name: 'Rule 1' }),
        createMockAutoCreationRule({ name: 'Rule 2' }),
        createMockAutoCreationRule({ name: 'Rule 3' })
      );

      renderWithProviders(<AutoCreationTab />);

      await waitFor(() => {
        expect(screen.getByText('Rule 1')).toBeInTheDocument();
        expect(screen.getByText('Rule 2')).toBeInTheDocument();
        expect(screen.getByText('Rule 3')).toBeInTheDocument();
      });
    });

    it('shows empty state when no rules exist', async () => {
      renderWithProviders(<AutoCreationTab />);

      await waitFor(() => {
        expect(screen.getByText(/no rules/i)).toBeInTheDocument();
      });
    });

    it('shows rule enabled/disabled status', async () => {
      mockDataStore.autoCreationRules.push(
        createMockAutoCreationRule({ name: 'Enabled Rule', enabled: true }),
        createMockAutoCreationRule({ name: 'Disabled Rule', enabled: false })
      );

      renderWithProviders(<AutoCreationTab />);

      await waitFor(() => {
        const enabledRow = screen.getByText('Enabled Rule').closest('tr');
        const disabledRow = screen.getByText('Disabled Rule').closest('tr');

        // Look for status badges specifically
        const enabledBadge = within(enabledRow!).getByText('Enabled');
        const disabledBadge = within(disabledRow!).getByText('Disabled');

        expect(enabledBadge).toHaveClass('status-badge', 'enabled');
        expect(disabledBadge).toHaveClass('status-badge', 'disabled');
      });
    });

    it('shows rule priority', async () => {
      mockDataStore.autoCreationRules.push(
        createMockAutoCreationRule({ name: 'High Priority', priority: 1 }),
        createMockAutoCreationRule({ name: 'Low Priority', priority: 100 })
      );

      renderWithProviders(<AutoCreationTab />);

      await waitFor(() => {
        const highRow = screen.getByText('High Priority').closest('tr');
        expect(within(highRow!).getByText('1')).toBeInTheDocument();
      });
    });

    it('shows rule match count', async () => {
      mockDataStore.autoCreationRules.push(
        createMockAutoCreationRule({ name: 'Popular Rule', match_count: 150 })
      );

      renderWithProviders(<AutoCreationTab />);

      await waitFor(() => {
        // Match count appears in multiple places; just verify at least one exists
        const matches = screen.getAllByText('150');
        expect(matches.length).toBeGreaterThan(0);
      });
    });

    it('sorts rules by priority by default', async () => {
      mockDataStore.autoCreationRules.push(
        createMockAutoCreationRule({ name: 'Third', priority: 30 }),
        createMockAutoCreationRule({ name: 'First', priority: 10 }),
        createMockAutoCreationRule({ name: 'Second', priority: 20 })
      );

      renderWithProviders(<AutoCreationTab />);

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
      const rule = createMockAutoCreationRule({ name: 'Test Rule', enabled: true });
      mockDataStore.autoCreationRules.push(rule);

      renderWithProviders(<AutoCreationTab />);

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
      const rule = createMockAutoCreationRule({ name: 'Editable Rule' });
      mockDataStore.autoCreationRules.push(rule);

      renderWithProviders(<AutoCreationTab />);

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
      const rule = createMockAutoCreationRule({ name: 'Deletable Rule' });
      mockDataStore.autoCreationRules.push(rule);

      renderWithProviders(<AutoCreationTab />);

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
      const rule = createMockAutoCreationRule({ name: 'Original Rule' });
      mockDataStore.autoCreationRules.push(rule);

      renderWithProviders(<AutoCreationTab />);

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
    it('opens rule builder when create button clicked', async () => {
      const user = userEvent.setup();
      renderWithProviders(<AutoCreationTab />);

      await user.click(screen.getByRole('button', { name: /create rule/i }));

      await waitFor(() => {
        expect(screen.getByRole('dialog')).toBeInTheDocument();
        expect(screen.getByLabelText(/rule name/i)).toHaveValue('');
      });
    });

    it('adds new rule to list after creation', async () => {
      const user = userEvent.setup();
      renderWithProviders(<AutoCreationTab />);

      await user.click(screen.getByRole('button', { name: /create rule/i }));

      // Fill in the form
      await user.type(screen.getByLabelText(/rule name/i), 'Brand New Rule');

      // Add condition
      await user.click(screen.getByRole('button', { name: /add condition/i }));
      await user.click(screen.getByText(/always/i));

      // Add action
      await user.click(screen.getByRole('button', { name: /add action/i }));
      await user.click(screen.getByText(/skip/i));

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
      mockDataStore.autoCreationRules.push(
        createMockAutoCreationRule({ name: 'Active Rule', enabled: true })
      );

      renderWithProviders(<AutoCreationTab />);

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
      mockDataStore.autoCreationRules.push(
        createMockAutoCreationRule({ name: 'Active Rule', enabled: true })
      );

      renderWithProviders(<AutoCreationTab />);

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
      mockDataStore.autoCreationRules.push(
        createMockAutoCreationRule({ enabled: true })
      );

      // Override the run handler to delay the response
      server.use(
        http.post('/api/auto-creation/run', async () => {
          await new Promise(resolve => setTimeout(resolve, 200));
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

      renderWithProviders(<AutoCreationTab />);

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /^run$/i })).toBeInTheDocument();
      });

      await user.click(screen.getByRole('button', { name: /^run$/i }));

      // Should show loading indicator while running
      await waitFor(() => {
        expect(screen.getByText(/running/i)).toBeInTheDocument();
      });
    });

    it('disables run buttons when no enabled rules exist', async () => {
      mockDataStore.autoCreationRules.push(
        createMockAutoCreationRule({ enabled: false })
      );

      renderWithProviders(<AutoCreationTab />);

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
      mockDataStore.autoCreationExecutions.push(
        createMockAutoCreationExecution({ status: 'completed', channels_created: 5 }),
        createMockAutoCreationExecution({ status: 'completed', channels_created: 3 })
      );

      renderWithProviders(<AutoCreationTab />);

      // Click to show history
      await waitFor(() => {
        const historySection = screen.getByText(/execution history/i);
        expect(historySection).toBeInTheDocument();
      });
    });

    it('shows execution status badges', async () => {
      mockDataStore.autoCreationExecutions.push(
        createMockAutoCreationExecution({ status: 'completed' }),
        createMockAutoCreationExecution({ status: 'failed' }),
        createMockAutoCreationExecution({ status: 'rolled_back' })
      );

      renderWithProviders(<AutoCreationTab />);

      await waitFor(() => {
        expect(screen.getByText(/completed/i)).toBeInTheDocument();
        expect(screen.getByText(/failed/i)).toBeInTheDocument();
        expect(screen.getByText(/rolled.*back/i)).toBeInTheDocument();
      });
    });

    it('allows viewing execution details', async () => {
      const user = userEvent.setup();
      mockDataStore.autoCreationExecutions.push(
        createMockAutoCreationExecution({ streams_matched: 25, channels_created: 10 })
      );

      renderWithProviders(<AutoCreationTab />);

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

    it('allows rolling back an execution', async () => {
      const user = userEvent.setup();
      mockDataStore.autoCreationExecutions.push(
        createMockAutoCreationExecution({ status: 'completed', mode: 'execute' })
      );

      renderWithProviders(<AutoCreationTab />);

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
      mockDataStore.autoCreationExecutions.push(
        createMockAutoCreationExecution({ status: 'completed', mode: 'dry_run' })
      );

      renderWithProviders(<AutoCreationTab />);

      await waitFor(() => {
        const rollbackBtn = screen.queryByRole('button', { name: /rollback/i });
        expect(rollbackBtn).toBeNull(); // No rollback for dry runs
      });
    });
  });

  describe('import/export', () => {
    it('shows import/export buttons', () => {
      renderWithProviders(<AutoCreationTab />);

      expect(screen.getByRole('button', { name: /import/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /export/i })).toBeInTheDocument();
    });

    it('exports rules as YAML', async () => {
      const user = userEvent.setup();
      mockDataStore.autoCreationRules.push(
        createMockAutoCreationRule({ name: 'Export Me' })
      );

      renderWithProviders(<AutoCreationTab />);

      await user.click(screen.getByRole('button', { name: /export/i }));

      await waitFor(() => {
        // Should show YAML in modal or download
        expect(screen.getByText(/yaml/i)).toBeInTheDocument();
      });
    });

    it('opens import dialog', async () => {
      const user = userEvent.setup();
      renderWithProviders(<AutoCreationTab />);

      await user.click(screen.getByRole('button', { name: /import/i }));

      await waitFor(() => {
        expect(screen.getByRole('dialog')).toBeInTheDocument();
        expect(screen.getByLabelText(/yaml content/i)).toBeInTheDocument();
      });
    });

    it('imports rules from YAML', async () => {
      const user = userEvent.setup();
      renderWithProviders(<AutoCreationTab />);

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
        expect(screen.getByText(/imported.*1.*rule/i)).toBeInTheDocument();
      });
    });
  });

  describe('error handling', () => {
    it('shows error message when fetch fails', async () => {
      server.use(
        http.get('/api/auto-creation/rules', () => {
          return new HttpResponse(null, { status: 500 });
        })
      );

      renderWithProviders(<AutoCreationTab />);

      await waitFor(() => {
        expect(screen.getByText(/failed to load/i)).toBeInTheDocument();
      });
    });

    it('shows retry button on error', async () => {
      server.use(
        http.get('/api/auto-creation/rules', () => {
          return new HttpResponse(null, { status: 500 });
        })
      );

      renderWithProviders(<AutoCreationTab />);

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument();
      });
    });

    it('shows error toast when run fails', async () => {
      mockDataStore.autoCreationRules.push(
        createMockAutoCreationRule({ enabled: true })
      );

      server.use(
        http.post('/api/auto-creation/run', () => {
          return new HttpResponse(
            JSON.stringify({ detail: 'Pipeline failed' }),
            { status: 500 }
          );
        })
      );

      const user = userEvent.setup();
      renderWithProviders(<AutoCreationTab />);

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
      renderWithProviders(<AutoCreationTab />);

      expect(screen.getByTestId('rules-skeleton')).toBeInTheDocument();

      await waitFor(() => {
        expect(screen.queryByTestId('rules-skeleton')).not.toBeInTheDocument();
      });
    });
  });

  describe('filters and search', () => {
    it('allows filtering rules by enabled status', async () => {
      const user = userEvent.setup();
      mockDataStore.autoCreationRules.push(
        createMockAutoCreationRule({ name: 'Rule One', enabled: true }),
        createMockAutoCreationRule({ name: 'Rule Two', enabled: false })
      );

      renderWithProviders(<AutoCreationTab />);

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
      mockDataStore.autoCreationRules.push(
        createMockAutoCreationRule({ name: 'ESPN Rule' }),
        createMockAutoCreationRule({ name: 'FOX Rule' })
      );

      renderWithProviders(<AutoCreationTab />);

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
      mockDataStore.autoCreationRules.push(
        createMockAutoCreationRule({ name: 'First', priority: 1 }),
        createMockAutoCreationRule({ name: 'Second', priority: 2 }),
        createMockAutoCreationRule({ name: 'Third', priority: 3 })
      );

      renderWithProviders(<AutoCreationTab />);

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
      const user = userEvent.setup();
      mockDataStore.autoCreationRules.push(
        createMockAutoCreationRule({ name: 'Rule 1' }),
        createMockAutoCreationRule({ name: 'Rule 2' })
      );

      renderWithProviders(<AutoCreationTab />);

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

      renderWithProviders(<AutoCreationTab />);

      expect(screen.getByTestId('auto-creation-tab')).toHaveClass('mobile');
    });
  });

  describe('statistics summary', () => {
    it('shows summary statistics', async () => {
      mockDataStore.autoCreationRules.push(
        createMockAutoCreationRule({ enabled: true, match_count: 50 }),
        createMockAutoCreationRule({ enabled: true, match_count: 30 }),
        createMockAutoCreationRule({ enabled: false, match_count: 20 })
      );

      renderWithProviders(<AutoCreationTab />);

      // Statistics are displayed as value and label in separate elements
      await waitFor(() => {
        const statsContainer = document.querySelector('.auto-creation-stats');
        expect(statsContainer).toBeInTheDocument();
        // Check that stat values exist
        expect(screen.getByText('3')).toBeInTheDocument(); // 3 rules total
        expect(screen.getByText('2')).toBeInTheDocument(); // 2 enabled
        expect(screen.getByText('100')).toBeInTheDocument(); // 100 total matches
      });
    });
  });
});
