import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { HttpError } from '../services/httpClient';
import { SourceLoadStatus } from './SourceLoadStatus';
import { classifySourceLoadError } from './sourceLoadState';

describe('SourceLoadStatus', () => {
  it.each([
    ['loading', 'Loading source data…'],
    ['error', 'Source data unavailable'],
    ['permission', 'Source data requires administrator access'],
    ['success', '0 provider accounts'],
  ] as const)('renders the %s state without inventing a count', (state, text) => {
    render(<SourceLoadStatus state={state} successText="0 provider accounts" />);
    expect(screen.getByText(text)).toBeVisible();
    if (state !== 'success') expect(screen.queryByText('0 provider accounts')).not.toBeInTheDocument();
  });

  it('classifies 403 separately from network failures', () => {
    expect(classifySourceLoadError(new HttpError('Forbidden', 403))).toBe('permission');
    expect(classifySourceLoadError(new Error('Network down'))).toBe('error');
  });
});
