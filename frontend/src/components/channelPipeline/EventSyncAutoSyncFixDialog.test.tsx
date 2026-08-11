import { fireEvent, render, screen, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { EventSyncAutoSyncFixDialog, type AutoSyncFixTarget } from './EventSyncAutoSyncFixDialog';

const TARGET: AutoSyncFixTarget = {
  groupId: 2,
  groupName: 'Sports',
  accountId: 7,
  accountName: 'Provider A',
  enable: false,
};

describe('EventSyncAutoSyncFixDialog accessible name', () => {
  it('names the descendant alertdialog and keeps the overlay semantic-neutral', () => {
    render(<EventSyncAutoSyncFixDialog target={TARGET} onCancel={vi.fn()} onConfirm={vi.fn()} />);
    const dialog = screen.getByRole('alertdialog', { name: 'Turn auto-sync OFF for ‘Sports’?' });
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    expect(dialog.closest('[data-modal-overlay]')).not.toHaveAttribute('role');
  });

  it('gives duplicate instances distinct title IDs', () => {
    render(<><EventSyncAutoSyncFixDialog target={TARGET} onCancel={vi.fn()} onConfirm={vi.fn()} /><EventSyncAutoSyncFixDialog target={{ ...TARGET, groupId: 3, groupName: 'News', enable: true }} onCancel={vi.fn()} onConfirm={vi.fn()} /></>);
    const sports = screen.getByRole('alertdialog', { name: 'Turn auto-sync OFF for ‘Sports’?' });
    const news = screen.getByRole('alertdialog', { name: 'Turn auto-sync ON for ‘News’?' });
    expect(sports.getAttribute('aria-labelledby')).not.toBe(news.getAttribute('aria-labelledby'));
    expect(document.getElementById(sports.getAttribute('aria-labelledby')!)).toHaveTextContent('Sports');
    expect(document.getElementById(news.getAttribute('aria-labelledby')!)).toHaveTextContent('News');
  });

  it('preserves busy Escape suppression and disabled actions', () => {
    const onCancel = vi.fn();
    render(<EventSyncAutoSyncFixDialog target={TARGET} busy onCancel={onCancel} onConfirm={vi.fn()} />);
    const dialog = screen.getByRole('alertdialog', { name: 'Turn auto-sync OFF for ‘Sports’?' });
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onCancel).not.toHaveBeenCalled();
    expect(within(dialog).getByRole('button', { name: /applying/i })).toBeDisabled();
  });
});
