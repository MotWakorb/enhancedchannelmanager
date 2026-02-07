/**
 * TDD Tests for RuleBuilder component.
 *
 * These tests define the expected behavior of the component BEFORE implementation.
 */
import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {
  server,
  mockDataStore,
  resetMockDataStore,
  createMockAutoCreationRule,
} from '../../test/mocks/server';
import { RuleBuilder } from './RuleBuilder';
import type { AutoCreationRule, CreateRuleData } from '../../types/autoCreation';

// Setup MSW server
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  resetMockDataStore();
});
afterAll(() => server.close());

describe('RuleBuilder', () => {
  describe('rendering', () => {
    it('renders the rule builder form', () => {
      render(<RuleBuilder onSave={vi.fn()} onCancel={vi.fn()} />);

      expect(screen.getByLabelText(/rule name/i)).toBeInTheDocument();
      // Check for section headings (h3 elements)
      expect(screen.getByRole('heading', { name: /conditions/i })).toBeInTheDocument();
      expect(screen.getByRole('heading', { name: /actions/i })).toBeInTheDocument();
    });

    it('renders save and cancel buttons', () => {
      render(<RuleBuilder onSave={vi.fn()} onCancel={vi.fn()} />);

      expect(screen.getByRole('button', { name: /save/i })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: /cancel/i })).toBeInTheDocument();
    });

    it('renders with default values for new rule', () => {
      render(<RuleBuilder onSave={vi.fn()} onCancel={vi.fn()} />);

      const nameInput = screen.getByLabelText(/rule name/i) as HTMLInputElement;
      expect(nameInput.value).toBe('');

      // Should have enabled checkbox checked by default
      const enabledCheckbox = screen.getByLabelText(/enabled/i) as HTMLInputElement;
      expect(enabledCheckbox.checked).toBe(true);
    });

    it('renders with existing rule values when editing', () => {
      const existingRule: AutoCreationRule = {
        id: 1,
        name: 'Existing Rule',
        description: 'Rule description',
        enabled: false,
        priority: 5,
        conditions: [{ type: 'always' }],
        actions: [{ type: 'skip' }],
        run_on_refresh: true,
        stop_on_first_match: false,
        match_count: 10,
        created_at: '2024-01-01T00:00:00Z',
        updated_at: '2024-01-02T00:00:00Z',
      };

      render(<RuleBuilder rule={existingRule} onSave={vi.fn()} onCancel={vi.fn()} />);

      expect(screen.getByLabelText(/rule name/i)).toHaveValue('Existing Rule');
      expect(screen.getByLabelText(/description/i)).toHaveValue('Rule description');
      expect(screen.getByLabelText(/enabled/i)).not.toBeChecked();
      expect(screen.getByLabelText(/priority/i)).toHaveValue(5);
    });
  });

  describe('form fields', () => {
    it('allows entering rule name', async () => {
      const user = userEvent.setup();
      render(<RuleBuilder onSave={vi.fn()} onCancel={vi.fn()} />);

      const nameInput = screen.getByLabelText(/rule name/i);
      await user.type(nameInput, 'My New Rule');

      expect(nameInput).toHaveValue('My New Rule');
    });

    it('allows entering description', async () => {
      const user = userEvent.setup();
      render(<RuleBuilder onSave={vi.fn()} onCancel={vi.fn()} />);

      const descInput = screen.getByLabelText(/description/i);
      await user.type(descInput, 'This rule does something');

      expect(descInput).toHaveValue('This rule does something');
    });

    it('allows setting priority', async () => {
      const user = userEvent.setup();
      render(<RuleBuilder onSave={vi.fn()} onCancel={vi.fn()} />);

      const priorityInput = screen.getByLabelText(/priority/i);
      await user.clear(priorityInput);
      await user.type(priorityInput, '10');

      expect(priorityInput).toHaveValue(10);
    });

    it('allows toggling enabled state', async () => {
      const user = userEvent.setup();
      render(<RuleBuilder onSave={vi.fn()} onCancel={vi.fn()} />);

      const enabledCheckbox = screen.getByLabelText(/enabled/i);
      expect(enabledCheckbox).toBeChecked();

      await user.click(enabledCheckbox);
      expect(enabledCheckbox).not.toBeChecked();
    });

    it('allows toggling run_on_refresh', async () => {
      const user = userEvent.setup();
      render(<RuleBuilder onSave={vi.fn()} onCancel={vi.fn()} />);

      const checkbox = screen.getByLabelText(/run on.*refresh/i);
      await user.click(checkbox);

      expect(checkbox).toBeChecked();
    });

    it('allows toggling stop_on_first_match', async () => {
      const user = userEvent.setup();
      render(<RuleBuilder onSave={vi.fn()} onCancel={vi.fn()} />);

      const checkbox = screen.getByLabelText(/stop on first match/i);
      // Default should be true
      expect(checkbox).toBeChecked();

      await user.click(checkbox);
      expect(checkbox).not.toBeChecked();
    });
  });

  describe('conditions section', () => {
    it('renders add condition button', () => {
      render(<RuleBuilder onSave={vi.fn()} onCancel={vi.fn()} />);

      expect(screen.getByRole('button', { name: /add condition/i })).toBeInTheDocument();
    });

    it('opens condition type selector when clicking add', async () => {
      const user = userEvent.setup();
      render(<RuleBuilder onSave={vi.fn()} onCancel={vi.fn()} />);

      await user.click(screen.getByRole('button', { name: /add condition/i }));

      // Should show condition type options
      await waitFor(() => {
        expect(screen.getByText(/stream name contains/i)).toBeInTheDocument();
      });
    });

    it('adds a condition when type is selected', async () => {
      const user = userEvent.setup();
      render(<RuleBuilder onSave={vi.fn()} onCancel={vi.fn()} />);

      await user.click(screen.getByRole('button', { name: /add condition/i }));
      await user.click(screen.getByText(/stream name contains/i));

      // Should show condition editor with the appropriate placeholder
      await waitFor(() => {
        expect(screen.getByPlaceholderText(/enter text to match/i)).toBeInTheDocument();
      });
    });

    it('allows removing a condition', async () => {
      const user = userEvent.setup();
      const ruleWithCondition: Partial<AutoCreationRule> = {
        conditions: [{ type: 'stream_name_contains', value: 'ESPN' }],
        actions: [{ type: 'skip' }],
      };

      render(
        <RuleBuilder
          rule={ruleWithCondition as AutoCreationRule}
          onSave={vi.fn()}
          onCancel={vi.fn()}
        />
      );

      // Find and click remove button for the condition
      const removeButton = screen.getByRole('button', { name: /remove condition/i });
      await user.click(removeButton);

      // Condition should be removed
      await waitFor(() => {
        expect(screen.queryByText('ESPN')).not.toBeInTheDocument();
      });
    });

    it('shows validation error when no conditions', async () => {
      const user = userEvent.setup();
      const onSave = vi.fn();
      render(<RuleBuilder onSave={onSave} onCancel={vi.fn()} />);

      // Fill in name but leave conditions empty
      await user.type(screen.getByLabelText(/rule name/i), 'Test Rule');

      // Try to save
      await user.click(screen.getByRole('button', { name: /save/i }));

      // Should show validation error
      await waitFor(() => {
        expect(screen.getByText(/at least one condition/i)).toBeInTheDocument();
      });

      // Should not have called onSave
      expect(onSave).not.toHaveBeenCalled();
    });
  });

  describe('actions section', () => {
    it('renders add action button', () => {
      render(<RuleBuilder onSave={vi.fn()} onCancel={vi.fn()} />);

      expect(screen.getByRole('button', { name: /add action/i })).toBeInTheDocument();
    });

    it('opens action type selector when clicking add', async () => {
      const user = userEvent.setup();
      render(<RuleBuilder onSave={vi.fn()} onCancel={vi.fn()} />);

      await user.click(screen.getByRole('button', { name: /add action/i }));

      // Should show action type options
      await waitFor(() => {
        expect(screen.getByText(/create channel/i)).toBeInTheDocument();
      });
    });

    it('adds an action when type is selected', async () => {
      const user = userEvent.setup();
      render(<RuleBuilder onSave={vi.fn()} onCancel={vi.fn()} />);

      await user.click(screen.getByRole('button', { name: /add action/i }));
      await user.click(screen.getByText(/create channel/i));

      // Should show action editor with template field
      await waitFor(() => {
        expect(screen.getByLabelText(/name template/i)).toBeInTheDocument();
      });
    });

    it('allows removing an action', async () => {
      const user = userEvent.setup();
      const ruleWithAction: Partial<AutoCreationRule> = {
        conditions: [{ type: 'always' }],
        actions: [{ type: 'create_channel', name_template: '{stream_name}' }],
      };

      render(
        <RuleBuilder
          rule={ruleWithAction as AutoCreationRule}
          onSave={vi.fn()}
          onCancel={vi.fn()}
        />
      );

      // Find and click remove button for the action
      const removeButton = screen.getByRole('button', { name: /remove action/i });
      await user.click(removeButton);

      // Action should be removed
      await waitFor(() => {
        expect(screen.queryByLabelText(/name template/i)).not.toBeInTheDocument();
      });
    });

    it('shows validation error when no actions', async () => {
      const user = userEvent.setup();
      const onSave = vi.fn();
      render(<RuleBuilder onSave={onSave} onCancel={vi.fn()} />);

      // Fill in name and add a condition
      await user.type(screen.getByLabelText(/rule name/i), 'Test Rule');
      await user.click(screen.getByRole('button', { name: /add condition/i }));
      await user.click(screen.getByText(/always/i));

      // Try to save without actions
      await user.click(screen.getByRole('button', { name: /save/i }));

      // Should show validation error
      await waitFor(() => {
        expect(screen.getByText(/at least one action/i)).toBeInTheDocument();
      });

      expect(onSave).not.toHaveBeenCalled();
    });
  });

  describe('save and cancel', () => {
    it('calls onSave with rule data when valid', async () => {
      const user = userEvent.setup();
      const onSave = vi.fn();
      render(<RuleBuilder onSave={onSave} onCancel={vi.fn()} />);

      // Fill in the form
      await user.type(screen.getByLabelText(/rule name/i), 'My Rule');
      await user.type(screen.getByLabelText(/description/i), 'Description');

      // Add condition
      await user.click(screen.getByRole('button', { name: /add condition/i }));
      await user.click(screen.getByText(/always/i));

      // Add action
      await user.click(screen.getByRole('button', { name: /add action/i }));
      await user.click(screen.getByText(/skip/i));

      // Save
      await user.click(screen.getByRole('button', { name: /save/i }));

      await waitFor(() => {
        expect(onSave).toHaveBeenCalledWith(
          expect.objectContaining({
            name: 'My Rule',
            description: 'Description',
            conditions: expect.arrayContaining([expect.objectContaining({ type: 'always' })]),
            actions: expect.arrayContaining([expect.objectContaining({ type: 'skip' })]),
          })
        );
      });
    });

    it('calls onCancel when cancel button clicked', async () => {
      const user = userEvent.setup();
      const onCancel = vi.fn();
      render(<RuleBuilder onSave={vi.fn()} onCancel={onCancel} />);

      await user.click(screen.getByRole('button', { name: /cancel/i }));

      expect(onCancel).toHaveBeenCalled();
    });

    it('shows confirmation dialog if form is dirty when canceling', async () => {
      const user = userEvent.setup();
      const onCancel = vi.fn();
      render(<RuleBuilder onSave={vi.fn()} onCancel={onCancel} />);

      // Make the form dirty by typing something
      await user.type(screen.getByLabelText(/rule name/i), 'Some text');

      // Click cancel
      await user.click(screen.getByRole('button', { name: /cancel/i }));

      // Should show confirmation dialog heading
      await waitFor(() => {
        expect(screen.getByRole('heading', { name: /unsaved changes/i })).toBeInTheDocument();
      });

      // Cancel should not have been called yet
      expect(onCancel).not.toHaveBeenCalled();
    });

    it('disables save button while saving', async () => {
      const user = userEvent.setup();
      let resolvePromise: () => void;
      const onSave = vi.fn().mockImplementation(() => {
        return new Promise<void>((resolve) => {
          resolvePromise = resolve;
        });
      });

      const ruleWithData: Partial<AutoCreationRule> = {
        name: 'Valid Rule',
        conditions: [{ type: 'always' }],
        actions: [{ type: 'skip' }],
      };

      render(
        <RuleBuilder
          rule={ruleWithData as AutoCreationRule}
          onSave={onSave}
          onCancel={vi.fn()}
        />
      );

      await user.click(screen.getByRole('button', { name: /save/i }));

      // Button should be disabled during save
      expect(screen.getByRole('button', { name: /sav(e|ing)/i })).toBeDisabled();

      // Resolve the promise
      resolvePromise!();

      await waitFor(() => {
        expect(screen.getByRole('button', { name: /save/i })).not.toBeDisabled();
      });
    });
  });

  describe('validation', () => {
    it('shows error when rule name is empty', async () => {
      const user = userEvent.setup();
      const ruleWithoutName: Partial<AutoCreationRule> = {
        conditions: [{ type: 'always' }],
        actions: [{ type: 'skip' }],
      };

      render(
        <RuleBuilder
          rule={ruleWithoutName as AutoCreationRule}
          onSave={vi.fn()}
          onCancel={vi.fn()}
        />
      );

      await user.click(screen.getByRole('button', { name: /save/i }));

      await waitFor(() => {
        expect(screen.getByText(/name is required/i)).toBeInTheDocument();
      });
    });

    it('validates before calling onSave', async () => {
      const user = userEvent.setup();
      const onSave = vi.fn();
      render(<RuleBuilder onSave={onSave} onCancel={vi.fn()} />);

      // Try to save empty form
      await user.click(screen.getByRole('button', { name: /save/i }));

      expect(onSave).not.toHaveBeenCalled();
    });

    it('shows inline validation errors for conditions', async () => {
      const user = userEvent.setup();
      render(<RuleBuilder onSave={vi.fn()} onCancel={vi.fn()} />);

      // Add a condition that requires a value
      await user.click(screen.getByRole('button', { name: /add condition/i }));
      await waitFor(() => {
        expect(screen.getByText(/stream name contains/i)).toBeInTheDocument();
      });
      await user.click(screen.getByText(/stream name contains/i));

      // Don't fill in the value and try to save
      await user.type(screen.getByLabelText(/rule name/i), 'Test Rule');
      await user.click(screen.getByRole('button', { name: /add action/i }));
      await waitFor(() => {
        expect(screen.getByText(/^skip$/i)).toBeInTheDocument();
      });
      await user.click(screen.getByText(/^skip$/i));

      await user.click(screen.getByRole('button', { name: /save/i }));

      await waitFor(() => {
        // Both section-level and inline errors should be shown
        const errors = screen.getAllByText(/value is required/i);
        expect(errors.length).toBeGreaterThanOrEqual(1);
      });
    });
  });

  describe('loading state', () => {
    it('shows loading indicator when isLoading prop is true', () => {
      render(<RuleBuilder onSave={vi.fn()} onCancel={vi.fn()} isLoading={true} />);

      expect(screen.getByTestId('loading-indicator')).toBeInTheDocument();
    });

    it('disables form inputs when loading', () => {
      render(<RuleBuilder onSave={vi.fn()} onCancel={vi.fn()} isLoading={true} />);

      expect(screen.getByLabelText(/rule name/i)).toBeDisabled();
      expect(screen.getByRole('button', { name: /save/i })).toBeDisabled();
    });
  });

  describe('accessibility', () => {
    it('has proper form labels', () => {
      render(<RuleBuilder onSave={vi.fn()} onCancel={vi.fn()} />);

      expect(screen.getByLabelText(/rule name/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/description/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/priority/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/enabled/i)).toBeInTheDocument();
    });

    it('shows validation errors with aria-describedby', async () => {
      const user = userEvent.setup();
      render(<RuleBuilder onSave={vi.fn()} onCancel={vi.fn()} />);

      await user.click(screen.getByRole('button', { name: /save/i }));

      await waitFor(() => {
        const nameInput = screen.getByLabelText(/rule name/i);
        const describedBy = nameInput.getAttribute('aria-describedby');
        expect(describedBy).toBeTruthy();
        const errorElement = document.getElementById(describedBy!);
        expect(errorElement).toHaveTextContent(/required/i);
      });
    });

    it('focuses first error field on validation failure', async () => {
      const user = userEvent.setup();
      render(<RuleBuilder onSave={vi.fn()} onCancel={vi.fn()} />);

      await user.click(screen.getByRole('button', { name: /save/i }));

      await waitFor(() => {
        expect(screen.getByLabelText(/rule name/i)).toHaveFocus();
      });
    });
  });

  describe('template preview', () => {
    it('shows template preview when action has name_template', async () => {
      const ruleWithTemplate: Partial<AutoCreationRule> = {
        name: 'Template Rule',
        conditions: [{ type: 'always' }],
        actions: [{ type: 'create_channel', name_template: '{stream_name}' }],
      };

      render(
        <RuleBuilder
          rule={ruleWithTemplate as AutoCreationRule}
          onSave={vi.fn()}
          onCancel={vi.fn()}
        />
      );

      // Should show template preview section
      expect(screen.getByText(/preview/i)).toBeInTheDocument();
      // Template variable should be in the input field
      expect(screen.getByDisplayValue('{stream_name}')).toBeInTheDocument();
    });
  });

  describe('keyboard navigation', () => {
    it('allows tab navigation through form fields', async () => {
      const user = userEvent.setup();
      render(<RuleBuilder onSave={vi.fn()} onCancel={vi.fn()} />);

      const nameInput = screen.getByLabelText(/rule name/i);
      nameInput.focus();

      await user.tab();
      expect(screen.getByLabelText(/description/i)).toHaveFocus();

      await user.tab();
      expect(screen.getByLabelText(/priority/i)).toHaveFocus();
    });

    it('submits form on Enter in text fields', async () => {
      const user = userEvent.setup();
      const onSave = vi.fn();

      const validRule: Partial<AutoCreationRule> = {
        name: 'Valid',
        conditions: [{ type: 'always' }],
        actions: [{ type: 'skip' }],
      };

      render(
        <RuleBuilder
          rule={validRule as AutoCreationRule}
          onSave={onSave}
          onCancel={vi.fn()}
        />
      );

      await user.type(screen.getByLabelText(/rule name/i), '{Enter}');

      await waitFor(() => {
        expect(onSave).toHaveBeenCalled();
      });
    });
  });
});
