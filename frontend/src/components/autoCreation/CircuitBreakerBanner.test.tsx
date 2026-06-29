/**
 * TDD Tests for CircuitBreakerBanner component (bd-fqur1).
 *
 * Covers:
 *  - Banner hides when breaker is not tripped
 *  - Banner shows on abandoned_run with correct message
 *  - Banner shows for manual-disable without reset button
 *  - Reset button only for admins on abandoned_run
 *  - Reset confirmation dialog flow
 *  - POST /reset-circuit-breaker called on confirm
 *  - onReset callback invoked after success
 */
import type * as React from 'react';
import { describe, it, expect, vi, beforeAll, afterAll, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { server, mockDataStore } from '../../test/mocks/server';
import { CircuitBreakerBanner } from './CircuitBreakerBanner';
import { NotificationProvider } from '../../contexts/NotificationContext';

// Setup MSW
beforeAll(() => server.listen({ onUnhandledRequest: 'error' }));
afterEach(() => server.resetHandlers());
afterAll(() => server.close());

const renderBanner = (props: React.ComponentProps<typeof CircuitBreakerBanner>) =>
  render(
    <NotificationProvider>
      <CircuitBreakerBanner {...props} />
    </NotificationProvider>,
  );

describe('CircuitBreakerBanner', () => {
  describe('when breaker is not tripped', () => {
    it('renders nothing', () => {
      const { container } = renderBanner({
        state: { disabled: false, reason: null },
        isAdmin: true,
        onReset: vi.fn(),
      });
      expect(container).toBeEmptyDOMElement();
    });
  });

  describe('when tripped by abandoned_run', () => {
    const trippedState = { disabled: true, reason: 'abandoned_run' as const };

    it('shows the banner', () => {
      renderBanner({ state: trippedState, isAdmin: false, onReset: vi.fn() });
      expect(screen.getByTestId('circuit-breaker-banner')).toBeInTheDocument();
    });

    it('shows abandoned_run message', () => {
      renderBanner({ state: trippedState, isAdmin: false, onReset: vi.fn() });
      expect(screen.getByText(/auto-creation suspended/i)).toBeInTheDocument();
    });

    it('shows reset button for admins', () => {
      renderBanner({ state: trippedState, isAdmin: true, onReset: vi.fn() });
      expect(screen.getByTestId('circuit-breaker-reset-btn')).toBeInTheDocument();
    });

    it('hides reset button for non-admins', () => {
      renderBanner({ state: trippedState, isAdmin: false, onReset: vi.fn() });
      expect(screen.queryByTestId('circuit-breaker-reset-btn')).not.toBeInTheDocument();
    });

    it('opens confirmation dialog on reset click', async () => {
      const user = userEvent.setup();
      renderBanner({ state: trippedState, isAdmin: true, onReset: vi.fn() });

      await user.click(screen.getByTestId('circuit-breaker-reset-btn'));

      expect(screen.getByTestId('circuit-breaker-confirm-dialog')).toBeInTheDocument();
    });

    it('closes confirmation dialog on cancel', async () => {
      const user = userEvent.setup();
      renderBanner({ state: trippedState, isAdmin: true, onReset: vi.fn() });

      await user.click(screen.getByTestId('circuit-breaker-reset-btn'));
      await user.click(screen.getByRole('button', { name: /cancel/i }));

      expect(screen.queryByTestId('circuit-breaker-confirm-dialog')).not.toBeInTheDocument();
    });

    it('calls POST reset-circuit-breaker and invokes onReset on confirm', async () => {
      const user = userEvent.setup();
      const onReset = vi.fn();
      // Wire the mock store so the handler returns was_disabled: true
      mockDataStore.circuitBreaker = { disabled: true, reason: 'abandoned_run' };
      renderBanner({ state: trippedState, isAdmin: true, onReset });

      await user.click(screen.getByTestId('circuit-breaker-reset-btn'));
      await user.click(screen.getByTestId('circuit-breaker-confirm-reset-btn'));

      await waitFor(() => expect(onReset).toHaveBeenCalledTimes(1));
    });

    it('shows success toast after reset', async () => {
      const user = userEvent.setup();
      mockDataStore.circuitBreaker = { disabled: true, reason: 'abandoned_run' };
      renderBanner({ state: trippedState, isAdmin: true, onReset: vi.fn() });

      await user.click(screen.getByTestId('circuit-breaker-reset-btn'));
      await user.click(screen.getByTestId('circuit-breaker-confirm-reset-btn'));

      await waitFor(() =>
        expect(screen.getByText(/circuit breaker cleared/i)).toBeInTheDocument()
      );
    });

    it('shows error toast when reset API fails', async () => {
      const user = userEvent.setup();
      server.use(
        http.post('/api/auto-creation/reset-circuit-breaker', () =>
          // Return a network-level error body with a detail that httpClient surfaces
          HttpResponse.json({ detail: 'Failed to reset circuit breaker' }, { status: 500 }),
        ),
      );
      renderBanner({ state: trippedState, isAdmin: true, onReset: vi.fn() });

      await user.click(screen.getByTestId('circuit-breaker-reset-btn'));
      await user.click(screen.getByTestId('circuit-breaker-confirm-reset-btn'));

      await waitFor(() =>
        expect(screen.getByText(/failed to reset circuit breaker/i)).toBeInTheDocument()
      );
    });
  });

  describe('when tripped manually (reason: null)', () => {
    const manualState = { disabled: true, reason: null as null };

    it('shows the banner', () => {
      renderBanner({ state: manualState, isAdmin: true, onReset: vi.fn() });
      expect(screen.getByTestId('circuit-breaker-banner')).toBeInTheDocument();
    });

    it('shows manual-disable message', () => {
      renderBanner({ state: manualState, isAdmin: true, onReset: vi.fn() });
      expect(screen.getByText(/run-on-refresh is disabled/i)).toBeInTheDocument();
    });

    it('does NOT show reset button even for admins', () => {
      renderBanner({ state: manualState, isAdmin: true, onReset: vi.fn() });
      expect(screen.queryByTestId('circuit-breaker-reset-btn')).not.toBeInTheDocument();
    });
  });
});
