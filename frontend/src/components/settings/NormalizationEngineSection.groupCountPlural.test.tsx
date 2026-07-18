/**
 * Tests for the "1 rules" singular/plural bug (bead enhancedchannelmanager-09x38.15
 * item 5) in the normalization groups list group-count chip. Harness mirrors
 * NormalizationEngineSection.ruleStats.test.tsx — a stable useNotifications
 * mock is required because the real hook is useMemo'd and a fresh object per
 * render call would make loadData's useCallback unstable.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';

import { NormalizationEngineSection } from './NormalizationEngineSection';

const stableNotifications = {
  success: vi.fn(),
  error: vi.fn(),
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
  getNormalizationRuleStats: vi.fn(),
}));

function makeGroup(overrides: { id: number; name: string; ruleCount: number }) {
  return {
    id: overrides.id,
    name: overrides.name,
    description: null,
    enabled: true,
    priority: 0,
    is_builtin: false,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    rules: Array.from({ length: overrides.ruleCount }, (_, i) => ({
      id: overrides.id * 100 + i,
      group_id: overrides.id,
      name: `Rule ${i}`,
      description: null,
      enabled: true,
      priority: i,
      condition_type: 'starts_with' as const,
      condition_value: 'X',
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
    })),
  };
}

describe('NormalizationEngineSection — group rule-count pluralization (bead 09x38.15 item 5)', () => {
  beforeEach(() => {
    mockGetRules.mockClear();
    mockGetTags.mockClear();
    mockGetTags.mockResolvedValue({ groups: [] });
  });

  it('renders "1 rule" (singular) for a group with exactly one rule', async () => {
    mockGetRules.mockResolvedValue({
      groups: [makeGroup({ id: 1, name: 'Solo Group', ruleCount: 1 })],
    });

    render(<NormalizationEngineSection />);

    expect(await screen.findByText('1 rule')).toBeInTheDocument();
    expect(screen.queryByText('1 rules')).not.toBeInTheDocument();
  });

  it('renders "N rules" (plural) for a group with zero or multiple rules', async () => {
    mockGetRules.mockResolvedValue({
      groups: [
        makeGroup({ id: 1, name: 'Empty Group', ruleCount: 0 }),
        makeGroup({ id: 2, name: 'Multi Group', ruleCount: 3 }),
      ],
    });

    render(<NormalizationEngineSection />);

    expect(await screen.findByText('0 rules')).toBeInTheDocument();
    expect(screen.getByText('3 rules')).toBeInTheDocument();
  });
});
