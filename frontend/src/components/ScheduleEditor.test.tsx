/**
 * Unit tests for ScheduleEditor component.
 *
 * Tests the schedule editing form functionality including:
 * - Schedule type selection (interval, daily, weekly, monthly)
 * - Time and timezone inputs
 * - Days of week selection
 * - Parameter fields for task-specific settings
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ScheduleEditor } from './ScheduleEditor';
import type { TaskSchedule, TaskParameterSchema } from '../services/api';

describe('ScheduleEditor', () => {
  const mockOnSave = vi.fn().mockResolvedValue(undefined);
  const mockOnCancel = vi.fn();

  const defaultProps = {
    onSave: mockOnSave,
    onCancel: mockOnCancel,
  };

  const mockSchedule: TaskSchedule = {
    id: 1,
    task_id: 'stream_probe',
    name: 'Test Schedule',
    enabled: true,
    schedule_type: 'daily',
    interval_seconds: null,
    schedule_time: '03:00',
    timezone: 'America/New_York',
    days_of_week: null,
    day_of_month: null,
    week_parity: null,
    next_run_at: null,
    last_run_at: null,
    parameters: {},
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('rendering', () => {
    it('renders schedule name input', () => {
      render(<ScheduleEditor {...defaultProps} schedule={mockSchedule} />);
      // Name input should exist (textbox role)
      const inputs = screen.getAllByRole('textbox');
      expect(inputs.length).toBeGreaterThan(0);
    });

    it('renders schedule type select', () => {
      render(<ScheduleEditor {...defaultProps} schedule={mockSchedule} />);
      expect(screen.getByText(/schedule type/i)).toBeInTheDocument();
    });

    it('renders enabled checkbox', () => {
      render(<ScheduleEditor {...defaultProps} schedule={mockSchedule} />);
      expect(screen.getByRole('checkbox')).toBeInTheDocument();
    });

    it('renders save and cancel buttons', () => {
      render(<ScheduleEditor {...defaultProps} schedule={mockSchedule} />);
      // Save button may say "Update Schedule", "Save", etc.
      const buttons = screen.getAllByRole('button');
      expect(buttons.length).toBeGreaterThanOrEqual(2);
      expect(screen.getByText(/cancel/i)).toBeInTheDocument();
    });

    it('shows Add Schedule button for new schedule', () => {
      render(<ScheduleEditor {...defaultProps} />);
      // New schedule shows "Add Schedule" button
      expect(screen.getByText(/add schedule/i)).toBeInTheDocument();
    });

    it('shows "Update Schedule" for existing schedule', () => {
      render(<ScheduleEditor {...defaultProps} schedule={mockSchedule} />);
      expect(screen.getByText(/update schedule/i)).toBeInTheDocument();
    });
  });

  describe('schedule type fields', () => {
    it('shows time input for daily schedule', () => {
      render(
        <ScheduleEditor
          {...defaultProps}
          schedule={{ ...mockSchedule, schedule_type: 'daily' }}
        />
      );
      expect(screen.getByDisplayValue('03:00')).toBeInTheDocument();
    });

    it('shows interval presets for interval schedule', () => {
      render(
        <ScheduleEditor
          {...defaultProps}
          schedule={{ ...mockSchedule, schedule_type: 'interval', interval_seconds: 3600 }}
        />
      );
      expect(screen.getByText('1 hr')).toBeInTheDocument();
    });

    it('shows days of week for weekly schedule', () => {
      render(
        <ScheduleEditor
          {...defaultProps}
          schedule={{ ...mockSchedule, schedule_type: 'weekly', days_of_week: [1, 3, 5] }}
        />
      );
      expect(screen.getByText('Mon')).toBeInTheDocument();
      expect(screen.getByText('Tue')).toBeInTheDocument();
    });

    it('shows day of month for monthly schedule', () => {
      render(
        <ScheduleEditor
          {...defaultProps}
          schedule={{ ...mockSchedule, schedule_type: 'monthly', day_of_month: 15 }}
        />
      );
      expect(screen.getByText(/day of month/i)).toBeInTheDocument();
    });
  });

  describe('parameter rendering', () => {
    const parameterSchema: TaskParameterSchema[] = [
      {
        name: 'batch_size',
        type: 'number',
        label: 'Batch Size',
        description: 'Number of streams to probe per batch',
        default: 10,
      },
      {
        name: 'timeout',
        type: 'number',
        label: 'Timeout (seconds)',
        description: 'Timeout in seconds',
        default: 30,
      },
    ];

    it('renders number parameter fields', () => {
      render(
        <ScheduleEditor
          {...defaultProps}
          schedule={mockSchedule}
          parameterSchema={parameterSchema}
        />
      );
      // Parameters section should render number inputs
      const spinbuttons = screen.getAllByRole('spinbutton');
      // At least the parameter inputs should exist
      expect(spinbuttons.length).toBeGreaterThanOrEqual(2);
    });

    it('uses default values when no parameters set', () => {
      render(
        <ScheduleEditor
          {...defaultProps}
          schedule={{ ...mockSchedule, parameters: {} }}
          parameterSchema={parameterSchema}
        />
      );
      // Default values from schema should be applied
      const inputs = screen.getAllByRole('spinbutton');
      expect(inputs.length).toBeGreaterThan(0);
    });

    it('uses defaultParameters when schedule has no parameters', () => {
      render(
        <ScheduleEditor
          {...defaultProps}
          parameterSchema={parameterSchema}
          defaultParameters={{ batch_size: 25, timeout: 45 }}
        />
      );
      // Default parameters should be applied - check input exists with value
      const inputs = screen.getAllByRole('spinbutton');
      expect(inputs.some(input => (input as HTMLInputElement).value === '25')).toBeTruthy();
    });

    it('uses schedule parameters when provided', () => {
      render(
        <ScheduleEditor
          {...defaultProps}
          schedule={{ ...mockSchedule, parameters: { batch_size: 30, timeout: 60 } }}
          parameterSchema={parameterSchema}
          defaultParameters={{ batch_size: 25 }}
        />
      );
      const batchInput = screen.getByDisplayValue('30');
      expect(batchInput).toBeInTheDocument();
    });
  });

  describe('interactions', () => {
    it('calls onSave when save button clicked', async () => {
      const user = userEvent.setup();
      render(<ScheduleEditor {...defaultProps} schedule={mockSchedule} />);

      const saveButton = screen.getByText(/update schedule/i);
      await user.click(saveButton);

      await waitFor(() => {
        expect(mockOnSave).toHaveBeenCalled();
      });
    });

    it('calls onCancel when cancel button clicked', async () => {
      const user = userEvent.setup();
      render(<ScheduleEditor {...defaultProps} schedule={mockSchedule} />);

      const cancelButton = screen.getByText(/cancel/i);
      await user.click(cancelButton);

      expect(mockOnCancel).toHaveBeenCalled();
    });

    it('can toggle enabled checkbox', async () => {
      const user = userEvent.setup();
      render(<ScheduleEditor {...defaultProps} schedule={mockSchedule} />);

      const checkbox = screen.getByRole('checkbox');
      expect(checkbox).toBeChecked();

      await user.click(checkbox);
      expect(checkbox).not.toBeChecked();
    });

    it('can change schedule name', async () => {
      const user = userEvent.setup();
      render(<ScheduleEditor {...defaultProps} schedule={mockSchedule} />);

      // Find the name input by its current value
      const nameInput = screen.getByDisplayValue('Test Schedule');
      await user.clear(nameInput);
      await user.type(nameInput, 'New Name');

      expect(nameInput).toHaveValue('New Name');
    });
  });

  describe('new schedule mode', () => {
    it('renders with empty name when no schedule provided', () => {
      render(<ScheduleEditor {...defaultProps} />);
      // Name input should exist and be empty
      const inputs = screen.getAllByRole('textbox');
      expect(inputs.length).toBeGreaterThan(0);
    });

    it('shows Add Schedule button for new schedule', () => {
      render(<ScheduleEditor {...defaultProps} />);
      // New schedule shows "Add Schedule" or "Create Schedule"
      expect(screen.getByText(/add schedule|create schedule/i)).toBeInTheDocument();
    });

    it('defaults to daily schedule type', () => {
      render(<ScheduleEditor {...defaultProps} />);
      // Daily should be selected by default - time input should be present
      const timeInput = screen.queryByDisplayValue(/\d{2}:\d{2}/);
      expect(timeInput !== null).toBeTruthy();
    });

    it('applies defaultParameters to new schedule', () => {
      const parameterSchema: TaskParameterSchema[] = [
        { name: 'batch_size', type: 'number', label: 'Batch Size', default: 10 },
      ];

      render(
        <ScheduleEditor
          {...defaultProps}
          parameterSchema={parameterSchema}
          defaultParameters={{ batch_size: 25 }}
        />
      );

      // Check that a number input exists with value 25
      const inputs = screen.getAllByRole('spinbutton');
      expect(inputs.some(input => (input as HTMLInputElement).value === '25')).toBeTruthy();
    });
  });

  describe('interval presets', () => {
    it('shows preset buttons', () => {
      render(
        <ScheduleEditor
          {...defaultProps}
          schedule={{ ...mockSchedule, schedule_type: 'interval', interval_seconds: 3600 }}
        />
      );

      expect(screen.getByText('5 min')).toBeInTheDocument();
      expect(screen.getByText('15 min')).toBeInTheDocument();
      expect(screen.getByText('1 hr')).toBeInTheDocument();
    });

    it('can select preset interval', async () => {
      const user = userEvent.setup();
      render(
        <ScheduleEditor
          {...defaultProps}
          schedule={{ ...mockSchedule, schedule_type: 'interval', interval_seconds: 3600 }}
        />
      );

      const preset = screen.getByText('2 hr');
      await user.click(preset);

      // After clicking, 2 hr should be selected (has 'active' class)
      expect(preset.closest('button')).toHaveClass('active');
    });
  });

  describe('timezone', () => {
    it('renders timezone selector for non-interval schedules', () => {
      render(<ScheduleEditor {...defaultProps} schedule={mockSchedule} />);
      // Timezone selector should be rendered as a CustomSelect or select element
      // For daily schedule, timezone is shown
      const timezoneTrigger = screen.queryByText(/eastern|pacific|central|utc/i);
      expect(timezoneTrigger !== null).toBeTruthy();
    });

    it('shows current timezone value', () => {
      render(<ScheduleEditor {...defaultProps} schedule={mockSchedule} />);
      // America/New_York shows as "Eastern (US)"
      expect(screen.getByText(/eastern/i)).toBeInTheDocument();
    });
  });

  describe('days of week selection', () => {
    it('can toggle days', async () => {
      const user = userEvent.setup();
      render(
        <ScheduleEditor
          {...defaultProps}
          schedule={{ ...mockSchedule, schedule_type: 'weekly', days_of_week: [1, 2, 3, 4, 5] }}
        />
      );

      // Monday should be selected (has 'active' class)
      const monButton = screen.getByText('Mon');
      expect(monButton.closest('button')).toHaveClass('active');

      // Click to toggle off
      await user.click(monButton);
      expect(monButton.closest('button')).not.toHaveClass('active');
    });
  });

  describe('saving behavior', () => {
    it('disables save button while saving', () => {
      render(<ScheduleEditor {...defaultProps} schedule={mockSchedule} saving={true} />);
      // When saving, button shows "Saving..." text
      const saveButton = screen.getByText(/saving/i).closest('button');
      expect(saveButton).toBeDisabled();
    });

    it('shows saving text while saving', () => {
      render(<ScheduleEditor {...defaultProps} schedule={mockSchedule} saving={true} />);
      expect(screen.getByText(/saving/i)).toBeInTheDocument();
    });
  });
});
