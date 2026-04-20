/**
 * ErrorBoundary component.
 *
 * Catches render-phase errors in descendant components so a single crash
 * does not white-screen the whole SPA. Two wiring points:
 *   1. Top-level in main.tsx — last-resort catch for the whole app.
 *   2. Per-tab in App.tsx — a crash in one tab does not kill the others.
 *
 * This is a class component because React's ErrorBoundary API
 * (getDerivedStateFromError + componentDidCatch) is only available on classes.
 */
import { Component, ErrorInfo, ReactNode } from 'react';
import './ErrorBoundary.css';

export interface ErrorBoundaryProps {
  children: ReactNode;
  /**
   * Custom fallback UI. Receives the error and a reset function.
   * If not provided, a sensible default card is rendered.
   */
  fallback?: ReactNode | ((error: Error, reset: () => void) => ReactNode);
  /** Optional hook for side-effects (logging, analytics) when an error is caught. */
  onError?: (error: Error, errorInfo: ErrorInfo) => void;
  /** Label for the default fallback (e.g. "Stats tab"). */
  scopeLabel?: string;
  /**
   * Reload behavior for the default fallback's primary action:
   *   - 'page' (default): full page reload via window.location.reload()
   *   - 'reset': reset boundary state only (re-mount children)
   */
  reloadMode?: 'page' | 'reset';
}

interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  state: ErrorBoundaryState = { hasError: false, error: null };

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    // Surface to the browser console for debugging (stack + component stack).
    // Keep this unconditional — the console is the debugger of last resort.
    console.error(
      '[ErrorBoundary] Caught render error',
      { scope: this.props.scopeLabel ?? 'top-level' },
      error,
      errorInfo.componentStack,
    );
    try {
      this.props.onError?.(error, errorInfo);
    } catch (callbackError) {
      // Never let a broken onError callback mask the original crash.
      console.error('[ErrorBoundary] onError callback threw', callbackError);
    }
  }

  private reset = (): void => {
    this.setState({ hasError: false, error: null });
  };

  render(): ReactNode {
    const { children, fallback, scopeLabel, reloadMode = 'page' } = this.props;
    const { hasError, error } = this.state;

    if (!hasError || error === null) {
      return children;
    }

    if (typeof fallback === 'function') {
      return fallback(error, this.reset);
    }
    if (fallback !== undefined) {
      return fallback;
    }

    const isPageReload = reloadMode === 'page';
    const title = scopeLabel
      ? `${scopeLabel} encountered an error`
      : 'Something went wrong';
    const primaryLabel = isPageReload ? 'Reload' : 'Reload tab';
    const primaryAction = isPageReload
      ? () => window.location.reload()
      : this.reset;

    return (
      <div className="error-boundary" role="alert">
        <div className="error-boundary-card">
          <span className="material-icons error-boundary-icon">error_outline</span>
          <h2 className="error-boundary-title">{title}</h2>
          <p className="error-boundary-message">{error.message || 'An unexpected error occurred.'}</p>
          <button
            type="button"
            className="error-boundary-button"
            onClick={primaryAction}
          >
            {primaryLabel}
          </button>
          <p className="error-boundary-hint">
            If this keeps happening, please report it to the maintainer.
          </p>
        </div>
      </div>
    );
  }
}

export default ErrorBoundary;
