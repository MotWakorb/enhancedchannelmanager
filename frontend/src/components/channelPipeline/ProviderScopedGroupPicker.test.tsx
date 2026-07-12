import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ProviderScopedGroupPicker } from './ProviderScopedGroupPicker';
import {
  joinProviderRows,
  type GroupProviderRow,
} from './providerScopedGroups';
import type { ProviderGroupScopeRow } from '../../services/api';

const NAMES: Record<number, string> = { 10: 'MLB PPV', 20: 'NBA', 30: 'NFL' };
const groupName = (id: number) => NAMES[id];

function raw(over: Partial<ProviderGroupScopeRow>): ProviderGroupScopeRow {
  return {
    m3u_account_id: 3,
    m3u_account_name: 'Provider A',
    channel_group_id: 10,
    auto_channel_sync: true,
    enabled: true,
    stream_count: 40,
    ...over,
  };
}

// MLB PPV under two providers (3 ON, 7 OFF); NBA under one provider.
const ROWS: GroupProviderRow[] = joinProviderRows(
  [
    raw({ m3u_account_id: 3, m3u_account_name: 'Provider A', channel_group_id: 10, auto_channel_sync: true }),
    raw({ m3u_account_id: 7, m3u_account_name: 'Provider B', channel_group_id: 10, auto_channel_sync: false, stream_count: 38 }),
    raw({ m3u_account_id: 7, m3u_account_name: 'Provider B', channel_group_id: 20, auto_channel_sync: false, stream_count: 52 }),
  ],
  groupName,
);

describe('joinProviderRows', () => {
  it('joins names and drops rows for unknown groups', () => {
    const rows = joinProviderRows(
      [raw({ channel_group_id: 10 }), raw({ channel_group_id: 999 })],
      groupName,
    );
    expect(rows).toHaveLength(1);
    expect(rows[0].groupName).toBe('MLB PPV');
  });
});

describe('ProviderScopedGroupPicker', () => {
  it('renders a single-provider group as a flat row (no expander)', () => {
    render(
      <ProviderScopedGroupPicker role="secondary" rows={ROWS} value={[]}
        onChange={vi.fn()} showAll={false} />,
    );
    // NBA is single-provider → a flat checkbox, not an expand button.
    expect(screen.getByTestId('psg-secondary-20-7')).toBeInTheDocument();
  });

  it('expands a multi-provider group into per-provider + whole-group rows', async () => {
    const user = userEvent.setup();
    render(
      <ProviderScopedGroupPicker role="secondary" rows={ROWS} value={[]}
        onChange={vi.fn()} showAll={false} />,
    );
    await user.click(screen.getByRole('button', { name: /MLB PPV/i }));
    expect(screen.getByTestId('psg-secondary-10-3')).toBeInTheDocument();
    expect(screen.getByTestId('psg-secondary-10-7')).toBeInTheDocument();
    expect(screen.getByTestId('psg-secondary-10-any')).toBeInTheDocument();
  });

  it('master select emits a single scope', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ProviderScopedGroupPicker role="master" rows={ROWS} value={null}
        onChange={onChange} showAll={false} />,
    );
    // The multi-provider node auto-expands (contains a mismatch), so the
    // radios are visible.
    await user.click(screen.getByTestId('psg-master-10-3'));
    expect(onChange).toHaveBeenCalledWith({ group_id: 10, m3u_account_id: 3 });
  });

  it('secondary select accumulates a scope list', async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <ProviderScopedGroupPicker role="secondary" rows={ROWS}
        value={[{ group_id: 20, m3u_account_id: 7 }]} onChange={onChange}
        showAll={false} />,
    );
    await user.click(screen.getByRole('button', { name: /MLB PPV/i }));
    await user.click(screen.getByTestId('psg-secondary-10-7'));
    expect(onChange).toHaveBeenCalledWith([
      { group_id: 20, m3u_account_id: 7 },
      { group_id: 10, m3u_account_id: 7 },
    ]);
  });

  it('role-relative valence: auto-sync ON is OK for master, a mismatch for secondary', async () => {
    const user = userEvent.setup();
    // Provider A (group 10) is auto-sync ON.
    const { rerender } = render(
      <ProviderScopedGroupPicker role="master" rows={ROWS} value={null}
        onChange={vi.fn()} showAll={false} />,
    );
    await user.click(screen.getByRole('button', { name: /MLB PPV/i }));
    const masterRowA = screen.getByTestId('psg-master-10-3').closest('li')!;
    expect(within(masterRowA).getByText(/Auto-sync ON ✓/)).toBeInTheDocument();

    rerender(
      <ProviderScopedGroupPicker role="secondary" rows={ROWS} value={[]}
        onChange={vi.fn()} showAll={false} />,
    );
    await user.click(screen.getByRole('button', { name: /MLB PPV/i }));
    const secRowA = screen.getByTestId('psg-secondary-10-3').closest('li')!;
    expect(within(secRowA).getByText(/Auto-sync ON ⚠/)).toBeInTheDocument();
  });

  it('excludes the master scope but allows the same group under a different provider', async () => {
    const user = userEvent.setup();
    render(
      <ProviderScopedGroupPicker role="secondary" rows={ROWS} value={[]}
        onChange={vi.fn()} showAll={false}
        excludeScope={{ group_id: 10, m3u_account_id: 3 }} />,
    );
    await user.click(screen.getByRole('button', { name: /MLB PPV/i }));
    // Provider A (master) is disabled; Provider B (same group) is selectable.
    expect(screen.getByTestId('psg-secondary-10-3')).toBeDisabled();
    expect(screen.getByTestId('psg-secondary-10-7')).not.toBeDisabled();
  });

  it('hides a disabled provider row unless showAll or it is already referenced', async () => {
    const disabledRows = joinProviderRows(
      [
        raw({ m3u_account_id: 3, channel_group_id: 10, enabled: true }),
        raw({ m3u_account_id: 7, m3u_account_name: 'Provider B', channel_group_id: 10, enabled: false, auto_channel_sync: false }),
      ],
      groupName,
    );
    const user = userEvent.setup();
    const { rerender } = render(
      <ProviderScopedGroupPicker role="secondary" rows={disabledRows} value={[]}
        onChange={vi.fn()} showAll={false} />,
    );
    await user.click(screen.getByRole('button', { name: /MLB PPV/i }));
    expect(screen.queryByTestId('psg-secondary-10-7')).not.toBeInTheDocument();

    // Referenced (selected) -> stays visible even when disabled + showAll off.
    rerender(
      <ProviderScopedGroupPicker role="secondary" rows={disabledRows}
        value={[{ group_id: 10, m3u_account_id: 7 }]} onChange={vi.fn()}
        showAll={false} />,
    );
    expect(screen.getByTestId('psg-secondary-10-7')).toBeInTheDocument();
  });
});
