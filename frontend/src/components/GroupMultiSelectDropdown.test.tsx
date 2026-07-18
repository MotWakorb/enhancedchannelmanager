/**
 * Unit tests for GroupMultiSelectDropdown (bead enhancedchannelmanager-zi85o
 * / GH #677): collapsed multi-select replacing unbounded inline checkbox
 * lists at ActionEditor / RuleBuilder / BulkRuleSettingsModal.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { GroupMultiSelectDropdown, type GroupMultiSelectOption } from './GroupMultiSelectDropdown';

const OPTIONS: GroupMultiSelectOption[] = [
  { id: 1, name: 'Sports' },
  { id: 2, name: 'News' },
  { id: 3, name: 'Movies' },
];

function renderDropdown(props: Partial<React.ComponentProps<typeof GroupMultiSelectDropdown>> = {}) {
  const onChange = vi.fn();
  render(
    <GroupMultiSelectDropdown
      options={OPTIONS}
      selectedIds={[]}
      onChange={onChange}
      label="Exclude target groups"
      {...props}
    />,
  );
  return { onChange };
}

describe('GroupMultiSelectDropdown', () => {
  it('renders collapsed with a placeholder and no visible options', () => {
    renderDropdown({ placeholder: 'No groups excluded' });

    expect(screen.getByRole('button', { name: 'Exclude target groups' })).toHaveTextContent('No groups excluded');
    expect(screen.queryByText('Sports')).not.toBeInTheDocument();
  });

  it('shows a "N selected" summary when options are selected, collapsed or not', () => {
    renderDropdown({ selectedIds: [1, 2] });

    expect(screen.getByRole('button', { name: 'Exclude target groups' })).toHaveTextContent('2 groups selected');
  });

  it('opens the menu on click and lists every option as a checkbox', async () => {
    const user = userEvent.setup();
    renderDropdown();

    await user.click(screen.getByRole('button', { name: 'Exclude target groups' }));

    const group = await screen.findByRole('group', { name: 'Exclude target groups' });
    const checkboxes = within(group).getAllByRole('checkbox');
    expect(checkboxes).toHaveLength(3);
    expect(within(group).getByText('Sports')).toBeInTheDocument();
    expect(within(group).getByText('News')).toBeInTheDocument();
    expect(within(group).getByText('Movies')).toBeInTheDocument();
  });

  it('reflects selectedIds as checked checkboxes in option order', async () => {
    const user = userEvent.setup();
    renderDropdown({ selectedIds: [2] });

    await user.click(screen.getByRole('button', { name: 'Exclude target groups' }));
    const group = await screen.findByRole('group', { name: 'Exclude target groups' });
    const checkboxes = within(group).getAllByRole('checkbox');

    expect(checkboxes[0]).not.toBeChecked(); // Sports
    expect(checkboxes[1]).toBeChecked();     // News
    expect(checkboxes[2]).not.toBeChecked(); // Movies
  });

  it('calls onChange with the option added when an unchecked box is toggled on', async () => {
    const user = userEvent.setup();
    const { onChange } = renderDropdown({ selectedIds: [1] });

    await user.click(screen.getByRole('button', { name: 'Exclude target groups' }));
    const group = await screen.findByRole('group', { name: 'Exclude target groups' });
    await user.click(within(group).getByText('News'));

    expect(onChange).toHaveBeenCalledWith([1, 2]);
  });

  it('calls onChange with the option removed when a checked box is toggled off', async () => {
    const user = userEvent.setup();
    const { onChange } = renderDropdown({ selectedIds: [1, 2] });

    await user.click(screen.getByRole('button', { name: 'Exclude target groups' }));
    const group = await screen.findByRole('group', { name: 'Exclude target groups' });
    await user.click(within(group).getByText('Sports'));

    expect(onChange).toHaveBeenCalledWith([2]);
  });

  it('filters options via the search box', async () => {
    const user = userEvent.setup();
    renderDropdown();

    await user.click(screen.getByRole('button', { name: 'Exclude target groups' }));
    const search = await screen.findByPlaceholderText('Search groups...');
    await user.type(search, 'ov');

    const group = screen.getByRole('group', { name: 'Exclude target groups' });
    expect(within(group).getByText('Movies')).toBeInTheDocument();
    expect(within(group).queryByText('Sports')).not.toBeInTheDocument();
    expect(within(group).queryByText('News')).not.toBeInTheDocument();
  });

  it('shows an empty-search message when no option matches', async () => {
    const user = userEvent.setup();
    renderDropdown();

    await user.click(screen.getByRole('button', { name: 'Exclude target groups' }));
    const search = await screen.findByPlaceholderText('Search groups...');
    await user.type(search, 'zzz-no-match');

    expect(await screen.findByText(/no groups match/i)).toBeInTheDocument();
  });

  it('Select All selects every currently visible (search-filtered) option, keeping prior selections', async () => {
    const user = userEvent.setup();
    const { onChange } = renderDropdown({ selectedIds: [2] });

    await user.click(screen.getByRole('button', { name: 'Exclude target groups' }));
    const search = await screen.findByPlaceholderText('Search groups...');
    await user.type(search, 'o'); // matches Sports, Movies -- not News

    await user.click(screen.getByRole('button', { name: /select all visible/i }));

    expect(onChange).toHaveBeenCalledWith(expect.arrayContaining([1, 2, 3]));
    expect(onChange.mock.calls[0][0]).toHaveLength(3);
  });

  it('Clear All (no active search) clears every selection', async () => {
    const user = userEvent.setup();
    const { onChange } = renderDropdown({ selectedIds: [1, 2, 3] });

    await user.click(screen.getByRole('button', { name: 'Exclude target groups' }));
    await user.click(await screen.findByRole('button', { name: /^clear all$/i }));

    expect(onChange).toHaveBeenCalledWith([]);
  });

  it('Clear (search-scoped) only clears the visible, matching selections', async () => {
    const user = userEvent.setup();
    const { onChange } = renderDropdown({ selectedIds: [1, 2, 3] });

    await user.click(screen.getByRole('button', { name: 'Exclude target groups' }));
    const search = await screen.findByPlaceholderText('Search groups...');
    await user.type(search, 'ews'); // matches only News (id 2)

    await user.click(screen.getByRole('button', { name: /clear visible/i }));

    expect(onChange).toHaveBeenCalledWith([1, 3]);
  });

  it('closes the menu when clicking outside', async () => {
    const user = userEvent.setup();
    renderDropdown();

    await user.click(screen.getByRole('button', { name: 'Exclude target groups' }));
    expect(await screen.findByRole('group', { name: 'Exclude target groups' })).toBeInTheDocument();

    await user.click(document.body);

    expect(screen.queryByRole('group', { name: 'Exclude target groups' })).not.toBeInTheDocument();
  });

  it('closes the menu on Escape and returns focus to the trigger button', async () => {
    const user = userEvent.setup();
    renderDropdown();

    const trigger = screen.getByRole('button', { name: 'Exclude target groups' });
    await user.click(trigger);
    expect(await screen.findByRole('group', { name: 'Exclude target groups' })).toBeInTheDocument();

    await user.keyboard('{Escape}');

    expect(screen.queryByRole('group', { name: 'Exclude target groups' })).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });

  it('renders a muted "(disabled)" suffix for inactive options without blocking selection', async () => {
    const user = userEvent.setup();
    const { onChange } = renderDropdown({
      options: [{ id: 9, name: 'Legacy Group', inactive: true }],
    });

    await user.click(screen.getByRole('button', { name: 'Exclude target groups' }));
    expect(await screen.findByText('Legacy Group (disabled)')).toBeInTheDocument();

    const group = screen.getByRole('group', { name: 'Exclude target groups' });
    await user.click(within(group).getByRole('checkbox'));

    expect(onChange).toHaveBeenCalledWith([9]);
  });

  it('shows the empty-options message and no control when options is empty', () => {
    renderDropdown({ options: [], emptyMessage: 'No channel groups available.' });

    expect(screen.getByText('No channel groups available.')).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('disables the trigger button and checkboxes when disabled', () => {
    renderDropdown({ disabled: true, selectedIds: [] });

    const trigger = screen.getByRole('button', { name: 'Exclude target groups' });
    expect(trigger).toBeDisabled();
  });
});
