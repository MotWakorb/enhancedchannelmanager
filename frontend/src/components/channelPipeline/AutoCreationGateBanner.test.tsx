/**
 * AutoCreationGateBanner tests (bead vkktd.4).
 *
 * Locks the show/hide contract for the "run-on-refresh rules will never fire"
 * nudge on the Channel Pipeline tab:
 *   - shows only when >=1 ENABLED run_on_refresh rule exists AND the
 *     auto_creation task is not effective-enabled;
 *   - dismissal persists, but RE-ARMS when the run-on-refresh rule-set
 *     changes;
 *   - the CTA fires the shared task-editor navigation contract.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import type { ChannelPipelineRule } from '../../types/channelPipeline';

vi.mock('../../services/api', () => ({
  getTask: vi.fn(),
}));

import * as api from '../../services/api';
import { AutoCreationGateBanner } from './AutoCreationGateBanner';

/** Mirrors the (unexported) localStorage key inside AutoCreationGateBanner. */
const AUTO_CREATION_GATE_DISMISS_KEY = 'ecm:auto-creation-gate-banner-dismissed';

function makeRule(overrides: Partial<ChannelPipelineRule> = {}): ChannelPipelineRule {
  return {
    id: 1,
    name: 'Sports rule',
    enabled: true,
    priority: 0,
    conditions: [],
    actions: [],
    run_on_refresh: true,
    stop_on_first_match: false,
    match_count: 0,
    created_at: '2026-07-01T00:00:00Z',
    updated_at: '2026-07-01T00:00:00Z',
    ...overrides,
  } as ChannelPipelineRule;
}

function mockTaskEffective(effective: boolean) {
  vi.mocked(api.getTask).mockResolvedValue({
    task_id: 'auto_creation',
    enabled: effective,
    effective_enabled: effective,
  } as unknown as Awaited<ReturnType<typeof api.getTask>>);
}

describe('AutoCreationGateBanner', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    sessionStorage.clear();
  });

  it('shows when an enabled run_on_refresh rule exists and the task is not effective-enabled', async () => {
    mockTaskEffective(false);
    render(<AutoCreationGateBanner rules={[makeRule()]} />);

    expect(await screen.findByTestId('auto-creation-gate-banner')).toBeInTheDocument();
    expect(screen.getByText(/run-on-refresh rules will never fire/i)).toBeInTheDocument();
  });

  it('stays hidden when the task is effective-enabled', async () => {
    mockTaskEffective(true);
    render(<AutoCreationGateBanner rules={[makeRule()]} />);

    await waitFor(() => expect(api.getTask).toHaveBeenCalled());
    expect(screen.queryByTestId('auto-creation-gate-banner')).not.toBeInTheDocument();
  });

  it('stays hidden with no enabled run_on_refresh rules (and never queries the task)', () => {
    render(
      <AutoCreationGateBanner
        rules={[makeRule({ run_on_refresh: false }), makeRule({ id: 2, enabled: false })]}
      />
    );

    expect(screen.queryByTestId('auto-creation-gate-banner')).not.toBeInTheDocument();
    expect(api.getTask).not.toHaveBeenCalled();
  });

  it('stays hidden when the task state check fails (fail quiet)', async () => {
    vi.mocked(api.getTask).mockRejectedValue(new Error('boom'));
    render(<AutoCreationGateBanner rules={[makeRule()]} />);

    await waitFor(() => expect(api.getTask).toHaveBeenCalled());
    expect(screen.queryByTestId('auto-creation-gate-banner')).not.toBeInTheDocument();
  });

  it('dismiss persists for the same rule-set', async () => {
    mockTaskEffective(false);
    const { unmount } = render(<AutoCreationGateBanner rules={[makeRule()]} />);

    fireEvent.click(await screen.findByTestId('auto-creation-gate-banner-dismiss'));
    expect(screen.queryByTestId('auto-creation-gate-banner')).not.toBeInTheDocument();

    // Remount with the same rule-set → still dismissed.
    unmount();
    render(<AutoCreationGateBanner rules={[makeRule()]} />);
    await waitFor(() => expect(api.getTask).toHaveBeenCalledTimes(2));
    expect(screen.queryByTestId('auto-creation-gate-banner')).not.toBeInTheDocument();
  });

  it('re-arms the dismissal when the run-on-refresh rule-set changes', async () => {
    mockTaskEffective(false);
    localStorage.setItem(AUTO_CREATION_GATE_DISMISS_KEY, '1'); // dismissed for rule-set {1}

    render(<AutoCreationGateBanner rules={[makeRule(), makeRule({ id: 2 })]} />);

    // Rule-set is now {1,2} → fingerprint differs → banner shows again.
    expect(await screen.findByTestId('auto-creation-gate-banner')).toBeInTheDocument();
  });

  it('CTA fires the shared open-task-editor contract for auto_creation', async () => {
    mockTaskEffective(false);
    const eventSpy = vi.fn();
    window.addEventListener('ecm:open-task-editor', eventSpy);

    render(<AutoCreationGateBanner rules={[makeRule()]} />);
    fireEvent.click(await screen.findByTestId('auto-creation-gate-banner-cta'));

    expect(sessionStorage.getItem('ecm:open-task-editor')).toBe(
      JSON.stringify({ taskId: 'auto_creation' })
    );
    expect(eventSpy).toHaveBeenCalled();
    window.removeEventListener('ecm:open-task-editor', eventSpy);
  });
});
