import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { HttpError } from '../services/httpClient';
import { SourceLoadStatus } from './SourceLoadStatus';
import { classifySourceLoadError } from './sourceLoadState';

describe('SourceLoadStatus', () => {
  it.each([
    ['loading', 'Loading provider accounts…'],
    ['error', 'Provider accounts unavailable'],
    ['permission', 'Provider accounts require administrator access'],
    ['success', '0 provider accounts'],
  ] as const)('renders the %s state without inventing a count', (state, text) => {
    render(
      <SourceLoadStatus
        state={state}
        sourceName="provider accounts"
        successText="0 provider accounts"
      />,
    );
    expect(screen.getByText(text)).toBeVisible();
    if (state !== 'success') expect(screen.queryByText('0 provider accounts')).not.toBeInTheDocument();
  });

  it('announces concise lifecycle updates and exposes loading progress', () => {
    const { rerender } = render(
      <SourceLoadStatus
        state="loading"
        sourceName="EPG sources"
        successText="0 EPG sources"
      />,
    );

    const status = screen.getByRole('status', { name: 'Loading EPG sources' });
    expect(status).toHaveAttribute('aria-live', 'polite');
    expect(status).toHaveAttribute('aria-atomic', 'true');
    expect(status).toHaveAttribute('aria-busy', 'true');

    rerender(
      <SourceLoadStatus
        state="success"
        sourceName="EPG sources"
        successText="2 EPG sources"
      />,
    );
    expect(screen.getByRole('status', { name: 'EPG sources loaded' }))
      .toHaveAttribute('aria-busy', 'false');
  });

  it('offers a source-scoped Retry only for transient errors', () => {
    const onRetry = vi.fn();
    const { rerender } = render(
      <SourceLoadStatus
        state="error"
        sourceName="logos"
        successText=""
        onRetry={onRetry}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Retry loading logos' }));
    expect(onRetry).toHaveBeenCalledOnce();

    rerender(
      <SourceLoadStatus
        state="permission"
        sourceName="logos"
        successText=""
        onRetry={onRetry}
      />,
    );
    expect(screen.queryByRole('button', { name: /Retry/i })).not.toBeInTheDocument();
  });

  it('can render a duplicate visual status without a second live announcement', () => {
    render(
      <SourceLoadStatus
        state="error"
        sourceName="logos"
        successText=""
        announce={false}
      />,
    );
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(screen.getByText('Logos unavailable')).toBeVisible();
  });

  it('classifies 403 separately from network failures', () => {
    expect(classifySourceLoadError(new HttpError('Forbidden', 403))).toBe('permission');
    expect(classifySourceLoadError(new Error('Network down'))).toBe('error');
  });
});
