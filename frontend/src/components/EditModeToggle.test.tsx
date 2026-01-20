/**
 * Unit tests for EditModeToggle component.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { EditModeToggle } from './EditModeToggle';

describe('EditModeToggle', () => {
  const defaultProps = {
    isEditMode: false,
    stagedCount: 0,
    onEnter: vi.fn(),
    onExit: vi.fn(),
  };

  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('when not in edit mode', () => {
    it('renders "Edit Mode" label', () => {
      render(<EditModeToggle {...defaultProps} />);
      expect(screen.getByText('Edit Mode')).toBeInTheDocument();
    });

    it('shows edit icon', () => {
      render(<EditModeToggle {...defaultProps} />);
      expect(screen.getByText('edit')).toBeInTheDocument();
    });

    it('calls onEnter when clicked', () => {
      render(<EditModeToggle {...defaultProps} />);
      fireEvent.click(screen.getByRole('button'));
      expect(defaultProps.onEnter).toHaveBeenCalledTimes(1);
      expect(defaultProps.onExit).not.toHaveBeenCalled();
    });

    it('does not show count badge', () => {
      render(<EditModeToggle {...defaultProps} stagedCount={5} />);
      expect(screen.queryByText('5')).not.toBeInTheDocument();
    });

    it('has correct title attribute', () => {
      render(<EditModeToggle {...defaultProps} />);
      expect(screen.getByRole('button')).toHaveAttribute('title', 'Enter Edit Mode');
    });
  });

  describe('when in edit mode', () => {
    const editModeProps = {
      ...defaultProps,
      isEditMode: true,
    };

    it('renders "Done" label', () => {
      render(<EditModeToggle {...editModeProps} />);
      expect(screen.getByText('Done')).toBeInTheDocument();
    });

    it('shows check icon', () => {
      render(<EditModeToggle {...editModeProps} />);
      expect(screen.getByText('check')).toBeInTheDocument();
    });

    it('calls onExit when clicked', () => {
      render(<EditModeToggle {...editModeProps} />);
      fireEvent.click(screen.getByRole('button'));
      expect(defaultProps.onExit).toHaveBeenCalledTimes(1);
      expect(defaultProps.onEnter).not.toHaveBeenCalled();
    });

    it('has correct title attribute', () => {
      render(<EditModeToggle {...editModeProps} />);
      expect(screen.getByRole('button')).toHaveAttribute('title', 'Exit Edit Mode');
    });

    it('has active class', () => {
      render(<EditModeToggle {...editModeProps} />);
      expect(screen.getByRole('button')).toHaveClass('active');
    });
  });

  describe('staged count badge', () => {
    it('shows count when in edit mode with staged changes', () => {
      render(<EditModeToggle {...defaultProps} isEditMode={true} stagedCount={3} />);
      expect(screen.getByText('3')).toBeInTheDocument();
    });

    it('does not show count when zero', () => {
      render(<EditModeToggle {...defaultProps} isEditMode={true} stagedCount={0} />);
      expect(screen.queryByText('0')).not.toBeInTheDocument();
    });

    it('does not show count when not in edit mode', () => {
      render(<EditModeToggle {...defaultProps} isEditMode={false} stagedCount={5} />);
      expect(screen.queryByText('5')).not.toBeInTheDocument();
    });

    it('updates when stagedCount changes', () => {
      const { rerender } = render(
        <EditModeToggle {...defaultProps} isEditMode={true} stagedCount={1} />
      );
      expect(screen.getByText('1')).toBeInTheDocument();

      rerender(<EditModeToggle {...defaultProps} isEditMode={true} stagedCount={5} />);
      expect(screen.getByText('5')).toBeInTheDocument();
      expect(screen.queryByText('1')).not.toBeInTheDocument();
    });
  });

  describe('disabled state', () => {
    it('is not disabled by default', () => {
      render(<EditModeToggle {...defaultProps} />);
      expect(screen.getByRole('button')).not.toBeDisabled();
    });

    it('can be disabled', () => {
      render(<EditModeToggle {...defaultProps} disabled={true} />);
      expect(screen.getByRole('button')).toBeDisabled();
    });

    it('does not call handlers when disabled', () => {
      render(<EditModeToggle {...defaultProps} disabled={true} />);
      fireEvent.click(screen.getByRole('button'));
      expect(defaultProps.onEnter).not.toHaveBeenCalled();
    });
  });
});
