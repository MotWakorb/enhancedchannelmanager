/**
 * TDD Tests for ConditionEditor component.
 *
 * These tests define the expected behavior of the component BEFORE implementation.
 */
import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {
  server,
  resetMockDataStore,
} from '../../test/mocks/server';
import { ConditionEditor } from './ConditionEditor';
import type { Condition, ConditionType } from '../../types/autoCreation';

// Setup MSW server
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  resetMockDataStore();
});
afterAll(() => server.close());

describe('ConditionEditor', () => {
  describe('rendering', () => {
    it('renders condition type selector', () => {
      render(
        <ConditionEditor
          condition={{ type: 'stream_name_contains' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      expect(screen.getByRole('combobox')).toBeInTheDocument();
    });

    it('renders remove button', () => {
      render(
        <ConditionEditor
          condition={{ type: 'stream_name_contains' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      expect(screen.getByRole('button', { name: /remove/i })).toBeInTheDocument();
    });

    it('displays condition type label', () => {
      render(
        <ConditionEditor
          condition={{ type: 'stream_name_contains' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      expect(screen.getByText(/stream name contains/i)).toBeInTheDocument();
    });
  });

  describe('condition types', () => {
    it('renders text input for string value conditions', () => {
      render(
        <ConditionEditor
          condition={{ type: 'stream_name_contains', value: 'ESPN' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      const input = screen.getByRole('textbox');
      expect(input).toHaveValue('ESPN');
    });

    it('renders number input for numeric conditions', () => {
      render(
        <ConditionEditor
          condition={{ type: 'quality_min', value: 720 }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      const input = screen.getByRole('spinbutton');
      expect(input).toHaveValue(720);
    });

    it('renders regex input with validation for pattern conditions', () => {
      const { container } = render(
        <ConditionEditor
          condition={{ type: 'stream_name_matches', value: '.*ESPN.*' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      const input = screen.getByRole('textbox');
      expect(input).toHaveValue('.*ESPN.*');
      // Check for the regex hint (separate from the type label that also contains "Regex")
      expect(container.querySelector('.condition-hint')).toHaveTextContent('Regex');
    });

    it('renders boolean toggle for boolean conditions', () => {
      render(
        <ConditionEditor
          condition={{ type: 'has_channel', value: true }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      const toggle = screen.getByRole('checkbox');
      expect(toggle).toBeChecked();
    });

    it('renders no value input for valueless conditions', () => {
      render(
        <ConditionEditor
          condition={{ type: 'always' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      // Should not have any input field
      expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
      expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument();
    });
  });

  describe('logical operators', () => {
    it('renders nested conditions for AND condition', () => {
      render(
        <ConditionEditor
          condition={{
            type: 'and',
            conditions: [
              { type: 'stream_name_contains', value: 'ESPN' },
              { type: 'quality_min', value: 720 },
            ],
          }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      // The main type selector shows "AND (All must match)"
      const comboboxes = screen.getAllByRole('combobox');
      expect(comboboxes[0]).toHaveTextContent(/AND/i);
      // Nested conditions render with their values
      expect(screen.getByDisplayValue('ESPN')).toBeInTheDocument();
      expect(screen.getByDisplayValue('720')).toBeInTheDocument();
    });

    it('renders nested conditions for OR condition', () => {
      render(
        <ConditionEditor
          condition={{
            type: 'or',
            conditions: [
              { type: 'stream_name_contains', value: 'ESPN' },
              { type: 'stream_name_contains', value: 'FOX' },
            ],
          }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      // The main type selector shows "OR (Any must match)"
      const comboboxes = screen.getAllByRole('combobox');
      expect(comboboxes[0]).toHaveTextContent(/OR/i);
    });

    it('renders NOT wrapper for negated condition', () => {
      render(
        <ConditionEditor
          condition={{
            type: 'not',
            conditions: [{ type: 'stream_name_contains', value: 'ESPN' }],
          }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      expect(screen.getByText(/not/i)).toBeInTheDocument();
    });

    it('allows adding nested conditions to logical operators', async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();

      render(
        <ConditionEditor
          condition={{
            type: 'and',
            conditions: [{ type: 'stream_name_contains', value: 'ESPN' }],
          }}
          onChange={onChange}
          onRemove={vi.fn()}
        />
      );

      const addButton = screen.getByRole('button', { name: /add nested/i });
      await user.click(addButton);

      // Should call onChange with updated conditions array
      expect(onChange).toHaveBeenCalled();
    });
  });

  describe('onChange handling', () => {
    it('calls onChange when type is changed', async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();

      render(
        <ConditionEditor
          condition={{ type: 'stream_name_contains', value: 'test' }}
          onChange={onChange}
          onRemove={vi.fn()}
        />
      );

      const typeSelect = screen.getByRole('combobox');
      await user.click(typeSelect);
      await user.click(screen.getByText(/stream name matches/i));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ type: 'stream_name_matches' })
      );
    });

    it('calls onChange when value is updated', async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();

      render(
        <ConditionEditor
          condition={{ type: 'stream_name_contains', value: '' }}
          onChange={onChange}
          onRemove={vi.fn()}
        />
      );

      const input = screen.getByRole('textbox');
      await user.type(input, 'N');

      // onChange is called with the typed value
      await waitFor(() => {
        expect(onChange).toHaveBeenCalledWith(
          expect.objectContaining({ value: 'N' })
        );
      });
    });

    it('calls onChange when negate is toggled', async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();

      render(
        <ConditionEditor
          condition={{ type: 'stream_name_contains', value: 'ESPN', negate: false }}
          onChange={onChange}
          onRemove={vi.fn()}
          showNegateOption={true}
        />
      );

      const negateCheckbox = screen.getByLabelText(/negate/i);
      await user.click(negateCheckbox);

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ negate: true })
      );
    });

    it('calls onChange when case_sensitive is toggled', async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();

      render(
        <ConditionEditor
          condition={{ type: 'stream_name_contains', value: 'ESPN', case_sensitive: false }}
          onChange={onChange}
          onRemove={vi.fn()}
          showCaseSensitiveOption={true}
        />
      );

      const checkbox = screen.getByLabelText(/case sensitive/i);
      await user.click(checkbox);

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ case_sensitive: true })
      );
    });
  });

  describe('onRemove handling', () => {
    it('calls onRemove when remove button is clicked', async () => {
      const user = userEvent.setup();
      const onRemove = vi.fn();

      render(
        <ConditionEditor
          condition={{ type: 'stream_name_contains' }}
          onChange={vi.fn()}
          onRemove={onRemove}
        />
      );

      await user.click(screen.getByRole('button', { name: /remove/i }));

      expect(onRemove).toHaveBeenCalled();
    });

    it('can be disabled from removing', () => {
      render(
        <ConditionEditor
          condition={{ type: 'stream_name_contains' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
          canRemove={false}
        />
      );

      expect(screen.queryByRole('button', { name: /remove/i })).not.toBeInTheDocument();
    });
  });

  describe('validation', () => {
    it('shows error for empty required value', async () => {
      const user = userEvent.setup();
      render(
        <ConditionEditor
          condition={{ type: 'stream_name_contains', value: '' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
          showValidation={true}
        />
      );

      expect(screen.getByText(/value is required/i)).toBeInTheDocument();
    });

    it('shows error for invalid regex pattern', () => {
      render(
        <ConditionEditor
          condition={{ type: 'stream_name_matches', value: '[invalid(' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
          showValidation={true}
        />
      );

      expect(screen.getByText(/invalid regex/i)).toBeInTheDocument();
    });

    it('shows error for negative quality value', () => {
      render(
        <ConditionEditor
          condition={{ type: 'quality_min', value: -100 }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
          showValidation={true}
        />
      );

      // Use getByRole to find the error alert
      const errorAlert = screen.getByRole('alert');
      expect(errorAlert.textContent?.toLowerCase()).toContain('must be');
      expect(errorAlert.textContent?.toLowerCase()).toContain('positive');
    });

    it('shows error for empty nested conditions in logical operators', () => {
      render(
        <ConditionEditor
          condition={{ type: 'and', conditions: [] }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
          showValidation={true}
        />
      );

      expect(screen.getByText(/at least one nested condition/i)).toBeInTheDocument();
    });
  });

  describe('condition options', () => {
    it('shows negate option when supported', () => {
      render(
        <ConditionEditor
          condition={{ type: 'stream_name_contains', value: 'test' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
          showNegateOption={true}
        />
      );

      expect(screen.getByLabelText(/negate/i)).toBeInTheDocument();
    });

    it('hides negate option by default', () => {
      render(
        <ConditionEditor
          condition={{ type: 'stream_name_contains', value: 'test' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      expect(screen.queryByLabelText(/negate/i)).not.toBeInTheDocument();
    });

    it('shows case sensitive option for string conditions', () => {
      render(
        <ConditionEditor
          condition={{ type: 'stream_name_contains', value: 'test' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
          showCaseSensitiveOption={true}
        />
      );

      expect(screen.getByLabelText(/case sensitive/i)).toBeInTheDocument();
    });

    it('hides case sensitive option for non-string conditions', () => {
      render(
        <ConditionEditor
          condition={{ type: 'quality_min', value: 720 }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
          showCaseSensitiveOption={true}
        />
      );

      // Should not show case sensitive for numeric conditions
      expect(screen.queryByLabelText(/case sensitive/i)).not.toBeInTheDocument();
    });
  });

  describe('readonly mode', () => {
    it('disables all inputs when readonly', () => {
      render(
        <ConditionEditor
          condition={{ type: 'stream_name_contains', value: 'ESPN' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
          readonly={true}
        />
      );

      expect(screen.getByRole('textbox')).toBeDisabled();
      expect(screen.getByRole('combobox')).toBeDisabled();
    });

    it('hides remove button when readonly', () => {
      render(
        <ConditionEditor
          condition={{ type: 'stream_name_contains', value: 'ESPN' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
          readonly={true}
        />
      );

      expect(screen.queryByRole('button', { name: /remove/i })).not.toBeInTheDocument();
    });
  });

  describe('condition type categories', () => {
    it('groups condition types by category in selector', async () => {
      const user = userEvent.setup();
      render(
        <ConditionEditor
          condition={{ type: 'stream_name_contains' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      await user.click(screen.getByRole('combobox'));

      // Should show category headers
      expect(screen.getByText(/stream conditions/i)).toBeInTheDocument();
      expect(screen.getByText(/channel conditions/i)).toBeInTheDocument();
      expect(screen.getByText(/logical operators/i)).toBeInTheDocument();
    });
  });

  describe('accessibility', () => {
    it('has accessible labels for inputs', () => {
      render(
        <ConditionEditor
          condition={{ type: 'stream_name_contains', value: '' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      expect(screen.getByLabelText(/condition type/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/value/i)).toBeInTheDocument();
    });

    it('shows validation errors with aria-describedby', () => {
      render(
        <ConditionEditor
          condition={{ type: 'stream_name_contains', value: '' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
          showValidation={true}
        />
      );

      const input = screen.getByRole('textbox');
      const describedBy = input.getAttribute('aria-describedby');
      expect(describedBy).toBeTruthy();
    });

    it('marks required fields with aria-required', () => {
      render(
        <ConditionEditor
          condition={{ type: 'stream_name_contains', value: '' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      const input = screen.getByRole('textbox');
      expect(input).toHaveAttribute('aria-required', 'true');
    });
  });

  describe('drag and drop', () => {
    it('shows drag handle when draggable', () => {
      render(
        <ConditionEditor
          condition={{ type: 'stream_name_contains', value: 'ESPN' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
          draggable={true}
        />
      );

      expect(screen.getByTestId('drag-handle')).toBeInTheDocument();
    });

    it('hides drag handle when not draggable', () => {
      render(
        <ConditionEditor
          condition={{ type: 'stream_name_contains', value: 'ESPN' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
          draggable={false}
        />
      );

      expect(screen.queryByTestId('drag-handle')).not.toBeInTheDocument();
    });
  });

  describe('compact mode', () => {
    it('renders in compact layout when specified', () => {
      const { container } = render(
        <ConditionEditor
          condition={{ type: 'stream_name_contains', value: 'ESPN' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
          compact={true}
        />
      );

      expect(container.firstChild).toHaveClass('compact');
    });
  });
});
