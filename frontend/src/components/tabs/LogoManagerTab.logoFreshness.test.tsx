/**
 * A logo added or removed in Logo Manager must reach the app-level logo
 * catalogue the Edit Channel picker reads from (bead
 * enhancedchannelmanager-5z7c9, instance 2 — drill run 2026-08-06-run9
 * finding P-4).
 *
 * WHAT WENT WRONG. Logo Manager owns its own paged copy of the catalogue and
 * refetched only that copy after a write. The Edit Channel logo picker reads
 * `App`'s `logos` state, loaded once via `api.getAllLogos()`. So a logo
 * uploaded through Logo Manager appeared in Logo Manager, and the file landed
 * in Dispatcharr's `/data/logos/`, while the picker kept listing 12 options
 * instead of 13 — searching the new logo's name found nothing — until the
 * whole page was reloaded.
 *
 * These tests pin the publish half of the fix: the tab announces `logos` on
 * the mutation that changed them, and only then. The subscribe half is
 * `App.tsx` calling `useServerDataInvalidation('logos', loadLogos)`; the
 * channel itself is covered in `hooks/useServerDataInvalidation.test.tsx`.
 */
import type * as React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { LogoManagerTab } from './LogoManagerTab';
import { NotificationProvider } from '../../contexts/NotificationContext';
import { useServerDataInvalidation } from '../../hooks/useServerDataInvalidation';
import * as api from '../../services/api';
import type { Logo, PaginatedResponse } from '../../types';

vi.mock('../../services/api');

const EXISTING: Logo = {
  id: 1,
  name: 'PBS East',
  url: 'http://example/pbs.png',
  cache_url: '',
  channel_count: 1,
  is_used: true,
};

function pageResponse(results: Logo[]): PaginatedResponse<Logo> {
  return { results, count: results.length, next: null, previous: null };
}

/** Stands in for `App`, the holder of the catalogue the picker reads. */
function CatalogueHolder({ onInvalidated }: { onInvalidated: () => void }) {
  useServerDataInvalidation('logos', onInvalidated);
  return null;
}

function renderTab(onInvalidated: () => void): React.JSX.Element {
  return (
    <NotificationProvider>
      <CatalogueHolder onInvalidated={onInvalidated} />
      <LogoManagerTab />
    </NotificationProvider>
  );
}

async function renderAndSettle(onInvalidated: () => void) {
  render(renderTab(onInvalidated));
  await waitFor(() => {
    expect(screen.queryByText('Loading logos...')).not.toBeInTheDocument();
  });
}

describe('Logo catalogue freshness after a Logo Manager write (bead 5z7c9 instance 2)', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(api.getLogos).mockResolvedValue(pageResponse([EXISTING]));
  });

  it('announces the catalogue is stale after a logo is added', async () => {
    const onInvalidated = vi.fn();
    vi.mocked(api.createLogo).mockResolvedValue({ ...EXISTING, id: 2, name: 'Run9 Uploaded Logo' });
    await renderAndSettle(onInvalidated);

    fireEvent.click(screen.getByRole('button', { name: /Add Logo/i }));
    const modal = screen.getByRole('heading', { name: 'Add Logo' }).closest('.logo-modal') as HTMLElement;
    fireEvent.change(within(modal).getByLabelText('Logo Name'), {
      target: { value: 'Run9 Uploaded Logo' },
    });
    fireEvent.change(within(modal).getByLabelText('Logo URL'), {
      target: { value: 'http://example/run9.png' },
    });
    fireEvent.click(within(modal).getByRole('button', { name: 'Add Logo' }));

    await waitFor(() => expect(api.createLogo).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(onInvalidated).toHaveBeenCalledTimes(1));
  });

  it('announces the catalogue is stale after a logo is deleted', async () => {
    const onInvalidated = vi.fn();
    vi.mocked(api.deleteLogo).mockResolvedValue(undefined as never);
    await renderAndSettle(onInvalidated);

    fireEvent.click(screen.getByRole('button', { name: 'Delete logo' }));
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(api.deleteLogo).toHaveBeenCalledWith(EXISTING.id));
    await waitFor(() => expect(onInvalidated).toHaveBeenCalledTimes(1));
  });

  it('says nothing when a delete fails — the catalogue did not change', async () => {
    const onInvalidated = vi.fn();
    vi.mocked(api.deleteLogo).mockRejectedValue(new Error('Dispatcharr unreachable'));
    await renderAndSettle(onInvalidated);

    fireEvent.click(screen.getByRole('button', { name: 'Delete logo' }));
    fireEvent.click(screen.getByRole('button', { name: 'Delete' }));

    await waitFor(() => expect(api.deleteLogo).toHaveBeenCalledTimes(1));
    expect(onInvalidated).not.toHaveBeenCalled();
  });

  it('says nothing on a plain page view — only a mutation invalidates', async () => {
    const onInvalidated = vi.fn();
    await renderAndSettle(onInvalidated);

    fireEvent.change(screen.getByPlaceholderText('Search logos...'), { target: { value: 'pbs' } });
    await waitFor(() => expect(api.getLogos).toHaveBeenCalledTimes(2));

    expect(onInvalidated).not.toHaveBeenCalled();
  });
});
