import { describe, it, expect, vi } from 'vitest';
import { render, screen, within, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ChannelPipelineRule } from '../../types/channelPipeline';
import { BulkRuleSettingsModal } from './BulkRuleSettingsModal';
import { getNormalizationRules } from '../../services/api';

vi.mock('../ModalOverlay', () => ({
  ModalOverlay: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock('../CustomSelect', () => ({
  CustomSelect: ({
    options,
    value,
    onChange,
    disabled,
  }: {
    options: { value: string; label: string; disabled?: boolean }[];
    value: string;
    onChange: (v: string) => void;
    disabled?: boolean;
  }) => (
    <select
      aria-label="custom-select"
      disabled={disabled}
      value={value}
      onChange={(e) => onChange(e.target.value)}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value} disabled={o.disabled}>
          {o.label}
        </option>
      ))}
    </select>
  ),
}));

vi.mock('../../services/api', () => ({
  getNormalizationRules: vi.fn().mockResolvedValue({ groups: [] }),
}));

function mkRule(overrides: Partial<ChannelPipelineRule>): ChannelPipelineRule {
  return {
    id: overrides.id ?? 1,
    name: overrides.name ?? 'Rule',
    description: undefined,
    enabled: overrides.enabled ?? true,
    priority: 0,
    m3u_account_id: undefined,
    target_group_id: undefined,
    conditions: [],
    actions: [],
    run_on_refresh: false,
    stop_on_first_match: true,
    sort_field: null,
    sort_order: 'asc',
    probe_on_sort: false,
    sort_regex: null,
    stream_sort_field: 'smart_sort',
    stream_sort_order: 'asc',
    normalization_group_ids: [],
    skip_struck_streams: false,
    orphan_action: 'delete',
    match_scope_target_group: false,
    match_count: 0,
    created_at: '2026-04-22T00:00:00Z',
    updated_at: '2026-04-22T00:00:00Z',
    ...overrides,
  };
}

describe('BulkRuleSettingsModal', () => {
  it('blocks apply when channel + stream quality probe mismatch', async () => {
    const user = userEvent.setup();
    const onApply = vi.fn().mockResolvedValue(undefined);

    render(
      <BulkRuleSettingsModal
        isOpen
        onClose={() => {}}
        selectedRuleIds={[1, 2]}
        rules={[mkRule({ id: 1 }), mkRule({ id: 2 })]}
        onApply={onApply}
      />,
    );

    // Apply channel sort, set Quality, check probe=true
    await user.click(screen.getByText('Apply channel sort'));
    const selects1 = screen.getAllByRole('combobox');
    const channelSortSelect = selects1.find((s) => s.textContent?.includes('No sorting (keep manual numbers)'));
    expect(channelSortSelect).toBeTruthy();
    await user.selectOptions(channelSortSelect!, 'quality');

    // Probe checkbox exists in channel section now.
    const probeCheckboxes = screen.getAllByRole('checkbox', { name: /probe unprobed streams/i });
    // First occurrence is channel sort probe.
    await user.click(probeCheckboxes[0]);

    // Apply stream sort, set Quality, leave stream probe unchecked (default false) => mismatch.
    await user.click(screen.getByText('Apply stream sort'));
    const selects2 = screen.getAllByRole('combobox');
    const streamSortSelect = selects2.find((s) => s.textContent?.includes('Smart Sort (default)'));
    expect(streamSortSelect).toBeTruthy();
    await user.selectOptions(streamSortSelect!, 'quality');

    await user.click(screen.getByRole('button', { name: /apply to selected/i }));

    expect(
      screen.getByText(/Channel sort and stream sort are both applying Quality probing/i),
    ).toBeInTheDocument();
    expect(onApply).not.toHaveBeenCalled();
  });

  // bead enhancedchannelmanager-zi85o / GH #677: the normalization-groups
  // checkbox list is now a GroupMultiSelectDropdown, collapsed by default.
  it('opens the normalization-groups picker and selects a group', async () => {
    const user = userEvent.setup();
    vi.mocked(getNormalizationRules).mockResolvedValueOnce({
      groups: [
        {
          id: 1, name: 'Sports Cleanup', description: null, enabled: true,
          priority: 0, is_builtin: false, created_at: '2026-04-22T00:00:00Z', updated_at: '2026-04-22T00:00:00Z',
        },
        {
          id: 2, name: 'Legacy Rules', description: null, enabled: false,
          priority: 1, is_builtin: false, created_at: '2026-04-22T00:00:00Z', updated_at: '2026-04-22T00:00:00Z',
        },
      ],
    });
    const onApply = vi.fn().mockResolvedValue(undefined);

    render(
      <BulkRuleSettingsModal
        isOpen
        onClose={() => {}}
        selectedRuleIds={[1]}
        rules={[mkRule({ id: 1 })]}
        onApply={onApply}
      />,
    );

    await user.click(screen.getByText('Apply normalization groups'));
    const trigger = await screen.findByRole('button', { name: 'Normalization Groups' });
    expect(screen.queryByText('Sports Cleanup')).not.toBeInTheDocument();

    await user.click(trigger);
    const group = await screen.findByRole('group', { name: 'Normalization Groups' });
    expect(within(group).getByText('Legacy Rules (disabled)')).toBeInTheDocument();
    await user.click(within(group).getByText('Sports Cleanup'));

    await user.click(screen.getByRole('button', { name: /apply to selected/i }));

    await waitFor(() => {
      expect(onApply).toHaveBeenCalledWith(
        [1],
        expect.objectContaining({ normalization_group_ids: [1] }),
      );
    });
  });
});

