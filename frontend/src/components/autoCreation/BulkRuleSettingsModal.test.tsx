import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { AutoCreationRule } from '../../types/autoCreation';
import { BulkRuleSettingsModal } from './BulkRuleSettingsModal';

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

function mkRule(overrides: Partial<AutoCreationRule>): AutoCreationRule {
  return {
    id: overrides.id ?? 1,
    name: overrides.name ?? 'Rule',
    description: null,
    enabled: overrides.enabled ?? true,
    priority: 0,
    m3u_account_id: null,
    target_group_id: null,
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
    managed_channel_ids: [],
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
});

