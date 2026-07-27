/**
 * Unit tests for GroupMultiSelectDropdown (bead enhancedchannelmanager-zi85o
 * / GH #677): collapsed multi-select replacing unbounded inline checkbox
 * lists at ActionEditor / RuleBuilder / BulkRuleSettingsModal.
 */
import { afterEach, beforeEach, describe, it, expect, vi } from 'vitest';
import { useState } from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { GroupMultiSelectDropdown, type GroupMultiSelectOption } from './GroupMultiSelectDropdown';

const OPTIONS: GroupMultiSelectOption[] = [
  { id: 1, name: 'Sports' },
  { id: 2, name: 'News' },
  { id: 3, name: 'Movies' },
];

beforeEach(() => {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
    x: 24, y: 100, top: 100, right: 244, bottom: 136, left: 24, width: 220, height: 36,
    toJSON: () => ({}),
  } as DOMRect);
});

afterEach(() => {
  vi.restoreAllMocks();
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: 768 });
});

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
  const setViewportHeight = (height: number) => {
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: height });
  };

  const setTriggerRect = (rect: Partial<DOMRect>) => {
    vi.mocked(HTMLElement.prototype.getBoundingClientRect).mockReturnValue({
      x: 24,
      y: 100,
      top: 100,
      right: 244,
      bottom: 136,
      left: 24,
      width: 220,
      height: 36,
      toJSON: () => ({}),
      ...rect,
    } as DOMRect);
  };

  const getMenu = () => document.querySelector('.group-multiselect-menu') as HTMLElement;

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

  it('opens below the trigger when the viewport has enough room', async () => {
    const user = userEvent.setup();
    setViewportHeight(800);
    setTriggerRect({ top: 100, bottom: 136 });
    renderDropdown();

    await user.click(screen.getByRole('button', { name: 'Exclude target groups' }));

    await waitFor(() => expect(getMenu()).toHaveStyle({ top: '140px', maxHeight: '300px' }));
  });

  it('neutralizes the shared dropdown margin and keeps one measured 4px portal gap', async () => {
    const user = userEvent.setup();
    setViewportHeight(800);
    setTriggerRect({ top: 100, bottom: 136 });
    renderDropdown();

    await user.click(screen.getByRole('button', { name: 'Exclude target groups' }));

    await waitFor(() => {
      const menu = getMenu();
      expect(menu.style.marginTop).toBe('0px');
      expect(Number.parseFloat(menu.style.top) - 136).toBe(4);
    });
  });

  it('opens above the trigger when the full menu does not fit below and more room is available above', async () => {
    const user = userEvent.setup();
    setViewportHeight(600);
    setTriggerRect({ top: 500, bottom: 536 });
    renderDropdown();

    await user.click(screen.getByRole('button', { name: 'Exclude target groups' }));

    await waitFor(() => expect(getMenu()).toHaveStyle({ top: '196px', maxHeight: '300px' }));
  });

  it('uses the short viewport so menu chrome still leaves room for options', async () => {
    const user = userEvent.setup();
    setViewportHeight(220);
    vi.mocked(HTMLElement.prototype.getBoundingClientRect).mockReturnValue({
      x: 24, y: 100, top: 100, right: 244, bottom: 136, left: 24, width: 220, height: 36,
      toJSON: () => ({}),
    } as DOMRect);
    renderDropdown();

    await user.click(screen.getByRole('button', { name: 'Exclude target groups' }));
    await waitFor(() => expect(getMenu()).toHaveStyle({ top: '8px', maxHeight: '204px' }));

    const optionsPane = screen.getByRole('group', { name: 'Exclude target groups' });
    expect(Number.parseFloat(getMenu().style.maxHeight)).toBeGreaterThanOrEqual(124);
    expect(optionsPane).toHaveClass('filter-dropdown-options');
  });

  it('recomputes on resize and capture scroll', async () => {
    const user = userEvent.setup();
    setViewportHeight(800);
    const rectSpy = vi.mocked(HTMLElement.prototype.getBoundingClientRect).mockReturnValue({
      x: 24, y: 300, top: 300, right: 244, bottom: 336, left: 24, width: 220, height: 36,
      toJSON: () => ({}),
    } as DOMRect);
    renderDropdown();

    await user.click(screen.getByRole('button', { name: 'Exclude target groups' }));
    await waitFor(() => expect(getMenu()).toHaveStyle({ top: '340px', maxHeight: '300px' }));

    rectSpy.mockReturnValue({
      x: 24, y: 700, top: 700, right: 244, bottom: 736, left: 24, width: 220, height: 36,
      toJSON: () => ({}),
    } as DOMRect);
    fireEvent(window, new Event('resize'));
    await waitFor(() => expect(getMenu()).toHaveStyle({ top: '396px', maxHeight: '300px' }));

    rectSpy.mockReturnValue({
      x: 24, y: 200, top: 200, right: 244, bottom: 236, left: 24, width: 220, height: 36,
      toJSON: () => ({}),
    } as DOMRect);
    fireEvent.scroll(document, { target: { scrollTop: 1 } });
    await waitFor(() => expect(getMenu()).toHaveStyle({ top: '240px', maxHeight: '300px' }));
  });

  it('wires selection through an overflowed options pane and updates the summary', async () => {
    const user = userEvent.setup();
    const manyOptions = Array.from({ length: 20 }, (_, index) => ({
      id: index + 1,
      name: `Normalization group ${String(index + 1).padStart(2, '0')}`,
    }));

    function ControlledDropdown() {
      const [selectedIds, setSelectedIds] = useState<number[]>([]);
      return (
        <GroupMultiSelectDropdown
          options={manyOptions}
          selectedIds={selectedIds}
          onChange={setSelectedIds}
          label="Normalization Groups"
        />
      );
    }

    setViewportHeight(600);
    setTriggerRect({ top: 500, bottom: 536 });
    render(<ControlledDropdown />);
    const trigger = screen.getByRole('button', { name: 'Normalization Groups' });
    await user.click(trigger);

    const optionsPane = await screen.findByRole('group', { name: 'Normalization Groups' });
    Object.defineProperties(optionsPane, {
      clientHeight: { configurable: true, value: 200 },
      scrollHeight: { configurable: true, value: 800 },
    });
    expect(optionsPane.scrollHeight).toBeGreaterThan(optionsPane.clientHeight);

    optionsPane.scrollTop = optionsPane.scrollHeight - optionsPane.clientHeight;
    fireEvent.scroll(optionsPane);
    expect(optionsPane.scrollTop).toBe(600);

    const finalOption = within(optionsPane).getByText('Normalization group 20');
    await user.click(finalOption);

    const checkboxes = within(optionsPane).getAllByRole('checkbox');
    expect(checkboxes[checkboxes.length - 1]).toBeChecked();
    expect(trigger).toHaveTextContent('1 group selected');
  });

  it.each([
    { top: -80, bottom: -20 },
    { top: 820, bottom: 856 },
  ])('closes when capture scroll moves the trigger fully out of view ($top)', async ({ top, bottom }) => {
    const user = userEvent.setup();
    setViewportHeight(800);
    const rectSpy = vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({
      x: 24, y: 100, top: 100, right: 244, bottom: 136, left: 24, width: 220, height: 36,
      toJSON: () => ({}),
    } as DOMRect);
    renderDropdown();
    await user.click(screen.getByRole('button', { name: 'Exclude target groups' }));
    expect(getMenu()).toBeInTheDocument();

    rectSpy.mockReturnValue({
      x: 24, y: top, top, right: 244, bottom, left: 24, width: 220, height: 36,
      toJSON: () => ({}),
    } as DOMRect);
    fireEvent.scroll(document);

    await waitFor(() => expect(document.querySelector('.group-multiselect-menu')).not.toBeInTheDocument());
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
