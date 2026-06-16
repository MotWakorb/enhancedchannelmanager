/**
 * Unit tests for CustomSelect keyboard handling.
 *
 * Focus: GH #489 (bd-jc4v7) — a space typed in the searchable dropdown's
 * search field must insert a literal space, not select the highlighted option.
 * The keydown handler is bound on the container, so search-input keystrokes
 * bubble up to it; Space must only open/select when the trigger is focused.
 */
import { describe, it, expect, vi, beforeAll } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CustomSelect, type SelectOption } from './CustomSelect';

// jsdom does not implement scrollIntoView, which the component calls when the
// highlighted option changes. Stub it so keyboard-driven highlighting works.
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

const OPTIONS: SelectOption[] = [
  { value: 'us-news', label: 'US News' },
  { value: 'us-sports', label: 'US Sports' },
  { value: 'uk-news', label: 'UK News' },
];

function renderSelect(props: Partial<React.ComponentProps<typeof CustomSelect>> = {}) {
  const onChange = vi.fn();
  render(
    <CustomSelect
      options={OPTIONS}
      value=""
      onChange={onChange}
      searchable
      {...props}
    />,
  );
  return { onChange };
}

describe('CustomSelect — searchable space handling (GH #489)', () => {
  it('inserts a space into the search field instead of selecting the highlighted option', async () => {
    const user = userEvent.setup();
    const { onChange } = renderSelect();

    // Open the dropdown; the search input auto-focuses.
    await user.click(screen.getByRole('button'));
    const search = screen.getByPlaceholderText('Search...') as HTMLInputElement;

    // Type a query that needs a space — the exact failure case from the report.
    await user.type(search, 'US News');

    expect(search.value).toBe('US News');
    expect(onChange).not.toHaveBeenCalled();
    // The space-containing query still filters to the matching option, and
    // does NOT match the others — proving the space landed in the input.
    expect(screen.getByText('US News')).toBeInTheDocument();
    expect(screen.queryByText('US Sports')).not.toBeInTheDocument();
  });

  it('selects the highlighted option on Enter while searching', async () => {
    const user = userEvent.setup();
    const { onChange } = renderSelect();

    await user.click(screen.getByRole('button'));
    const search = screen.getByPlaceholderText('Search...');

    // Typing resets the highlight to the first filtered option.
    await user.type(search, 'US News');
    await user.keyboard('{Enter}');

    expect(onChange).toHaveBeenCalledWith('us-news');
  });

  it('opens a closed dropdown when Space is pressed on the trigger', async () => {
    const user = userEvent.setup();
    renderSelect();

    const trigger = screen.getByRole('button');
    trigger.focus();
    await user.keyboard(' ');

    // Search input only renders once the menu is open.
    expect(screen.getByPlaceholderText('Search...')).toBeInTheDocument();
  });
});
