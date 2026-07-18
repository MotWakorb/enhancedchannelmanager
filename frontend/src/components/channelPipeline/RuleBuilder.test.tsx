/**
 * TDD Tests for RuleBuilder component.
 *
 * These tests define the expected behavior of the component BEFORE implementation.
 */
import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from 'vitest';
import { render, screen, waitFor, within, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import {
  server,
  resetMockDataStore,
  mockDataStore,
  createMockChannelGroup,
} from '../../test/mocks/server';
import { RuleBuilder } from './RuleBuilder';
import type { ChannelPipelineRule } from '../../types/channelPipeline';

/**
 * Reveal a wizard step by clicking its pill (bead 09x38.10). The Standard
 * editor is a true step-at-a-time wizard: only the active step's fields are in
 * the accessibility tree, so a test must navigate to a step before interacting
 * with its fields. Step 1 = Logic (conditions/actions), Step 2 = Targeting
 * (merge scope, manual-merge, fold, normalization), Step 3 = Output & Run
 * (sorts, orphan cleanup, run behavior). The rule name/description/enabled live
 * in the always-visible header, reachable from any step.
 */
async function gotoStep(user: ReturnType<typeof userEvent.setup>, n: 1 | 2 | 3) {
  await user.click(screen.getByTestId(`rule-step-${n}`));
}

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
      const existingRule: ChannelPipelineRule = {
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

      // Run behavior lives on Step 3 (Output & Run).
      await gotoStep(user, 3);
      const checkbox = screen.getByLabelText(/run on.*refresh/i);
      await user.click(checkbox);

      expect(checkbox).toBeChecked();
    });

    it('allows toggling stop_on_first_match', async () => {
      const user = userEvent.setup();
      render(<RuleBuilder onSave={vi.fn()} onCancel={vi.fn()} />);

      // Run behavior lives on Step 3 (Output & Run).
      await gotoStep(user, 3);
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

    it('adds a blank condition when clicking add', async () => {
      const user = userEvent.setup();
      render(<RuleBuilder onSave={vi.fn()} onCancel={vi.fn()} />);

      await user.click(screen.getByRole('button', { name: /add condition/i }));

      // Should add a condition card with default Stream Name / Contains and value input
      await waitFor(() => {
        expect(screen.getByPlaceholderText(/enter text/i)).toBeInTheDocument();
      });
    });

    it('allows removing a condition', async () => {
      const user = userEvent.setup();
      const ruleWithCondition: Partial<ChannelPipelineRule> = {
        conditions: [{ type: 'stream_name_contains', value: 'ESPN' }],
        actions: [{ type: 'skip' }],
      };

      render(
        <RuleBuilder
          rule={ruleWithCondition as ChannelPipelineRule}
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

    it('adds a blank action when clicking add', async () => {
      const user = userEvent.setup();
      render(<RuleBuilder onSave={vi.fn()} onCancel={vi.fn()} />);

      await user.click(screen.getByRole('button', { name: /add action/i }));

      // Should add an action card with "Select an action..." placeholder
      await waitFor(() => {
        expect(screen.getByRole('combobox', { name: /action type/i })).toBeInTheDocument();
        expect(screen.getByText(/select an action/i)).toBeInTheDocument();
      });
    });

    it('allows removing an action', async () => {
      const user = userEvent.setup();
      const ruleWithAction: Partial<ChannelPipelineRule> = {
        conditions: [{ type: 'always' }],
        actions: [{ type: 'create_channel', name_template: '{stream_name}' }],
      };

      render(
        <RuleBuilder
          rule={ruleWithAction as ChannelPipelineRule}
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

      // Fill in name and add a condition with a value
      await user.type(screen.getByLabelText(/rule name/i), 'Test Rule');
      await user.click(screen.getByRole('button', { name: /add condition/i }));
      await user.type(screen.getByPlaceholderText(/enter text/i), 'ESPN');

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

      const validRule: Partial<ChannelPipelineRule> = {
        conditions: [{ type: 'always' }],
        actions: [{ type: 'skip' }],
      };

      render(<RuleBuilder rule={validRule as ChannelPipelineRule} onSave={onSave} onCancel={vi.fn()} />);

      // Fill in the form
      await user.clear(screen.getByLabelText(/rule name/i));
      await user.type(screen.getByLabelText(/rule name/i), 'My Rule');
      await user.type(screen.getByLabelText(/description/i), 'Description');

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

      const ruleWithData: Partial<ChannelPipelineRule> = {
        name: 'Valid Rule',
        conditions: [{ type: 'always' }],
        actions: [{ type: 'skip' }],
      };

      render(
        <RuleBuilder
          rule={ruleWithData as ChannelPipelineRule}
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
      const ruleWithoutName: Partial<ChannelPipelineRule> = {
        conditions: [{ type: 'always' }],
        actions: [{ type: 'skip' }],
      };

      render(
        <RuleBuilder
          rule={ruleWithoutName as ChannelPipelineRule}
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

      // Add a condition (defaults to Stream Name Contains with empty value)
      await user.click(screen.getByRole('button', { name: /add condition/i }));

      // Don't fill in the value and try to save
      await user.type(screen.getByLabelText(/rule name/i), 'Test Rule');
      await user.click(screen.getByRole('button', { name: /add action/i }));
      await user.click(screen.getByRole('combobox', { name: /action type/i }));
      await user.click(screen.getByRole('option', { name: /skip/i }));

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
      const ruleWithTemplate: Partial<ChannelPipelineRule> = {
        name: 'Template Rule',
        conditions: [{ type: 'always' }],
        actions: [{ type: 'create_channel', name_template: '{stream_name}' }],
      };

      render(
        <RuleBuilder
          rule={ruleWithTemplate as ChannelPipelineRule}
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

      // Compact header (m1s38.3): Name → Enabled → Description.
      await user.tab();
      expect(screen.getByLabelText(/enabled/i)).toHaveFocus();
      await user.tab();
      expect(screen.getByLabelText(/description/i)).toHaveFocus();
    });

    it('submits form on Enter in text fields', async () => {
      const user = userEvent.setup();
      const onSave = vi.fn();

      const validRule: Partial<ChannelPipelineRule> = {
        name: 'Valid',
        conditions: [{ type: 'always' }],
        actions: [{ type: 'skip' }],
      };

      render(
        <RuleBuilder
          rule={validRule as ChannelPipelineRule}
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

  describe('merge scope group (GH #298)', () => {
    it('shows the Scope group selector when merge scope is on', async () => {
      const user = userEvent.setup();
      mockDataStore.channelGroups.push(
        createMockChannelGroup({ id: 11, name: 'Sports' }),
      );
      // Default rule has match_scope_target_group ON, so the selector shows.
      render(<RuleBuilder onSave={vi.fn()} onCancel={vi.fn()} />);

      // Merge scope lives on Step 2 (Targeting).
      await gotoStep(user, 2);
      await waitFor(() => {
        expect(screen.getByText('Scope group')).toBeVisible();
      });
    });

    it('hides the Scope group selector when merge scope is off', async () => {
      const user = userEvent.setup();
      mockDataStore.channelGroups.push(
        createMockChannelGroup({ id: 11, name: 'Sports' }),
      );
      render(<RuleBuilder onSave={vi.fn()} onCancel={vi.fn()} />);

      await gotoStep(user, 2);
      // Toggle the merge-scope checkbox OFF.
      await user.click(
        screen.getByRole('checkbox', { name: /scope merge lookups to a target group/i }),
      );

      await waitFor(() => {
        expect(screen.queryByText('Scope group')).not.toBeInTheDocument();
      });
    });

    it('includes match_scope_group_id in the save payload', async () => {
      const user = userEvent.setup();
      const onSave = vi.fn();
      mockDataStore.channelGroups.push(
        createMockChannelGroup({ id: 42, name: 'Sports' }),
      );

      const rule: Partial<ChannelPipelineRule> = {
        name: 'Scoped',
        conditions: [{ type: 'always' }],
        actions: [{ type: 'merge_streams' }],
        match_scope_target_group: true,
        match_scope_group_id: 42,
      };
      render(<RuleBuilder rule={rule as ChannelPipelineRule} onSave={onSave} onCancel={vi.fn()} />);

      await user.click(screen.getByRole('button', { name: /save/i }));

      await waitFor(() => {
        expect(onSave).toHaveBeenCalledWith(
          expect.objectContaining({ match_scope_group_id: 42 }),
        );
      });
    });

    it('sends null match_scope_group_id when scope is off', async () => {
      const user = userEvent.setup();
      const onSave = vi.fn();
      mockDataStore.channelGroups.push(
        createMockChannelGroup({ id: 42, name: 'Sports' }),
      );

      const rule: Partial<ChannelPipelineRule> = {
        name: 'Scoped',
        conditions: [{ type: 'always' }],
        actions: [{ type: 'merge_streams' }],
        match_scope_target_group: true,
        match_scope_group_id: 42,
      };
      render(<RuleBuilder rule={rule as ChannelPipelineRule} onSave={onSave} onCancel={vi.fn()} />);

      // Turn scope OFF (Step 2) — the saved scope group must be cleared to null.
      await gotoStep(user, 2);
      await user.click(
        screen.getByRole('checkbox', { name: /scope merge lookups to a target group/i }),
      );
      await user.click(screen.getByRole('button', { name: /save/i }));

      await waitFor(() => {
        expect(onSave).toHaveBeenCalledWith(
          expect.objectContaining({ match_scope_group_id: null }),
        );
      });
    });

    it('warns about the all-groups fallback for a merge-only rule with no scope group', async () => {
      mockDataStore.channelGroups.push(
        createMockChannelGroup({ id: 11, name: 'Sports' }),
      );
      // Merge-Streams-only rule (no create_channel/create_group action),
      // scope ON, no explicit scope group → the all-groups fallback warning.
      const rule: Partial<ChannelPipelineRule> = {
        name: 'MergeOnly',
        conditions: [{ type: 'always' }],
        actions: [{ type: 'merge_streams' }],
        match_scope_target_group: true,
        match_scope_group_id: null,
      };
      const user = userEvent.setup();
      render(<RuleBuilder rule={rule as ChannelPipelineRule} onSave={vi.fn()} onCancel={vi.fn()} />);

      // Merge scope + its fallback warning live on Step 2 (Targeting).
      await gotoStep(user, 2);
      await waitFor(() => {
        expect(screen.getByText(/fall back to ALL channel groups/i)).toBeVisible();
      });
    });

    it('does not warn when a create_channel action provides a target group', async () => {
      const user = userEvent.setup();
      mockDataStore.channelGroups.push(
        createMockChannelGroup({ id: 11, name: 'Sports' }),
      );
      const rule: Partial<ChannelPipelineRule> = {
        name: 'HasCreateChannel',
        conditions: [{ type: 'always' }],
        actions: [{ type: 'create_channel', name_template: '{stream_name}', group_id: 11 }],
        match_scope_target_group: true,
        match_scope_group_id: null,
      };
      render(<RuleBuilder rule={rule as ChannelPipelineRule} onSave={vi.fn()} onCancel={vi.fn()} />);

      // Selector is visible on Step 2, but the all-groups warning must NOT appear.
      await gotoStep(user, 2);
      await waitFor(() => {
        expect(screen.getByText('Scope group')).toBeVisible();
      });
      expect(screen.queryByText(/fall back to ALL channel groups/i)).not.toBeInTheDocument();
    });
  });

  describe('manual channel merge opt-in (allow_manual_channel_merge)', () => {
    it('renders the toggle unchecked by default', async () => {
      const user = userEvent.setup();
      render(<RuleBuilder onSave={vi.fn()} onCancel={vi.fn()} />);

      // Manual-merge protection lives on Step 2 (Targeting).
      await gotoStep(user, 2);
      expect(
        screen.getByRole('checkbox', { name: /allow merging into manual channels/i }),
      ).not.toBeChecked();
    });

    it('round-trips a rule with the flag enabled', async () => {
      const user = userEvent.setup();
      const rule: Partial<ChannelPipelineRule> = {
        name: 'OptIn',
        conditions: [{ type: 'always' }],
        actions: [{ type: 'merge_streams' }],
        allow_manual_channel_merge: true,
      };
      render(<RuleBuilder rule={rule as ChannelPipelineRule} onSave={vi.fn()} onCancel={vi.fn()} />);

      await gotoStep(user, 2);
      expect(
        screen.getByRole('checkbox', { name: /allow merging into manual channels/i }),
      ).toBeChecked();
    });

    it('defaults allow_manual_channel_merge to false in the save payload', async () => {
      const user = userEvent.setup();
      const onSave = vi.fn();
      const rule: Partial<ChannelPipelineRule> = {
        name: 'Protected',
        conditions: [{ type: 'always' }],
        actions: [{ type: 'merge_streams' }],
      };
      render(<RuleBuilder rule={rule as ChannelPipelineRule} onSave={onSave} onCancel={vi.fn()} />);

      await user.click(screen.getByRole('button', { name: /save/i }));

      await waitFor(() => {
        expect(onSave).toHaveBeenCalledWith(
          expect.objectContaining({ allow_manual_channel_merge: false }),
        );
      });
    });

    it('includes allow_manual_channel_merge true after toggling on', async () => {
      const user = userEvent.setup();
      const onSave = vi.fn();
      const rule: Partial<ChannelPipelineRule> = {
        name: 'OptIn',
        conditions: [{ type: 'always' }],
        actions: [{ type: 'merge_streams' }],
      };
      render(<RuleBuilder rule={rule as ChannelPipelineRule} onSave={onSave} onCancel={vi.fn()} />);

      await gotoStep(user, 2);
      await user.click(
        screen.getByRole('checkbox', { name: /allow merging into manual channels/i }),
      );
      await user.click(screen.getByRole('button', { name: /save/i }));

      await waitFor(() => {
        expect(onSave).toHaveBeenCalledWith(
          expect.objectContaining({ allow_manual_channel_merge: true }),
        );
      });
    });
  });

  describe('fold match key opt-in (fold_match_key, GH #645)', () => {
    it('renders the toggle unchecked by default', async () => {
      const user = userEvent.setup();
      render(<RuleBuilder onSave={vi.fn()} onCancel={vi.fn()} />);

      // Spacing/case fold lives on Step 2 (Targeting).
      await gotoStep(user, 2);
      expect(
        screen.getByRole('checkbox', { name: /ignore spacing and case differences/i }),
      ).not.toBeChecked();
    });

    it('round-trips a rule with the flag enabled', async () => {
      const user = userEvent.setup();
      const rule: Partial<ChannelPipelineRule> = {
        name: 'Folded',
        conditions: [{ type: 'always' }],
        actions: [{ type: 'merge_streams' }],
        fold_match_key: true,
      };
      render(<RuleBuilder rule={rule as ChannelPipelineRule} onSave={vi.fn()} onCancel={vi.fn()} />);

      await gotoStep(user, 2);
      expect(
        screen.getByRole('checkbox', { name: /ignore spacing and case differences/i }),
      ).toBeChecked();
    });

    it('defaults fold_match_key to false in the save payload', async () => {
      const user = userEvent.setup();
      const onSave = vi.fn();
      const rule: Partial<ChannelPipelineRule> = {
        name: 'Strict',
        conditions: [{ type: 'always' }],
        actions: [{ type: 'merge_streams' }],
      };
      render(<RuleBuilder rule={rule as ChannelPipelineRule} onSave={onSave} onCancel={vi.fn()} />);

      await user.click(screen.getByRole('button', { name: /save/i }));

      await waitFor(() => {
        expect(onSave).toHaveBeenCalledWith(
          expect.objectContaining({ fold_match_key: false }),
        );
      });
    });

    it('includes fold_match_key true after toggling on', async () => {
      const user = userEvent.setup();
      const onSave = vi.fn();
      const rule: Partial<ChannelPipelineRule> = {
        name: 'Folded',
        conditions: [{ type: 'always' }],
        actions: [{ type: 'merge_streams' }],
      };
      render(<RuleBuilder rule={rule as ChannelPipelineRule} onSave={onSave} onCancel={vi.fn()} />);

      await gotoStep(user, 2);
      await user.click(
        screen.getByRole('checkbox', { name: /ignore spacing and case differences/i }),
      );
      await user.click(screen.getByRole('button', { name: /save/i }));

      await waitFor(() => {
        expect(onSave).toHaveBeenCalledWith(
          expect.objectContaining({ fold_match_key: true }),
        );
      });
    });
  });

  describe('grouped two-pane redesign (bead m1s38.3)', () => {
    const VALID_RULE = {
      name: 'Sports',
      conditions: [{ type: 'stream_name_contains', value: 'ESPN' }],
      actions: [{ type: 'skip' }],
    } as unknown as ChannelPipelineRule;

    /** Render + capture the guarded-close the parent's Escape/× would invoke. */
    async function renderWithClose(
      props: Partial<React.ComponentProps<typeof RuleBuilder>> = {},
    ) {
      let registeredClose: (() => void) | undefined;
      const onCancel = props.onCancel ?? vi.fn();
      render(
        <RuleBuilder
          rule={VALID_RULE}
          onSave={vi.fn()}
          onCancel={onCancel}
          {...props}
          onRegisterClose={fn => {
            if (fn) registeredClose = fn;
          }}
        />,
      );
      await waitFor(() => expect(typeof registeredClose).toBe('function'));
      return { registeredClose: () => registeredClose!(), onCancel };
    }

    // Bead 09x38.10: the Standard editor is now a TRUE step-at-a-time wizard
    // (matching the Event Sync editor), not a scroll-jump form. One step renders
    // at a time; numbered pills + Back/Next drive it; the synthesis rail stays
    // visible across steps.
    describe('true step wizard (bead 09x38.10)', () => {
      function getStepper() {
        return screen.getByRole('navigation', { name: /rule steps/i });
      }

      it('renders the numbered step pills Logic -> Targeting -> Output & Run', () => {
        render(<RuleBuilder rule={VALID_RULE} onSave={vi.fn()} onCancel={vi.fn()} />);

        const pills = within(getStepper()).getAllByRole('button');
        expect(pills.map(p => p.textContent)).toEqual([
          '1 Logic',
          '2 Targeting',
          '3 Output & Run',
        ]);
      });

      it('marks the current step with aria-current="step" and moves it on navigation', async () => {
        const user = userEvent.setup();
        render(<RuleBuilder rule={VALID_RULE} onSave={vi.fn()} onCancel={vi.fn()} />);

        const stepper = getStepper();
        // Step 1 is current on open.
        expect(stepper.querySelector('[aria-current="step"]')?.textContent).toBe('1 Logic');

        await gotoStep(user, 3);
        expect(stepper.querySelector('[aria-current="step"]')?.textContent).toBe(
          '3 Output & Run',
        );
      });

      it('renders only the active step (step-at-a-time gating)', async () => {
        const user = userEvent.setup();
        render(<RuleBuilder rule={VALID_RULE} onSave={vi.fn()} onCancel={vi.fn()} />);

        // On Step 1, Logic content is visible; Targeting/Output content is in
        // the DOM (sections use the `hidden` attribute) but not visible, so
        // getByRole (which drops hidden elements) cannot see it and the Output
        // label is present-but-hidden.
        expect(screen.getByRole('heading', { name: /conditions/i })).toBeVisible();
        expect(
          screen.queryByRole('checkbox', { name: /allow merging into manual channels/i }),
        ).toBeNull();
        expect(screen.getByText(/orphan cleanup/i)).not.toBeVisible();

        // Step 2 reveals Targeting, hides Logic.
        await gotoStep(user, 2);
        expect(
          screen.getByRole('checkbox', { name: /allow merging into manual channels/i }),
        ).toBeVisible();
        expect(screen.queryByRole('heading', { name: /conditions/i })).toBeNull();

        // Step 3 reveals Output & Run.
        await gotoStep(user, 3);
        expect(screen.getByText(/orphan cleanup/i)).toBeVisible();
      });

      // Bead 09x38.15 item 6: Orphan Cleanup defaults to "Delete orphaned
      // channels" with no visible warning — unlike the adjacent merge-scope
      // control, which already warns inline (.norm-hint) when its default
      // resolves to a risky fallback. Add the same idiom here.
      it('warns inline when Orphan Cleanup is on its destructive default (delete)', async () => {
        const user = userEvent.setup();
        render(<RuleBuilder rule={VALID_RULE} onSave={vi.fn()} onCancel={vi.fn()} />);

        await gotoStep(user, 3);

        const orphanField = screen.getByText('Orphan Cleanup').closest('.form-field') as HTMLElement;
        expect(within(orphanField).getByText(/will be deleted/i)).toBeVisible();
        expect(within(orphanField).getByText('warning')).toBeInTheDocument();
      });

      it('hides the orphan-delete warning when set to a non-destructive action', async () => {
        const user = userEvent.setup();
        render(<RuleBuilder rule={VALID_RULE} onSave={vi.fn()} onCancel={vi.fn()} />);

        await gotoStep(user, 3);
        const orphanField = screen.getByText('Orphan Cleanup').closest('.form-field') as HTMLElement;
        await user.click(within(orphanField).getByRole('button', { name: /delete orphaned channels/i }));
        await user.click(await screen.findByRole('option', { name: /move to uncategorized/i }));

        expect(within(orphanField).queryByText(/will be deleted/i)).not.toBeInTheDocument();
      });

      it('adaptive footer: no Back on step 1, Back+Next on middle steps, Save (no Next) on the last step', async () => {
        const user = userEvent.setup();
        render(<RuleBuilder rule={VALID_RULE} onSave={vi.fn()} onCancel={vi.fn()} />);

        // Step 1: no Back; Next present; Save available (exposed on every step).
        expect(screen.queryByTestId('rule-wizard-back')).toBeNull();
        expect(screen.getByTestId('rule-wizard-next')).toBeInTheDocument();
        expect(screen.getByRole('button', { name: /^save$/i })).toBeInTheDocument();

        // Step 2: Back + Next both present.
        await gotoStep(user, 2);
        expect(screen.getByTestId('rule-wizard-back')).toBeInTheDocument();
        expect(screen.getByTestId('rule-wizard-next')).toBeInTheDocument();

        // Step 3 (last): Back present, Next gone, Save present.
        await gotoStep(user, 3);
        expect(screen.getByTestId('rule-wizard-back')).toBeInTheDocument();
        expect(screen.queryByTestId('rule-wizard-next')).toBeNull();
        expect(screen.getByRole('button', { name: /^save$/i })).toBeInTheDocument();
      });

      it('Back and Next walk the steps', async () => {
        const user = userEvent.setup();
        render(<RuleBuilder rule={VALID_RULE} onSave={vi.fn()} onCancel={vi.fn()} />);

        await user.click(screen.getByTestId('rule-wizard-next')); // -> step 2
        expect(getStepper().querySelector('[aria-current="step"]')?.textContent).toBe(
          '2 Targeting',
        );
        await user.click(screen.getByTestId('rule-wizard-back')); // -> step 1
        expect(getStepper().querySelector('[aria-current="step"]')?.textContent).toBe(
          '1 Logic',
        );
      });

      it('the synthesis rail stays visible on every step', async () => {
        const user = userEvent.setup();
        render(<RuleBuilder rule={VALID_RULE} onSave={vi.fn()} onCancel={vi.fn()} />);

        expect(screen.getByTestId('rule-intent-card')).toBeVisible();
        await gotoStep(user, 2);
        expect(screen.getByTestId('rule-intent-card')).toBeVisible();
        await gotoStep(user, 3);
        expect(screen.getByTestId('rule-intent-card')).toBeVisible();
      });

      it('pressing Save from a later step with a Logic error routes back to Step 1 and shows it', async () => {
        const user = userEvent.setup();
        // A rule with a name but NO conditions — invalid; the error lives in Logic.
        const noConditions = {
          name: 'Named',
          conditions: [],
          actions: [{ type: 'skip' }],
        } as unknown as ChannelPipelineRule;
        const onSave = vi.fn();
        render(<RuleBuilder rule={noConditions} onSave={onSave} onCancel={vi.fn()} />);

        // Go to the last step and Save from there.
        await gotoStep(user, 3);
        await user.click(screen.getByRole('button', { name: /^save$/i }));

        // Routed back to Step 1 with the conditions error visible; nothing saved.
        expect(getStepper().querySelector('[aria-current="step"]')?.textContent).toBe(
          '1 Logic',
        );
        expect(screen.getByText(/at least one condition/i)).toBeVisible();
        expect(onSave).not.toHaveBeenCalled();
      });
    });

    // Bead 09x38.10 round-trip invariant: open a fully-configured existing rule,
    // walk EVERY step, then save — the payload must be identical to the loaded
    // config. The wizard changes only which step reveals a field, never what
    // Save persists.
    describe('round-trip invariant across the wizard — bead 09x38.10', () => {
      const FULL_RULE = {
        name: 'Full Config',
        description: 'Every field set',
        enabled: true,
        priority: 0,
        conditions: [{ type: 'stream_name_contains', value: 'ESPN', connector: 'and' }],
        actions: [{ type: 'skip' }],
        run_on_refresh: true,
        stop_on_first_match: false,
        sort_field: 'stream_name',
        sort_order: 'desc',
        probe_on_sort: false,
        sort_regex: '',
        stream_sort_field: 'quality',
        stream_sort_order: 'desc',
        quality_tie_break_order: 'desc',
        quality_m3u_tie_break_enabled: true,
        normalization_group_ids: [],
        skip_struck_streams: true,
        orphan_action: 'move_uncategorized',
        match_scope_target_group: true,
        match_scope_group_id: 7,
        allow_manual_channel_merge: true,
        fold_match_key: true,
      } as unknown as ChannelPipelineRule;

      it('open -> step through every step -> save yields an unchanged payload', async () => {
        const user = userEvent.setup();
        const onSave = vi.fn().mockResolvedValue(undefined);
        mockDataStore.channelGroups.push(createMockChannelGroup({ id: 7, name: 'Sports' }));

        render(<RuleBuilder rule={FULL_RULE} onSave={onSave} onCancel={vi.fn()} />);

        // Walk Logic -> Targeting -> Output & Run via Next, touching nothing.
        await user.click(screen.getByTestId('rule-wizard-next'));
        await user.click(screen.getByTestId('rule-wizard-next'));
        // On the last step, Save is the primary footer action.
        await user.click(screen.getByRole('button', { name: /^save$/i }));

        await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
        expect(onSave).toHaveBeenCalledWith({
          name: 'Full Config',
          description: 'Every field set',
          enabled: true,
          priority: 0,
          conditions: [{ type: 'stream_name_contains', value: 'ESPN', connector: 'and' }],
          actions: [{ type: 'skip' }],
          run_on_refresh: true,
          stop_on_first_match: false,
          sort_field: 'stream_name',
          sort_order: 'desc',
          probe_on_sort: false,
          sort_regex: '',
          stream_sort_field: 'quality',
          stream_sort_order: 'desc',
          quality_tie_break_order: 'desc',
          quality_m3u_tie_break_enabled: true,
          normalization_group_ids: [],
          skip_struck_streams: true,
          orphan_action: 'move_uncategorized',
          match_scope_target_group: true,
          match_scope_group_id: 7,
          allow_manual_channel_merge: true,
          fold_match_key: true,
        });
      });
    });

    describe('full-field dirty guard (data-loss fix)', () => {
      it('no change → the registered close closes at once with NO confirm', async () => {
        const { registeredClose, onCancel } = await renderWithClose();

        await act(async () => registeredClose());

        expect(onCancel).toHaveBeenCalledTimes(1);
        expect(screen.queryByTestId('rule-discard-dialog')).toBeNull();
      });

      it('a shallow name edit still guards the close', async () => {
        const user = userEvent.setup();
        const { registeredClose, onCancel } = await renderWithClose();

        await user.type(screen.getByLabelText(/rule name/i), '!');
        await act(async () => registeredClose());

        expect(screen.getByTestId('rule-discard-dialog')).toBeInTheDocument();
        expect(onCancel).not.toHaveBeenCalled();
      });

      it('a DEEP edit to an existing condition value (count unchanged) marks dirty', async () => {
        const user = userEvent.setup();
        const { registeredClose, onCancel } = await renderWithClose();

        // Edit the existing condition's value — the count does not change.
        await user.type(screen.getByDisplayValue('ESPN'), 'x');
        await act(async () => registeredClose());

        expect(screen.getByTestId('rule-discard-dialog')).toBeInTheDocument();
        expect(onCancel).not.toHaveBeenCalled();
      });

      it('a DEEP edit to an existing action marks dirty', async () => {
        const user = userEvent.setup();
        const ruleWithTemplate = {
          name: 'R',
          conditions: [{ type: 'always' }],
          actions: [{ type: 'create_channel', name_template: '{stream_name}' }],
        } as unknown as ChannelPipelineRule;
        const { registeredClose, onCancel } = await renderWithClose({ rule: ruleWithTemplate });

        await user.type(await screen.findByDisplayValue('{stream_name}'), 'X');
        await act(async () => registeredClose());

        expect(screen.getByTestId('rule-discard-dialog')).toBeInTheDocument();
        expect(onCancel).not.toHaveBeenCalled();
      });

      it('adding or removing a condition marks dirty', async () => {
        const user = userEvent.setup();
        const { registeredClose, onCancel } = await renderWithClose();

        await user.click(screen.getByRole('button', { name: /add condition/i }));
        await act(async () => registeredClose());

        expect(screen.getByTestId('rule-discard-dialog')).toBeInTheDocument();
        expect(onCancel).not.toHaveBeenCalled();
      });

      it('Escape/× routes through the guard: onCancel fires only after Confirm; "Keep editing" dismisses', async () => {
        const user = userEvent.setup();
        const { registeredClose, onCancel } = await renderWithClose();

        await user.type(screen.getByLabelText(/rule name/i), '!');
        await act(async () => registeredClose());

        // The confirm is shown; nothing discarded yet.
        expect(screen.getByTestId('rule-discard-dialog')).toBeInTheDocument();
        expect(onCancel).not.toHaveBeenCalled();

        // "Keep editing" dismisses without discarding.
        await user.click(screen.getByTestId('rule-discard-keep'));
        expect(screen.queryByTestId('rule-discard-dialog')).toBeNull();
        expect(onCancel).not.toHaveBeenCalled();

        // Re-open and confirm — only now is the discard committed.
        await act(async () => registeredClose());
        await user.click(screen.getByTestId('rule-discard-confirm'));
        expect(onCancel).toHaveBeenCalledTimes(1);
      });

      it('the discard confirm is an alertdialog', async () => {
        const user = userEvent.setup();
        const { registeredClose } = await renderWithClose();

        await user.type(screen.getByLabelText(/rule name/i), '!');
        await act(async () => registeredClose());

        expect(screen.getByRole('alertdialog')).toBeInTheDocument();
      });

      it('a successful save clears the dirty state (no confirm on the next close)', async () => {
        const user = userEvent.setup();
        const onSave = vi.fn().mockResolvedValue(undefined);
        const onCancel = vi.fn();
        let registeredClose: (() => void) | undefined;
        render(
          <RuleBuilder
            rule={VALID_RULE}
            onSave={onSave}
            onCancel={onCancel}
            onRegisterClose={fn => {
              if (fn) registeredClose = fn;
            }}
          />,
        );
        await waitFor(() => expect(typeof registeredClose).toBe('function'));

        // Edit → dirty, then Save.
        await user.type(screen.getByLabelText(/rule name/i), '!');
        await user.click(screen.getByRole('button', { name: /save/i }));
        await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));

        // The saved config is now the clean baseline → closing does not prompt.
        await act(async () => registeredClose!());
        expect(screen.queryByTestId('rule-discard-dialog')).toBeNull();
        expect(onCancel).toHaveBeenCalledTimes(1);
      });
    });

    describe('intent sentence', () => {
      it('renders destructive/risky clauses in bold', () => {
        const riskyRule = {
          name: 'Risky',
          conditions: [{ type: 'always' }],
          actions: [{ type: 'merge_streams' }],
          orphan_action: 'delete',
          allow_manual_channel_merge: true,
          stop_on_first_match: true,
        } as unknown as ChannelPipelineRule;
        render(<RuleBuilder rule={riskyRule} onSave={vi.fn()} onCancel={vi.fn()} />);

        const intent = screen.getByTestId('rule-intent');
        expect(within(intent).getByText(/Orphaned channels are deleted/i).tagName).toBe('STRONG');
        expect(
          within(intent).getByText(/May merge streams into manual channels/i).tagName,
        ).toBe('STRONG');
        expect(
          within(intent).getByText(/Stops after the first matching rule/i).tagName,
        ).toBe('STRONG');
      });
    });

    describe('analyzer advisory chips', () => {
      it('renders findings from the endpoint and NEVER disables Save', async () => {
        server.use(
          http.post('/api/channel-pipeline/rules/analyze-body', () =>
            HttpResponse.json({
              rules: [
                {
                  rule_id: null,
                  rule_name: 'Sports',
                  findings: [
                    {
                      code: 'REGEX_TRIVIALLY_MATCHES_ALL',
                      severity: 'warning',
                      field: 'conditions',
                      message: 'This pattern matches every stream.',
                      suggestion: 'Tighten the pattern.',
                      detail: {},
                    },
                  ],
                },
              ],
              summary: { error: 0, warning: 1, info: 0 },
            }),
          ),
        );
        render(<RuleBuilder rule={VALID_RULE} onSave={vi.fn()} onCancel={vi.fn()} />);

        // Chip appears (after the debounce) with the advisory message.
        const chip = await screen.findByTestId('rule-analyzer-chip', {}, { timeout: 3000 });
        expect(chip).toHaveTextContent(/matches every stream/i);

        // Advisory only: Save is never gated by a finding.
        expect(screen.getByRole('button', { name: /save/i })).not.toBeDisabled();
      });
    });

    describe('sort/numbering hint (GH #644)', () => {
      it('shows the hint when Channel Sort is set and Channel Number is unset (auto)', () => {
        const rule: Partial<ChannelPipelineRule> = {
          name: 'PL Channels',
          conditions: [{ type: 'always' }],
          actions: [{ type: 'create_channel', name_template: '{stream_name}', group_id: 1 }],
          sort_field: 'stream_name',
        };
        render(<RuleBuilder rule={rule as ChannelPipelineRule} onSave={vi.fn()} onCancel={vi.fn()} />);

        expect(screen.getByTestId('sort-numbering-hint')).toHaveTextContent(
          /won't renumber channels/i,
        );
      });

      it('shows the hint when Channel Number is the literal "auto" string', () => {
        const rule: Partial<ChannelPipelineRule> = {
          name: 'PL Channels',
          conditions: [{ type: 'always' }],
          actions: [
            { type: 'create_channel', name_template: '{stream_name}', group_id: 1, channel_number: 'auto' },
          ],
          sort_field: 'stream_name',
        };
        render(<RuleBuilder rule={rule as ChannelPipelineRule} onSave={vi.fn()} onCancel={vi.fn()} />);

        expect(screen.getByTestId('sort-numbering-hint')).toBeInTheDocument();
      });

      it('does not show the hint when Channel Number has a fixed starting range', () => {
        const rule: Partial<ChannelPipelineRule> = {
          name: 'US Channels',
          conditions: [{ type: 'always' }],
          actions: [
            { type: 'create_channel', name_template: '{stream_name}', group_id: 2, channel_number: '400-99999' },
          ],
          sort_field: 'stream_name',
        };
        render(<RuleBuilder rule={rule as ChannelPipelineRule} onSave={vi.fn()} onCancel={vi.fn()} />);

        expect(screen.queryByTestId('sort-numbering-hint')).not.toBeInTheDocument();
      });

      it('does not show the hint when no Channel Sort is selected', () => {
        const rule: Partial<ChannelPipelineRule> = {
          name: 'PL Channels',
          conditions: [{ type: 'always' }],
          actions: [{ type: 'create_channel', name_template: '{stream_name}', group_id: 1 }],
          sort_field: '',
        };
        render(<RuleBuilder rule={rule as ChannelPipelineRule} onSave={vi.fn()} onCancel={vi.fn()} />);

        expect(screen.queryByTestId('sort-numbering-hint')).not.toBeInTheDocument();
      });

      it('does not show the hint when the rule has no create_channel action', () => {
        const rule: Partial<ChannelPipelineRule> = {
          name: 'Merge Only',
          conditions: [{ type: 'always' }],
          actions: [{ type: 'merge_streams' }],
          sort_field: 'stream_name',
        };
        render(<RuleBuilder rule={rule as ChannelPipelineRule} onSave={vi.fn()} onCancel={vi.fn()} />);

        expect(screen.queryByTestId('sort-numbering-hint')).not.toBeInTheDocument();
      });

      // Multi-create_channel semantics: the backend's _get_rule_starting_number
      // returns on the FIRST create_channel action — later ones are never
      // consulted — so ONLY the first action decides whether the renumber
      // pass runs, and the hint must agree in both directions.
      it('does not show the hint when the FIRST create_channel has a fixed range, even if a later one is auto', () => {
        const rule: Partial<ChannelPipelineRule> = {
          name: 'Fixed First',
          conditions: [{ type: 'always' }],
          actions: [
            { type: 'create_channel', name_template: '{stream_name}', group_id: 1, channel_number: '100-99999' },
            { type: 'create_channel', name_template: '{stream_name} B', group_id: 2 },
          ],
          sort_field: 'stream_name',
        };
        render(<RuleBuilder rule={rule as ChannelPipelineRule} onSave={vi.fn()} onCancel={vi.fn()} />);

        expect(screen.queryByTestId('sort-numbering-hint')).not.toBeInTheDocument();
      });

      it('shows the hint when the FIRST create_channel is auto, even if a later one has a fixed range', () => {
        const rule: Partial<ChannelPipelineRule> = {
          name: 'Auto First',
          conditions: [{ type: 'always' }],
          actions: [
            { type: 'create_channel', name_template: '{stream_name}', group_id: 1 },
            { type: 'create_channel', name_template: '{stream_name} B', group_id: 2, channel_number: '400-99999' },
          ],
          sort_field: 'stream_name',
        };
        render(<RuleBuilder rule={rule as ChannelPipelineRule} onSave={vi.fn()} onCancel={vi.fn()} />);

        expect(screen.getByTestId('sort-numbering-hint')).toBeInTheDocument();
      });
    });
  });
});
