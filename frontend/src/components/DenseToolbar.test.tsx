import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { DenseToolbar } from './DenseToolbar';

describe('DenseToolbar', () => {
  it('renders semantic groups in the approved visual and keyboard order', async () => {
    const user = userEvent.setup();
    render(<DenseToolbar
      label="Inventory controls"
      search={<input aria-label="Search inventory" />}
      filters={<button>Filters</button>}
      sortView={<button>Sort</button>}
      selection={<output>2 selected</output>}
      bulkActions={<button>Delete selected</button>}
      secondaryActions={<button>More actions</button>}
    />);
    expect(screen.getByRole('toolbar', { name: 'Inventory controls' })).toBeVisible();
    const expected = ['Search inventory', 'Filters', 'Sort', 'Delete selected', 'More actions'];
    for (const name of expected) {
      await user.tab();
      expect(screen.getByRole(name === 'Search inventory' ? 'textbox' : 'button', { name })).toHaveFocus();
    }
  });

  it('omits unsupported groups without changing the remaining order', () => {
    render(<DenseToolbar label="Filter-only controls" filters={<button>Filter</button>} secondaryActions={<button>Refresh</button>} />);
    expect(screen.queryByRole('group', { name: 'search' })).not.toBeInTheDocument();
    expect(screen.getAllByRole('button').map((button) => button.textContent)).toEqual(['Filter', 'Refresh']);
  });

  it('exposes selection in text and retains disabled bulk actions', () => {
    render(<DenseToolbar
      label="Selection controls"
      selection={<output aria-live="polite">0 selected</output>}
      bulkActions={<button disabled>Delete selected</button>}
    />);
    expect(screen.getByText('0 selected')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Delete selected' })).toBeDisabled();
  });
});
