import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { VLCProtocolHelperModal } from './VLCProtocolHelperModal';

describe('VLCProtocolHelperModal semantics', () => {
  it('resolves its accessible name, contains focus, and closes on Escape', async () => {
    const onClose = vi.fn();
    render(
      <VLCProtocolHelperModal
        isOpen={true}
        onClose={onClose}
        onDownloadM3U={vi.fn()}
        streamName="Synthetic stream"
      />
    );

    const dialog = screen.getByRole('dialog', { name: 'VLC Protocol Not Available' });
    const titleId = dialog.getAttribute('aria-labelledby');
    expect(titleId).toBeTruthy();
    expect(document.getElementById(titleId!)).toHaveTextContent('VLC Protocol Not Available');
    await vi.waitFor(() => expect(dialog).toContainElement(document.activeElement as HTMLElement));

    await userEvent.keyboard('{Escape}');
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('uses distinct title references for simultaneous instances', () => {
    render(
      <>
        <VLCProtocolHelperModal isOpen={true} onClose={vi.fn()} onDownloadM3U={vi.fn()} streamName="One" />
        <VLCProtocolHelperModal isOpen={true} onClose={vi.fn()} onDownloadM3U={vi.fn()} streamName="Two" />
      </>
    );

    const ids = screen.getAllByRole('dialog', { name: 'VLC Protocol Not Available' })
      .map((dialog) => dialog.getAttribute('aria-labelledby'));
    expect(new Set(ids).size).toBe(2);
    ids.forEach((id) => expect(document.getElementById(id!)).toHaveTextContent('VLC Protocol Not Available'));
  });
});
