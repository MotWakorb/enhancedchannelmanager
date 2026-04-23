/**
 * Unit tests for ErrorBoundary component.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ErrorBoundary } from './ErrorBoundary';

/** Child component that throws on render when `shouldThrow` is true. */
function Boom({ shouldThrow, message = 'kaboom' }: { shouldThrow: boolean; message?: string }) {
  if (shouldThrow) {
    throw new Error(message);
  }
  return <div>child-ok</div>;
}

describe('ErrorBoundary', () => {
  // React logs caught errors to console.error during the commit phase — silence it
  // so test output stays clean. componentDidCatch also calls console.error.
  let consoleErrorSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    consoleErrorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
  });

  afterEach(() => {
    consoleErrorSpy.mockRestore();
  });

  it('renders children when no error', () => {
    render(
      <ErrorBoundary>
        <Boom shouldThrow={false} />
      </ErrorBoundary>,
    );
    expect(screen.getByText('child-ok')).toBeInTheDocument();
  });

  it('renders default fallback when child throws', () => {
    render(
      <ErrorBoundary>
        <Boom shouldThrow message="render blew up" />
      </ErrorBoundary>,
    );

    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByText('Something went wrong')).toBeInTheDocument();
    expect(screen.getByText('render blew up')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reload' })).toBeInTheDocument();
  });

  it('renders scoped title when scopeLabel provided', () => {
    render(
      <ErrorBoundary scopeLabel="Stats tab" reloadMode="reset">
        <Boom shouldThrow />
      </ErrorBoundary>,
    );

    expect(screen.getByText('Stats tab encountered an error')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reload tab' })).toBeInTheDocument();
  });

  it('calls onError callback with the error', () => {
    const onError = vi.fn();
    render(
      <ErrorBoundary onError={onError}>
        <Boom shouldThrow message="callback-me" />
      </ErrorBoundary>,
    );

    expect(onError).toHaveBeenCalledTimes(1);
    const [caughtError, errorInfo] = onError.mock.calls[0];
    expect(caughtError).toBeInstanceOf(Error);
    expect((caughtError as Error).message).toBe('callback-me');
    // React passes an ErrorInfo object with componentStack.
    expect(errorInfo).toHaveProperty('componentStack');
  });

  it('renders a custom fallback node when provided', () => {
    render(
      <ErrorBoundary fallback={<div>custom-fallback</div>}>
        <Boom shouldThrow />
      </ErrorBoundary>,
    );
    expect(screen.getByText('custom-fallback')).toBeInTheDocument();
  });

  it('renders a render-prop fallback and resets on demand', () => {
    const renderFallback = (error: Error, reset: () => void) => (
      <div>
        <span>fallback: {error.message}</span>
        <button type="button" onClick={reset}>
          retry
        </button>
      </div>
    );

    const { rerender } = render(
      <ErrorBoundary fallback={renderFallback}>
        <Boom shouldThrow message="first" />
      </ErrorBoundary>,
    );

    expect(screen.getByText('fallback: first')).toBeInTheDocument();

    // Swap children to a non-throwing version, then click retry to reset the
    // boundary's internal state so it re-renders children.
    rerender(
      <ErrorBoundary fallback={renderFallback}>
        <Boom shouldThrow={false} />
      </ErrorBoundary>,
    );
    fireEvent.click(screen.getByRole('button', { name: 'retry' }));

    expect(screen.getByText('child-ok')).toBeInTheDocument();
  });
});
