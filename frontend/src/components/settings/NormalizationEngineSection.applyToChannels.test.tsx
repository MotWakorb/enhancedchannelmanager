/**
 * Tests for the "Apply to existing channels" button/modal flow added for
 * GH-104 (bd-u9odj). These tests exercise only the new apply-to-channels
 * feature and avoid re-rendering the whole Normalization settings surface.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';

import { NormalizationEngineSection } from './NormalizationEngineSection';

// Mock the notification context.
// IMPORTANT: `useNotifications` must return a STABLE reference across
// renders. The real hook returns a `useMemo`'d value; if the mock returns
// a fresh object each call, any `useCallback` that depends on it (e.g.
// `loadData` in this component) gets invalidated, re-firing its effect
// on every render and causing an infinite loop — which is what stuck
// these tests in a permanent "loading" state before the fix.
const mockSuccess = vi.fn();
const mockError = vi.fn();
const mockWarning = vi.fn();
const mockInfo = vi.fn();
const stableNotifications = {
  success: mockSuccess,
  error: mockError,
  warning: mockWarning,
  info: mockInfo,
  notify: vi.fn(),
  dismiss: vi.fn(),
  dismissAll: vi.fn(),
};
vi.mock('../../contexts/NotificationContext', () => ({
  useNotifications: () => stableNotifications,
}));

// Minimal mocks for the API surface this component talks to. We only care
// about apply-to-channels here; everything else just needs to resolve.
const mockPreview = vi.fn();
const mockExecute = vi.fn();
const mockGetRules = vi.fn();
const mockGetTags = vi.fn();
vi.mock('../../services/api', () => ({
  getNormalizationRules: (...args: unknown[]) => mockGetRules(...args),
  getTagGroups: (...args: unknown[]) => mockGetTags(...args),
  createNormalizationGroup: vi.fn(),
  updateNormalizationGroup: vi.fn(),
  deleteNormalizationGroup: vi.fn(),
  reorderNormalizationGroups: vi.fn(),
  createNormalizationRule: vi.fn(),
  updateNormalizationRule: vi.fn(),
  deleteNormalizationRule: vi.fn(),
  reorderNormalizationRules: vi.fn(),
  testNormalizationRule: vi.fn(),
  testNormalizationBatch: vi.fn(),
  normalizeTexts: vi.fn(),
  exportNormalizationRulesYaml: vi.fn(),
  importNormalizationRulesYaml: vi.fn(),
  previewApplyNormalizationToChannels: (...args: unknown[]) =>
    mockPreview(...args),
  executeApplyNormalizationToChannels: (...args: unknown[]) =>
    mockExecute(...args),
}));

function baseDiff(overrides: Partial<Record<string, unknown>> = {}) {
  return {
    channel_id: 1,
    current_name: 'RTL RAW',
    proposed_name: 'RTL',
    normalized_core: 'RTL',
    channel_number_prefix: '',
    group_id: 5,
    group_name: 'Germany',
    collision: false,
    collision_target_id: null,
    collision_target_name: null,
    collision_target_group_id: null,
    collision_target_group_name: null,
    suggested_action: 'rename' as const,
    ...overrides,
  };
}

describe('NormalizationEngineSection — apply to existing channels (GH-104)', () => {
  beforeEach(() => {
    // Use `mockClear` (not `mockReset`) — each test re-seeds
    // `mockPreview` / `mockExecute` with its own `mockResolvedValue`,
    // and the mount-time seeds below keep `mockGetRules` / `mockGetTags`
    // resolving cleanly.
    mockPreview.mockClear();
    mockExecute.mockClear();
    mockGetRules.mockClear();
    mockGetTags.mockClear();
    mockSuccess.mockClear();
    mockError.mockClear();
    mockWarning.mockClear();
    // Seed the mount-time API calls so the loading state always resolves.
    mockGetRules.mockResolvedValue({ groups: [] });
    mockGetTags.mockResolvedValue({ groups: [] });
  });

  it('renders the apply-to-channels button in the header', async () => {
    render(<NormalizationEngineSection />);
    // Wait for the initial data load promise to resolve
    await waitFor(() => {
      expect(
        screen.getByTestId('apply-to-channels-btn')
      ).toBeInTheDocument();
    });
  });

  it('opens modal and displays the diff rows returned by preview', async () => {
    mockPreview.mockResolvedValue({
      dry_run: true,
      channels_with_changes: 2,
      diffs: [
        baseDiff({ channel_id: 1, current_name: 'RTL RAW', proposed_name: 'RTL' }),
        baseDiff({
          channel_id: 2,
          current_name: 'Pro7 HD',
          proposed_name: 'Pro7',
          collision: true,
          collision_target_id: 99,
          collision_target_name: 'Pro7',
          suggested_action: 'merge',
        }),
      ],
    });

    render(<NormalizationEngineSection />);
    const btn = await screen.findByTestId('apply-to-channels-btn');
    await act(async () => {
      fireEvent.click(btn);
    });

    const modal = await screen.findByTestId('apply-to-channels-modal');
    expect(modal).toBeInTheDocument();
    const row1 = await screen.findByTestId('apply-row-1');
    const row2 = await screen.findByTestId('apply-row-2');
    expect(within(row1).getByText('RTL RAW')).toBeInTheDocument();
    expect(within(row1).getByText('RTL')).toBeInTheDocument();
    expect(within(row2).getByText('Pro7 HD')).toBeInTheDocument();
    // Collision row cell shows the target name
    const row2Cells = within(row2).getAllByText('Pro7');
    expect(row2Cells.length).toBeGreaterThanOrEqual(1);
  });

  it('lets the user change a per-row action via the dropdown', async () => {
    mockPreview.mockResolvedValue({
      dry_run: true,
      channels_with_changes: 1,
      diffs: [baseDiff({ channel_id: 1 })],
    });

    render(<NormalizationEngineSection />);
    const btn = await screen.findByTestId(
      'apply-to-channels-btn',
      undefined,
      { timeout: 3000 }
    );
    await act(async () => {
      fireEvent.click(btn);
    });
    const select = await screen.findByTestId('apply-action-1');
    // Default seed is 'rename' for non-colliding rows
    expect((select as HTMLSelectElement).value).toBe('rename');
    await act(async () => {
      fireEvent.change(select, { target: { value: 'skip' } });
    });
    expect((select as HTMLSelectElement).value).toBe('skip');
  });

  it('bulk-accepts all non-colliding rows', async () => {
    mockPreview.mockResolvedValue({
      dry_run: true,
      channels_with_changes: 2,
      diffs: [
        baseDiff({ channel_id: 1, collision: false }),
        baseDiff({
          channel_id: 2,
          collision: true,
          collision_target_id: 10,
          collision_target_name: 'X',
          suggested_action: 'merge',
        }),
      ],
    });

    render(<NormalizationEngineSection />);
    const btn = await screen.findByTestId(
      'apply-to-channels-btn',
      undefined,
      { timeout: 3000 }
    );
    await act(async () => {
      fireEvent.click(btn);
    });
    // Colliding rows seed with 'skip', flip it to 'rename' manually first
    const row2 = await screen.findByTestId('apply-action-2');
    await act(async () => {
      fireEvent.change(row2, { target: { value: 'merge' } });
    });
    const row1 = await screen.findByTestId('apply-action-1');
    await act(async () => {
      fireEvent.change(row1, { target: { value: 'skip' } });
    });

    await act(async () => {
      fireEvent.click(screen.getByTestId('apply-to-channels-bulk-accept'));
    });

    expect((row1 as HTMLSelectElement).value).toBe('rename');
    // Colliding row is left alone by the bulk button
    expect((row2 as HTMLSelectElement).value).toBe('merge');
  });

  it('posts the selected actions when Execute is clicked', async () => {
    mockPreview.mockResolvedValue({
      dry_run: true,
      channels_with_changes: 1,
      diffs: [baseDiff({ channel_id: 1 })],
    });
    mockExecute.mockResolvedValue({
      dry_run: false,
      status: 'completed',
      renamed: [{ channel_id: 1, old_name: 'RTL RAW', new_name: 'RTL' }],
      merged: [],
      skipped: [],
      errors: [],
    });

    render(<NormalizationEngineSection />);
    const btn = await screen.findByTestId(
      'apply-to-channels-btn',
      undefined,
      { timeout: 3000 }
    );
    await act(async () => {
      fireEvent.click(btn);
    });

    await screen.findByTestId('apply-row-1');
    await act(async () => {
      fireEvent.click(screen.getByTestId('apply-to-channels-execute'));
    });

    await waitFor(() => {
      expect(mockExecute).toHaveBeenCalledTimes(1);
    });
    const sent = mockExecute.mock.calls[0][0];
    expect(sent).toEqual([
      expect.objectContaining({ channel_id: 1, action: 'rename' }),
    ]);
  });
});
