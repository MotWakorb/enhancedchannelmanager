/**
 * Unit tests for CustomSelect keyboard handling.
 *
 * Focus: GH #489 (bd-jc4v7) — a space typed in the searchable dropdown's
 * search field must insert a literal space, not select the highlighted option.
 * The keydown handler is bound on the container, so search-input keystrokes
 * bubble up to it; Space must only open/select when the trigger is focused.
 */
import { afterEach, beforeEach, describe, it, expect, vi, beforeAll } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CustomSelect, type SelectOption } from './CustomSelect';

// jsdom does not implement scrollIntoView, which the component calls when the
// highlighted option changes. Stub it so keyboard-driven highlighting works.
beforeAll(() => {
  Element.prototype.scrollIntoView = vi.fn();
});

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

describe('CustomSelect — viewport placement', () => {
  it('uses its trigger id for a visible label relationship', () => {
    render(
      <>
        <label htmlFor="smart-sort-condition">Condition</label>
        <CustomSelect
          id="smart-sort-condition"
          options={OPTIONS}
          value="us-news"
          onChange={vi.fn()}
        />
      </>,
    );

    expect(screen.getByRole('button', { name: 'Condition' })).toHaveAttribute(
      'id',
      'smart-sort-condition',
    );
  });

  it('opens upward near the bottom of the viewport and clamps to its 250px cap', async () => {
    const user = userEvent.setup();
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 600 });
    vi.mocked(HTMLElement.prototype.getBoundingClientRect).mockReturnValue({
      x: 24, y: 500, top: 500, right: 244, bottom: 536, left: 24, width: 220, height: 36,
      toJSON: () => ({}),
    } as DOMRect);
    renderSelect();

    await user.click(screen.getByRole('button'));

    await waitFor(() => expect(document.querySelector('.custom-select-menu')).toHaveStyle({
      top: '246px',
      maxHeight: '250px',
    }));
  });

  it('connects the trigger to the portaled listbox and restores focus on Escape', async () => {
    const user = userEvent.setup();
    renderSelect();
    const trigger = screen.getByRole('button');

    await user.click(trigger);

    const listbox = screen.getByRole('listbox');
    expect(trigger).toHaveAttribute('aria-controls', listbox.id);
    expect(screen.getByRole('textbox', { name: 'Search options' })).toBeInTheDocument();

    await user.keyboard('{Escape}');
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});
