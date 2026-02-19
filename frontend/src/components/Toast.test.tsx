/**
 * Unit tests for Toast component.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { Toast, ToastType } from './Toast';

describe('Toast', () => {
  const defaultProps = {
    id: 'test-toast',
    type: 'info' as ToastType,
    message: 'Test message',
    onDismiss: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  describe('rendering', () => {
    it('renders the message', () => {
      render(<Toast {...defaultProps} />);
      expect(screen.getByText('Test message')).toBeInTheDocument();
    });

    it('renders the title when provided', () => {
      render(<Toast {...defaultProps} title="Test Title" />);
      expect(screen.getByText('Test Title')).toBeInTheDocument();
    });

    it('does not render title when not provided', () => {
      render(<Toast {...defaultProps} />);
      expect(screen.queryByText('Test Title')).not.toBeInTheDocument();
    });

    it('has role="alert"', () => {
      render(<Toast {...defaultProps} />);
      expect(screen.getByRole('alert')).toBeInTheDocument();
    });
  });

  describe('toast types', () => {
    const types: ToastType[] = ['info', 'success', 'warning', 'error'];

    types.forEach((type) => {
      it(`renders ${type} toast with correct class`, () => {
        render(<Toast {...defaultProps} type={type} />);
        expect(screen.getByRole('alert')).toHaveClass(`toast-${type}`);
      });
    });

    it('renders info icon for info type', () => {
      render(<Toast {...defaultProps} type="info" />);
      expect(screen.getByText('info')).toBeInTheDocument();
    });

    it('renders check_circle icon for success type', () => {
      render(<Toast {...defaultProps} type="success" />);
      expect(screen.getByText('check_circle')).toBeInTheDocument();
    });

    it('renders warning icon for warning type', () => {
      render(<Toast {...defaultProps} type="warning" />);
      expect(screen.getByText('warning')).toBeInTheDocument();
    });

    it('renders error icon for error type', () => {
      render(<Toast {...defaultProps} type="error" />);
      expect(screen.getByText('error')).toBeInTheDocument();
    });
  });

  describe('accessibility', () => {
    it('has aria-live="polite" for non-error types', () => {
      render(<Toast {...defaultProps} type="info" />);
      expect(screen.getByRole('alert')).toHaveAttribute('aria-live', 'polite');
    });

    it('has aria-live="assertive" for error type', () => {
      render(<Toast {...defaultProps} type="error" />);
      expect(screen.getByRole('alert')).toHaveAttribute('aria-live', 'assertive');
    });

    it('dismiss button has aria-label', () => {
      render(<Toast {...defaultProps} />);
      expect(screen.getByLabelText('Dismiss notification')).toBeInTheDocument();
    });
  });

  describe('dismiss functionality', () => {
    it('calls onDismiss with id when dismiss button clicked', async () => {
      render(<Toast {...defaultProps} />);

      fireEvent.click(screen.getByLabelText('Dismiss notification'));

      // Wait for exit animation
      act(() => {
        vi.advanceTimersByTime(300);
      });

      expect(defaultProps.onDismiss).toHaveBeenCalledWith('test-toast');
    });

    it('adds exiting class when dismissing', () => {
      render(<Toast {...defaultProps} />);

      fireEvent.click(screen.getByLabelText('Dismiss notification'));

      expect(screen.getByRole('alert')).toHaveClass('toast-exiting');
    });
  });

  describe('auto-dismiss', () => {
    it('auto-dismisses after duration', () => {
      render(<Toast {...defaultProps} duration={5000} />);

      act(() => {
        vi.advanceTimersByTime(5000);
      });

      // Wait for exit animation
      act(() => {
        vi.advanceTimersByTime(300);
      });

      expect(defaultProps.onDismiss).toHaveBeenCalledWith('test-toast');
    });

    it('does not auto-dismiss when duration is 0', () => {
      render(<Toast {...defaultProps} duration={0} />);

      act(() => {
        vi.advanceTimersByTime(10000);
      });

      expect(defaultProps.onDismiss).not.toHaveBeenCalled();
    });

    it('shows progress bar when duration > 0', () => {
      render(<Toast {...defaultProps} duration={5000} />);

      const progressBar = document.querySelector('.toast-progress');
      expect(progressBar).toBeInTheDocument();
    });

    it('hides progress bar when duration is 0', () => {
      render(<Toast {...defaultProps} duration={0} />);

      const progressBar = document.querySelector('.toast-progress');
      expect(progressBar).not.toBeInTheDocument();
    });
  });

  describe('action button', () => {
    const actionProps = {
      ...defaultProps,
      action: {
        label: 'Undo',
        onClick: vi.fn(),
      },
    };

    it('renders action button when provided', () => {
      render(<Toast {...actionProps} />);
      expect(screen.getByText('Undo')).toBeInTheDocument();
    });

    it('calls action onClick when clicked', () => {
      render(<Toast {...actionProps} />);

      fireEvent.click(screen.getByText('Undo'));

      expect(actionProps.action.onClick).toHaveBeenCalledTimes(1);
    });

    it('dismisses toast after action clicked', () => {
      render(<Toast {...actionProps} />);

      fireEvent.click(screen.getByText('Undo'));

      // Wait for exit animation
      act(() => {
        vi.advanceTimersByTime(300);
      });

      expect(defaultProps.onDismiss).toHaveBeenCalledWith('test-toast');
    });

    it('does not render action button when not provided', () => {
      render(<Toast {...defaultProps} />);
      expect(screen.queryByText('Undo')).not.toBeInTheDocument();
    });
  });

  describe('progress bar', () => {
    it('starts at 100%', () => {
      render(<Toast {...defaultProps} duration={5000} />);

      const progressBar = document.querySelector('.toast-progress') as HTMLElement;
      expect(progressBar.style.width).toBe('100%');
    });

    it('decreases over time', () => {
      render(<Toast {...defaultProps} duration={5000} />);

      act(() => {
        vi.advanceTimersByTime(2500); // Half the duration
      });

      const progressBar = document.querySelector('.toast-progress') as HTMLElement;
      const width = parseFloat(progressBar.style.width);
      // Should be around 50% with some tolerance for timing
      expect(width).toBeLessThan(60);
      expect(width).toBeGreaterThan(40);
    });
  });
});
