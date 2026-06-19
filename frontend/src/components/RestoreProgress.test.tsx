/**
 * Tests for the RestoreProgress component — the shared stage indicator for
 * restore and dry-run modes.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { RestoreProgress } from './RestoreProgress';
import type { RestoreProgressView } from '../hooks/useRestoreProgress';

/** A running view at the "channel" stage (9th of 13). */
function runningView(overrides: Partial<RestoreProgressView> = {}): RestoreProgressView {
  return {
    progress: {
      total: 10,
      current: 4,
      percentage: 65,
      status: 'running',
      current_item: 'channel',
      success_count: 0,
      failed_count: 0,
      skipped_count: 0,
      started_at: null,
    },
    stageNumber: 9,
    totalStages: 13,
    stageLabel: 'Channels',
    itemCurrent: 4,
    itemTotal: 10,
    percentage: 65,
    status: 'running',
    isRunning: true,
    isError: false,
    isComplete: false,
    error: null,
    ...overrides,
  };
}

describe('RestoreProgress', () => {
  it('renders "Stage N of 13" with the category name', () => {
    render(<RestoreProgress view={runningView()} />);
    expect(screen.getByText('Stage 9 of 13')).toBeInTheDocument();
    expect(screen.getByText('Channels')).toBeInTheDocument();
  });

  it('renders per-category item counts', () => {
    render(<RestoreProgress view={runningView()} />);
    expect(screen.getByText('4 of 10 items')).toBeInTheDocument();
  });

  it('renders an overall progress bar reflecting the percentage', () => {
    render(<RestoreProgress view={runningView()} />);
    const bar = screen.getByRole('progressbar');
    expect(bar).toHaveAttribute('aria-valuenow', '65');
    expect(bar).toHaveStyle({ width: '65%' });
  });

  it('uses restore labels in restore mode', () => {
    render(<RestoreProgress view={runningView()} mode="restore" />);
    expect(screen.getByText('Restoring')).toBeInTheDocument();
  });

  it('uses dry-run labels in dry-run mode (same machinery)', () => {
    render(<RestoreProgress view={runningView()} mode="dry-run" />);
    expect(screen.getByText('Computing dry-run')).toBeInTheDocument();
    // Stage machinery is identical across modes.
    expect(screen.getByText('Stage 9 of 13')).toBeInTheDocument();
    expect(screen.getByTestId('restore-progress')).toHaveAttribute('data-mode', 'dry-run');
  });

  it('renders an explicit error state on failure (not a frozen spinner)', () => {
    render(
      <RestoreProgress
        view={runningView({
          status: 'failed',
          isRunning: false,
          isError: true,
          isComplete: false,
          error: 'Importer step channel raised',
        })}
      />
    );
    expect(screen.getByText('Restore failed')).toBeInTheDocument();
    expect(screen.getByTestId('restore-progress-error')).toHaveTextContent(
      'Importer step channel raised'
    );
    // No progress bar in the error state — nothing is "in progress".
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
  });

  it('renders a completion state when done', () => {
    render(
      <RestoreProgress
        view={runningView({
          status: 'completed',
          percentage: 100,
          isRunning: false,
          isComplete: true,
        })}
      />
    );
    expect(screen.getByText('Restore complete')).toBeInTheDocument();
  });

  it('uses the dry-run error label in dry-run mode', () => {
    render(
      <RestoreProgress
        view={runningView({ status: 'failed', isRunning: false, isError: true, error: 'boom' })}
        mode="dry-run"
      />
    );
    expect(screen.getByText('Dry-run failed')).toBeInTheDocument();
  });
});
