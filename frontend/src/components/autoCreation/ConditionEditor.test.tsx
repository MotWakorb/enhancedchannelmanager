/**
 * Tests for ConditionEditor component.
 *
 * The editor uses a three-part layout: Field dropdown (CustomSelect),
 * Operator dropdown (CustomSelect), Value input.
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
    it('renders field and operator dropdowns', () => {
      const { container } = render(
        <ConditionEditor
          condition={{ type: 'stream_name_contains' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      // Should have two CustomSelect elements (field + operator)
      const selects = container.querySelectorAll('.custom-select');
      expect(selects.length).toBeGreaterThanOrEqual(2);
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

    it('displays field and operator labels separately', () => {
      render(
        <ConditionEditor
          condition={{ type: 'stream_name_contains' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      // Field shows "Stream Name", Operator shows "Contains"
      expect(screen.getByText(/stream name/i)).toBeInTheDocument();
      expect(screen.getByText(/^Contains$/)).toBeInTheDocument();
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

    it('renders dropdown for quality conditions', () => {
      const { container } = render(
        <ConditionEditor
          condition={{ type: 'quality_min', value: 720 }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      // Quality now uses a CustomSelect dropdown with preset options
      const selects = container.querySelectorAll('.custom-select');
      expect(selects.length).toBe(3); // field + operator + value
      // Should display the selected quality label
      expect(screen.getByText(/HD.*720p/)).toBeInTheDocument();
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
      // Check for the regex hint
      expect(container.querySelector('.condition-hint')).toHaveTextContent('Regex');
    });

    it('renders no value input for existence conditions', () => {
      render(
        <ConditionEditor
          condition={{ type: 'has_channel', value: true }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      // has_channel maps to field "Channel" with operator "Exists" - no value input
      expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
      expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument();
    });

    it('renders no value input for valueless conditions', () => {
      render(
        <ConditionEditor
          condition={{ type: 'always' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
      expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument();
    });
  });

  describe('onChange handling', () => {
    it('calls onChange when operator is changed', async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();

      render(
        <ConditionEditor
          condition={{ type: 'stream_name_contains', value: 'test' }}
          onChange={onChange}
          onRemove={vi.fn()}
        />
      );

      // Click the operator dropdown trigger (contains text "Contains")
      const operatorTrigger = screen.getByText(/^Contains$/).closest('button')!;
      await user.click(operatorTrigger);
      // Select "Matches (Regex)" from the dropdown
      await user.click(screen.getByText(/Matches.*Regex/i));

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

      await waitFor(() => {
        expect(onChange).toHaveBeenCalledWith(
          expect.objectContaining({ value: 'N' })
        );
      });
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
    it('shows error for empty required value', () => {
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

    it('shows quality dropdown with preset options', async () => {
      const user = userEvent.setup();
      const { container } = render(
        <ConditionEditor
          condition={{ type: 'quality_min', value: 1080 }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
          showValidation={true}
        />
      );

      // The value dropdown should show FHD / 1080p
      expect(screen.getByText(/FHD.*1080p/)).toBeInTheDocument();
      // Open the value dropdown to verify options
      const valueSelect = container.querySelectorAll('.custom-select')[2];
      const trigger = valueSelect?.querySelector('button');
      if (trigger) {
        await user.click(trigger);
        expect(screen.getByText(/UHD.*4K/)).toBeInTheDocument();
        expect(screen.getByText(/SD.*480p/)).toBeInTheDocument();
      }
    });
  });

  describe('condition options', () => {
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

      expect(screen.queryByLabelText(/case sensitive/i)).not.toBeInTheDocument();
    });
  });

  describe('readonly mode', () => {
    it('disables all inputs when readonly', () => {
      const { container } = render(
        <ConditionEditor
          condition={{ type: 'stream_name_contains', value: 'ESPN' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
          readonly={true}
        />
      );

      expect(screen.getByRole('textbox')).toBeDisabled();
      // CustomSelect triggers should be disabled
      const selectTriggers = container.querySelectorAll('.custom-select-trigger');
      selectTriggers.forEach(trigger => expect(trigger).toBeDisabled());
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

  describe('accessibility', () => {
    it('has accessible label for value input', () => {
      render(
        <ConditionEditor
          condition={{ type: 'stream_name_contains', value: '' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

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

  describe('reorder controls', () => {
    it('shows reorder controls when orderNumber and totalItems are provided', () => {
      render(
        <ConditionEditor
          condition={{ type: 'stream_name_contains', value: 'ESPN' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
          orderNumber={1}
          totalItems={3}
        />
      );

      expect(screen.getByTestId('reorder-controls')).toBeInTheDocument();
      expect(screen.getByTestId('order-number')).toBeInTheDocument();
    });

    it('hides reorder controls when totalItems is 1 or less', () => {
      render(
        <ConditionEditor
          condition={{ type: 'stream_name_contains', value: 'ESPN' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
          orderNumber={1}
          totalItems={1}
        />
      );

      expect(screen.queryByTestId('reorder-controls')).not.toBeInTheDocument();
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
