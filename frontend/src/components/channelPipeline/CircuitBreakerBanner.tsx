/**
 * Circuit-breaker banner for the Channel Pipeline tab.
 *
 * Shown when the run-on-refresh circuit breaker is tripped. Distinguishes two
 * cases:
 *   - reason === 'abandoned_run': tripped automatically after an abandoned
 *     (OOM-killed) run. Shows a reset button for admins.
 *   - reason === null / other: tripped manually by the user. No reset button
 *     (user disabled it intentionally; they re-enable via the run-on-refresh
 *     toggle in the rule settings).
 *
 * Manual "Run Now" is never gated by the circuit breaker — only the automatic
 * post-refresh trigger is affected.
 */
import { useState } from 'react';
import { useNotifications } from '../../contexts/NotificationContext';
import type { CircuitBreakerState } from '../../services/channelPipelineApi';
import * as channelPipelineApi from '../../services/channelPipelineApi';
import './CircuitBreakerBanner.css';

export interface CircuitBreakerBannerProps {
  /** Current state from GET /api/channel-pipeline/circuit-breaker */
  state: CircuitBreakerState;
  /** Whether the current user is an admin (and can reset the breaker) */
  isAdmin: boolean;
  /** Called after a successful reset so the parent can refresh state */
  onReset: () => void;
}

export function CircuitBreakerBanner({ state, isAdmin, onReset }: CircuitBreakerBannerProps) {
  const notifications = useNotifications();
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [resetting, setResetting] = useState(false);

  // Only render when the breaker is actually tripped
  if (!state.disabled) return null;

  const isAbandonedRun = state.reason === 'abandoned_run';
  const canReset = isAdmin && isAbandonedRun;

  const handleConfirmReset = async () => {
    setResetting(true);
    try {
      await channelPipelineApi.resetCircuitBreaker();
      notifications.success(
        'Circuit breaker cleared — the channel pipeline will run after M3U refresh again.',
        'Channel Pipeline',
      );
      setConfirmOpen(false);
      onReset();
    } catch (err) {
      notifications.error(
        err instanceof Error ? err.message : 'Failed to reset circuit breaker',
        'Channel Pipeline',
      );
    } finally {
      setResetting(false);
    }
  };

  return (
    <>
      <div
        className={`circuit-breaker-banner ${isAbandonedRun ? 'circuit-breaker-banner--abandoned' : 'circuit-breaker-banner--manual'}`}
        data-testid="circuit-breaker-banner"
        role="alert"
      >
        <span className="material-icons circuit-breaker-icon">
          {isAbandonedRun ? 'warning' : 'pause_circle'}
        </span>
        <div className="circuit-breaker-text">
          {isAbandonedRun ? (
            <>
              <strong>Channel pipeline suspended</strong> — the previous run was abandoned
              (likely an OOM crash). Run-on-refresh is disabled until an operator resets
              the circuit breaker. Manual &quot;Run Now&quot; is unaffected.
            </>
          ) : (
            <>
              <strong>Run-on-refresh is disabled</strong> — the channel pipeline will not fire
              automatically after M3U refresh. Manual &quot;Run Now&quot; is unaffected.
            </>
          )}
        </div>
        {canReset && (
          <button
            className="btn-secondary circuit-breaker-reset-btn"
            onClick={() => setConfirmOpen(true)}
            data-testid="circuit-breaker-reset-btn"
            aria-label="Reset circuit breaker"
          >
            <span className="material-icons">restart_alt</span>
            Reset
          </button>
        )}
      </div>

      {/* Reset confirmation dialog */}
      {confirmOpen && (
        <div
          className="circuit-breaker-confirm-overlay"
          role="dialog"
          aria-modal="true"
          aria-labelledby="cb-confirm-title"
          data-testid="circuit-breaker-confirm-dialog"
        >
          <div className="circuit-breaker-confirm-dialog">
            <h3 id="cb-confirm-title">Reset Circuit Breaker</h3>
            <p>
              This will re-enable the automatic channel pipeline after every M3U refresh.
              Only reset if the previous abandoned run has been investigated.
            </p>
            <div className="circuit-breaker-confirm-footer">
              <button
                className="btn-secondary"
                onClick={() => setConfirmOpen(false)}
                disabled={resetting}
              >
                Cancel
              </button>
              <button
                className="btn-primary"
                onClick={handleConfirmReset}
                disabled={resetting}
                data-testid="circuit-breaker-confirm-reset-btn"
                aria-label="Confirm reset"
              >
                {resetting ? (
                  <>
                    <span className="material-icons spinning" style={{ fontSize: '16px', marginRight: '4px' }}>sync</span>
                    Resetting...
                  </>
                ) : (
                  'Reset Circuit Breaker'
                )}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
