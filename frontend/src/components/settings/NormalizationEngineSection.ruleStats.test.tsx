/**
 * Tests for the rule-match-count stats feature (enhancedchannelmanager-hq3de.e)
 * added to NormalizationEngineSection. Mirrors the harness in
 * NormalizationEngineSection.applyToChannels.test.tsx — a stable
 * useNotifications mock (the real hook is useMemo'd; a fresh object per call
 * would make loadData's useCallback unstable and re-fire its effect forever).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import { NormalizationEngineSection } from './NormalizationEngineSection';

const mockSuccess = vi.fn();
const mockError = vi.fn();
const stableNotifications = {
  success: mockSuccess,
  error: mockError,
  warning: vi.fn(),
  info: vi.fn(),
  notify: vi.fn(),
  dismiss: vi.fn(),
  dismissAll: vi.fn(),
};
vi.mock('../../contexts/NotificationContext', () => ({
  useNotifications: () => stableNotifications,
}));

const mockGetRules = vi.fn();
const mockGetTags = vi.fn();
const mockGetRuleStats = vi.fn();
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
  previewApplyNormalizationToChannels: vi.fn(),
  executeApplyNormalizationToChannels: vi.fn(),
  getNormalizationRuleStats: (...args: unknown[]) => mockGetRuleStats(...args),
}));

const oneGroupWithRule = {
  groups: [
    {
      id: 1,
      name: 'Prefixes',
      description: null,
      enabled: true,
      priority: 0,
      is_builtin: false,
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
      rules: [
        {
          id: 10,
          group_id: 1,
          name: 'Strip US:',
          description: null,
          enabled: true,
          priority: 0,
          condition_type: 'starts_with' as const,
          condition_value: 'US:',
          case_sensitive: false,
          tag_group_id: null,
          tag_match_position: null,
          require_delimiter: false,
          tag_group_name: null,
          conditions: null,
          condition_logic: 'AND' as const,
          action_type: 'strip_prefix' as const,
          action_value: null,
          else_action_type: null,
          else_action_value: null,
          stop_processing: false,
          is_builtin: false,
          created_at: '2026-01-01T00:00:00Z',
          updated_at: '2026-01-01T00:00:00Z',
        },
      ],
    },
  ],
};

describe('NormalizationEngineSection — rule stats (bead hq3de.e)', () => {
  beforeEach(() => {
    mockGetRules.mockClear();
    mockGetTags.mockClear();
    mockGetRuleStats.mockClear();
    mockSuccess.mockClear();
    mockError.mockClear();
    mockGetRules.mockResolvedValue(oneGroupWithRule);
    mockGetTags.mockResolvedValue({ groups: [] });
  });

  it('renders a Rule Stats button that is not loaded by default', async () => {
    render(<NormalizationEngineSection />);
    await waitFor(() => {
      expect(screen.getByTestId('rule-stats-btn')).toBeInTheDocument();
    });
    expect(mockGetRuleStats).not.toHaveBeenCalled();
  });

  it('loads and displays match-count badges on click, expanding the group first', async () => {
    mockGetRuleStats.mockResolvedValue({
      rule_stats: [
        { rule_id: 10, rule_name: 'Strip US:', group_id: 1, group_name: 'Prefixes', enabled: true, match_count: 42, match_percentage: 8.4 },
      ],
      total_streams_tested: 500,
      total_rules: 1,
    });

    render(<NormalizationEngineSection />);
    const btn = await screen.findByTestId('rule-stats-btn');

    // Expand the group so the rule row renders.
    fireEvent.click(screen.getByText('Prefixes'));
    await screen.findByText('Strip US:');

    fireEvent.click(btn);

    await waitFor(() => {
      expect(mockGetRuleStats).toHaveBeenCalled();
    });
    await waitFor(() => {
      expect(screen.getByText('42 matches')).toBeInTheDocument();
    });
  });

  it('hides badges again on a second click without re-fetching', async () => {
    mockGetRuleStats.mockResolvedValue({
      rule_stats: [
        { rule_id: 10, rule_name: 'Strip US:', group_id: 1, group_name: 'Prefixes', enabled: true, match_count: 1, match_percentage: 0.2 },
      ],
      total_streams_tested: 500,
      total_rules: 1,
    });

    render(<NormalizationEngineSection />);
    const btn = await screen.findByTestId('rule-stats-btn');
    fireEvent.click(screen.getByText('Prefixes'));
    await screen.findByText('Strip US:');

    fireEvent.click(btn);
    await waitFor(() => expect(screen.getByText('1 match')).toBeInTheDocument());

    fireEvent.click(btn);
    await waitFor(() => expect(screen.queryByText('1 match')).not.toBeInTheDocument());

    expect(mockGetRuleStats).toHaveBeenCalledTimes(1);
  });

  it('shows an error notification when the stats fetch fails', async () => {
    mockGetRuleStats.mockRejectedValue(new Error('boom'));

    render(<NormalizationEngineSection />);
    const btn = await screen.findByTestId('rule-stats-btn');
    fireEvent.click(btn);

    await waitFor(() => {
      expect(mockError).toHaveBeenCalledWith('boom', 'Normalization');
    });
  });
});
