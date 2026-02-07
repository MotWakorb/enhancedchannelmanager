/**
 * TDD Tests for ActionEditor component.
 *
 * These tests define the expected behavior of the component BEFORE implementation.
 */
import { describe, it, expect, beforeAll, afterAll, afterEach, vi } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {
  server,
  resetMockDataStore,
  mockDataStore,
  createMockChannelGroup,
} from '../../test/mocks/server';
import { ActionEditor } from './ActionEditor';
import type { Action, ActionType } from '../../types/autoCreation';

// Setup MSW server
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => {
  server.resetHandlers();
  resetMockDataStore();
});
afterAll(() => server.close());

describe('ActionEditor', () => {
  describe('rendering', () => {
    it('renders action type selector', () => {
      render(
        <ActionEditor
          action={{ type: 'create_channel' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      // Action type selector should be the first combobox
      expect(screen.getAllByRole('combobox').length).toBeGreaterThan(0);
    });

    it('renders remove button', () => {
      render(
        <ActionEditor
          action={{ type: 'create_channel' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      expect(screen.getByRole('button', { name: /remove/i })).toBeInTheDocument();
    });

    it('displays action type label', () => {
      render(
        <ActionEditor
          action={{ type: 'create_channel' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      expect(screen.getByText(/create channel/i)).toBeInTheDocument();
    });
  });

  describe('create_channel action', () => {
    it('renders name template input', () => {
      render(
        <ActionEditor
          action={{ type: 'create_channel', name_template: '{stream_name}' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      expect(screen.getByLabelText(/name template/i)).toHaveValue('{stream_name}');
    });

    it('renders group selector', () => {
      mockDataStore.channelGroups.push(
        createMockChannelGroup({ name: 'Sports' }),
        createMockChannelGroup({ name: 'News' })
      );

      render(
        <ActionEditor
          action={{ type: 'create_channel' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      expect(screen.getByLabelText(/target group/i)).toBeInTheDocument();
    });

    it('renders if_exists selector', () => {
      render(
        <ActionEditor
          action={{ type: 'create_channel', if_exists: 'skip' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      expect(screen.getByLabelText(/if.*exists/i)).toBeInTheDocument();
    });

    it('shows all if_exists options', async () => {
      const user = userEvent.setup();
      render(
        <ActionEditor
          action={{ type: 'create_channel' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      await user.click(screen.getByLabelText(/if.*exists/i));

      expect(screen.getByText(/skip/i)).toBeInTheDocument();
      expect(screen.getByText(/merge/i)).toBeInTheDocument();
      expect(screen.getByText(/update/i)).toBeInTheDocument();
      expect(screen.getByText(/use existing/i)).toBeInTheDocument();
    });
  });

  describe('create_group action', () => {
    it('renders name template input', () => {
      render(
        <ActionEditor
          action={{ type: 'create_group', name_template: '{stream_group}' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      expect(screen.getByLabelText(/name template/i)).toHaveValue('{stream_group}');
    });

    it('renders if_exists selector', () => {
      render(
        <ActionEditor
          action={{ type: 'create_group' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      expect(screen.getByLabelText(/if.*exists/i)).toBeInTheDocument();
    });
  });

  describe('merge_streams action', () => {
    it('renders target channel finder options', () => {
      render(
        <ActionEditor
          action={{ type: 'merge_streams', target: 'auto' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      expect(screen.getByLabelText(/target/i)).toBeInTheDocument();
    });

    it('shows find_channel_by options when target is existing_channel', async () => {
      const user = userEvent.setup();
      render(
        <ActionEditor
          action={{ type: 'merge_streams', target: 'existing_channel' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      expect(screen.getByLabelText(/find.*by/i)).toBeInTheDocument();

      await user.click(screen.getByLabelText(/find.*by/i));

      expect(screen.getByText(/exact name/i)).toBeInTheDocument();
      expect(screen.getByText(/regex/i)).toBeInTheDocument();
      expect(screen.getByText(/tvg.*id/i)).toBeInTheDocument();
    });

    it('shows find_channel_value input when find_by is set', () => {
      render(
        <ActionEditor
          action={{
            type: 'merge_streams',
            target: 'existing_channel',
            find_channel_by: 'name_exact',
            find_channel_value: 'ESPN',
          }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      expect(screen.getByLabelText(/find.*value/i)).toHaveValue('ESPN');
    });
  });

  describe('assign_logo action', () => {
    it('renders logo URL/value input', () => {
      render(
        <ActionEditor
          action={{ type: 'assign_logo', value: 'https://example.com/logo.png' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      expect(screen.getByLabelText(/logo.*url/i)).toHaveValue('https://example.com/logo.png');
    });

    it('shows template variables hint', () => {
      render(
        <ActionEditor
          action={{ type: 'assign_logo' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      expect(screen.getByText(/template variables/i)).toBeInTheDocument();
    });
  });

  describe('assign_tvg_id action', () => {
    it('renders TVG ID template input', () => {
      render(
        <ActionEditor
          action={{ type: 'assign_tvg_id', value: '{tvg_id}' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      expect(screen.getByLabelText(/tvg.*id/i)).toHaveValue('{tvg_id}');
    });
  });

  describe('set_channel_number action', () => {
    it('renders channel number input', () => {
      render(
        <ActionEditor
          action={{ type: 'set_channel_number', channel_number: '101' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      expect(screen.getByLabelText(/channel number/i)).toHaveValue('101');
    });

    it('accepts numeric and template values', async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();

      render(
        <ActionEditor
          action={{ type: 'set_channel_number' }}
          onChange={onChange}
          onRemove={vi.fn()}
        />
      );

      const input = screen.getByLabelText(/channel number/i);
      // Type a numeric value (curly braces have special meaning in userEvent)
      await user.type(input, '100');

      await waitFor(() => {
        expect(onChange).toHaveBeenCalled();
      });
    });
  });

  describe('skip action', () => {
    it('renders with minimal UI', () => {
      render(
        <ActionEditor
          action={{ type: 'skip' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      // Skip action should only show the type selector
      expect(screen.getByText(/skip/i)).toBeInTheDocument();
      expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    });

    it('shows description of what skip does', () => {
      render(
        <ActionEditor
          action={{ type: 'skip' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      expect(screen.getByText(/stream will not be processed/i)).toBeInTheDocument();
    });
  });

  describe('stop_processing action', () => {
    it('renders with description', () => {
      render(
        <ActionEditor
          action={{ type: 'stop_processing' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      expect(screen.getByText(/stop processing/i)).toBeInTheDocument();
      expect(screen.getByText(/no further rules/i)).toBeInTheDocument();
    });
  });

  describe('log_match action', () => {
    it('renders message input', () => {
      render(
        <ActionEditor
          action={{ type: 'log_match', message: 'Matched: {stream_name}' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      expect(screen.getByLabelText(/message/i)).toHaveValue('Matched: {stream_name}');
    });
  });

  describe('onChange handling', () => {
    it('calls onChange when action type is changed', async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();

      render(
        <ActionEditor
          action={{ type: 'create_channel' }}
          onChange={onChange}
          onRemove={vi.fn()}
        />
      );

      // Click the action type selector (first combobox)
      await user.click(screen.getByRole('combobox', { name: /action type/i }));
      // Click the Skip option in the dropdown
      const skipOptions = screen.getAllByRole('option', { name: /skip/i });
      await user.click(skipOptions[0]);

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ type: 'skip' })
      );
    });

    it('calls onChange when name_template is updated', async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();

      render(
        <ActionEditor
          action={{ type: 'create_channel', name_template: '' }}
          onChange={onChange}
          onRemove={vi.fn()}
        />
      );

      // Wait for component to settle (groups fetch etc)
      await waitFor(() => {
        expect(screen.getByLabelText(/name template/i)).toBeInTheDocument();
      });

      await user.type(screen.getByLabelText(/name template/i), 'test');

      await waitFor(() => {
        expect(onChange).toHaveBeenCalled();
      });
    });

    it('calls onChange when if_exists is changed', async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();

      render(
        <ActionEditor
          action={{ type: 'create_channel', if_exists: 'skip' }}
          onChange={onChange}
          onRemove={vi.fn()}
        />
      );

      // Change the select value
      await user.selectOptions(screen.getByLabelText(/if.*exists/i), 'merge');

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ if_exists: 'merge' })
      );
    });

    it('calls onChange when group_id is changed', async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();
      const group = createMockChannelGroup({ name: 'Sports' });
      mockDataStore.channelGroups.push(group);

      render(
        <ActionEditor
          action={{ type: 'create_channel' }}
          onChange={onChange}
          onRemove={vi.fn()}
        />
      );

      // Wait for groups to load
      await waitFor(() => {
        expect(screen.getByLabelText(/target group/i)).toBeInTheDocument();
      });

      // Select the group
      await user.selectOptions(screen.getByLabelText(/target group/i), String(group.id));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ group_id: group.id })
      );
    });
  });

  describe('onRemove handling', () => {
    it('calls onRemove when remove button is clicked', async () => {
      const user = userEvent.setup();
      const onRemove = vi.fn();

      render(
        <ActionEditor
          action={{ type: 'create_channel' }}
          onChange={vi.fn()}
          onRemove={onRemove}
        />
      );

      await user.click(screen.getByRole('button', { name: /remove/i }));

      expect(onRemove).toHaveBeenCalled();
    });

    it('can be disabled from removing', () => {
      render(
        <ActionEditor
          action={{ type: 'create_channel' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
          canRemove={false}
        />
      );

      expect(screen.queryByRole('button', { name: /remove/i })).not.toBeInTheDocument();
    });
  });

  describe('validation', () => {
    it('shows error for empty required name_template', () => {
      render(
        <ActionEditor
          action={{ type: 'create_channel', name_template: '' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
          showValidation={true}
        />
      );

      expect(screen.getByText(/name template is required/i)).toBeInTheDocument();
    });

    it('shows error for invalid template syntax', () => {
      render(
        <ActionEditor
          action={{ type: 'create_channel', name_template: '{invalid_var}' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
          showValidation={true}
        />
      );

      expect(screen.getByText(/unknown variable/i)).toBeInTheDocument();
    });

    it('validates merge_streams target configuration', () => {
      render(
        <ActionEditor
          action={{
            type: 'merge_streams',
            target: 'existing_channel',
            find_channel_by: 'name_exact',
            find_channel_value: '', // Missing required value
          }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
          showValidation={true}
        />
      );

      expect(screen.getByText(/find.*value is required/i)).toBeInTheDocument();
    });
  });

  describe('template variables helper', () => {
    it('shows available template variables', async () => {
      const user = userEvent.setup();
      render(
        <ActionEditor
          action={{ type: 'create_channel' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      // Click helper button to show variables
      await user.click(screen.getByRole('button', { name: /show variables/i }));

      expect(screen.getByText(/{stream_name}/)).toBeInTheDocument();
      expect(screen.getByText(/{stream_group}/)).toBeInTheDocument();
      expect(screen.getByText(/{quality}/)).toBeInTheDocument();
    });

    it('inserts variable when clicked', async () => {
      const user = userEvent.setup();
      const onChange = vi.fn();

      render(
        <ActionEditor
          action={{ type: 'create_channel', name_template: '' }}
          onChange={onChange}
          onRemove={vi.fn()}
        />
      );

      // Focus the template input first
      const input = screen.getByLabelText(/name template/i);
      await user.click(input);

      // Open variables helper
      await user.click(screen.getByRole('button', { name: /show variables/i }));

      // Click a variable
      await user.click(screen.getByText(/{stream_name}/));

      expect(onChange).toHaveBeenCalledWith(
        expect.objectContaining({ name_template: '{stream_name}' })
      );
    });
  });

  describe('template preview', () => {
    it('shows preview of template with example values', () => {
      render(
        <ActionEditor
          action={{ type: 'create_channel', name_template: '{stream_name} HD' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
          showPreview={true}
        />
      );

      expect(screen.getByText(/preview/i)).toBeInTheDocument();
      // Should show example like "ESPN HD"
      expect(screen.getByText(/ESPN HD/i)).toBeInTheDocument();
    });

    it('updates preview when template changes', async () => {
      const user = userEvent.setup();
      const { rerender } = render(
        <ActionEditor
          action={{ type: 'create_channel', name_template: '{stream_name}' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
          showPreview={true}
        />
      );

      rerender(
        <ActionEditor
          action={{ type: 'create_channel', name_template: '{stream_name} ({quality})' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
          showPreview={true}
        />
      );

      expect(screen.getByText(/ESPN.*1080p/i)).toBeInTheDocument();
    });
  });

  describe('readonly mode', () => {
    it('disables all inputs when readonly', () => {
      render(
        <ActionEditor
          action={{ type: 'create_channel', name_template: '{stream_name}' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
          readonly={true}
        />
      );

      expect(screen.getByLabelText(/name template/i)).toBeDisabled();
      // All comboboxes should be disabled
      const comboboxes = screen.getAllByRole('combobox');
      comboboxes.forEach(cb => expect(cb).toBeDisabled());
    });

    it('hides remove button when readonly', () => {
      render(
        <ActionEditor
          action={{ type: 'create_channel' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
          readonly={true}
        />
      );

      expect(screen.queryByRole('button', { name: /remove/i })).not.toBeInTheDocument();
    });
  });

  describe('action type categories', () => {
    it('groups action types by category in selector', async () => {
      const user = userEvent.setup();
      render(
        <ActionEditor
          action={{ type: 'create_channel' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      // Click the action type selector
      await user.click(screen.getByRole('combobox', { name: /action type/i }));

      expect(screen.getByText(/creation/i)).toBeInTheDocument();
      expect(screen.getByText(/assignment/i)).toBeInTheDocument();
      expect(screen.getByText(/control/i)).toBeInTheDocument();
    });
  });

  describe('accessibility', () => {
    it('has accessible labels for inputs', () => {
      render(
        <ActionEditor
          action={{ type: 'create_channel', name_template: '' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
        />
      );

      expect(screen.getByLabelText(/action type/i)).toBeInTheDocument();
      expect(screen.getByLabelText(/name template/i)).toBeInTheDocument();
    });

    it('shows validation errors with aria-describedby', () => {
      render(
        <ActionEditor
          action={{ type: 'create_channel', name_template: '' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
          showValidation={true}
        />
      );

      const input = screen.getByLabelText(/name template/i);
      const describedBy = input.getAttribute('aria-describedby');
      expect(describedBy).toBeTruthy();
    });
  });

  describe('drag and drop', () => {
    it('shows drag handle when draggable', () => {
      render(
        <ActionEditor
          action={{ type: 'create_channel' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
          draggable={true}
        />
      );

      expect(screen.getByTestId('drag-handle')).toBeInTheDocument();
    });

    it('hides drag handle when not draggable', () => {
      render(
        <ActionEditor
          action={{ type: 'create_channel' }}
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
        <ActionEditor
          action={{ type: 'create_channel' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
          compact={true}
        />
      );

      expect(container.firstChild).toHaveClass('compact');
    });
  });

  describe('action dependencies', () => {
    it('shows warning when action depends on previous action', () => {
      render(
        <ActionEditor
          action={{ type: 'assign_logo' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
          previousActions={[{ type: 'skip' }]}
        />
      );

      expect(screen.getByText(/requires.*channel/i)).toBeInTheDocument();
    });

    it('hides warning when dependency is satisfied', () => {
      render(
        <ActionEditor
          action={{ type: 'assign_logo', value: 'logo.png' }}
          onChange={vi.fn()}
          onRemove={vi.fn()}
          previousActions={[{ type: 'create_channel', name_template: '{stream_name}' }]}
        />
      );

      expect(screen.queryByText(/requires.*channel/i)).not.toBeInTheDocument();
    });
  });
});
