/**
 * StreamDedupModal — operator decision surface for stream-to-channel
 * deduplication (BD-G / bd-4vxjj, ADR-008 §D1 + §D2).
 *
 * Distinct from MergeChannelsModal: this modal answers "should this incoming
 * stream merge into an existing channel, or create a new one?". The candidate
 * comes from `GET /api/channel-merges/candidates` (BD-D), which already
 * enforces the §D2 hard confidence floor — if the matcher's top-1 falls below
 * 60% confidence, the endpoint returns no candidate and this modal renders the
 * no-candidate empty state. We never see (and therefore never surface) a
 * sub-floor candidate on the client.
 *
 * Tab order is fixed at Cancel → Create New → Merge to keep the destructive-
 * adjacent action (Merge) last in the natural Tab progression. Exact (100%)
 * matches autofocus Merge so the operator can confirm with a single Enter
 * press; fuzzy matches force a conscious selection per §D2.
 *
 * The focus trap is bespoke (no project-wide focus-trap utility exists today —
 * `ModalOverlay` provides Escape handling only). The systemic fix is tracked
 * in deferred backlog bead enhancedchannelmanager-bfbk8 per ADR-008 §D10.
 */
import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { ModalOverlay } from './ModalOverlay';
import './ModalBase.css';
import './StreamDedupModal.css';

/**
 * Candidate returned by `GET /api/channel-merges/candidates` (BD-D).
 *
 * Per ADR-008 §D8 channel-id type note: `channel_id` is a string (the
 * Dispatcharr UUID), not a number — the epic body's `number` typing is
 * corrected at implementation time.
 */
export interface DedupCandidate {
  channel_id: string;
  channel_name: string;
  /** Normalized confidence score, 0.0–1.0. Per §D2 always ≥ 0.60. */
  confidence: number;
}

export type DedupTrigger = 'drag_drop' | 'add_stream' | 'm3u_refresh' | 'mcp_tool';

export interface StreamDedupModalProps {
  isOpen: boolean;
  /** The incoming stream name the operator is resolving. */
  streamName: string;
  /** Top-1 candidate from BD-D, or null if no candidate cleared the §D2 floor. */
  candidate: DedupCandidate | null;
  /** Surface that originated the prompt. Recorded in the journal (ADR-008 §D6). */
  trigger: DedupTrigger;
  /** Called with the candidate's `channel_id` when the operator chooses Merge. */
  onMerge: (channelId: string) => Promise<void>;
  /** Called when the operator chooses Create New. */
  onCreateNew: () => Promise<void>;
  /** Called when the operator chooses Cancel (or presses Escape via ModalOverlay). */
  onCancel: () => void;
}

const EXACT_MATCH_THRESHOLD = 1.0;

/** Format a 0.0–1.0 confidence score as a whole-percent badge ("92%"). */
function formatConfidencePercent(confidence: number): string {
  return `${Math.round(confidence * 100)}%`;
}

/**
 * Detect the operator's prefers-reduced-motion preference.
 *
 * Wraps `window.matchMedia` defensively for SSR / older jsdom hosts where it
 * may be undefined. Listens for live preference changes so the animation
 * class follows the user toggling system settings without a remount.
 */
function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState<boolean>(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return false;
    }
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  });

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
      return;
    }
    const mql = window.matchMedia('(prefers-reduced-motion: reduce)');
    const handler = (e: MediaQueryListEvent) => setReduced(e.matches);
    mql.addEventListener('change', handler);
    return () => mql.removeEventListener('change', handler);
  }, []);

  return reduced;
}

/**
 * Bespoke focus trap for this modal — confines Tab/Shift+Tab to the three
 * action buttons in the footer. `ModalOverlay` already handles Escape.
 *
 * The systemic ModalOverlay-level focus trap is deferred (ADR-008 §D10,
 * backlog enhancedchannelmanager-bfbk8). When that lands, this hook collapses
 * to whatever the shared utility exposes.
 */
function useFooterFocusTrap(
  enabled: boolean,
  containerRef: React.RefObject<HTMLElement | null>,
): void {
  useEffect(() => {
    if (!enabled) return;

    const handler = (e: KeyboardEvent) => {
      if (e.key !== 'Tab') return;
      const root = containerRef.current;
      if (!root) return;

      // Only trap inside the buttons we own — the modal's footer is the
      // complete set of interactive elements (no form inputs in the decision
      // surface). If new interactive elements get added to the body, extend
      // the selector here.
      const focusable = Array.from(
        root.querySelectorAll<HTMLButtonElement>(
          '.modal-footer button:not([disabled])',
        ),
      );
      if (focusable.length === 0) return;

      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;

      if (e.shiftKey) {
        if (active === first) {
          e.preventDefault();
          last.focus();
        }
      } else {
        if (active === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };

    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [enabled, containerRef]);
}

export function StreamDedupModal({
  isOpen,
  streamName,
  candidate,
  trigger: _trigger,
  onMerge,
  onCreateNew,
  onCancel,
}: StreamDedupModalProps) {
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const mergeButtonRef = useRef<HTMLButtonElement>(null);

  const prefersReducedMotion = usePrefersReducedMotion();
  const isExactMatch = candidate !== null && candidate.confidence >= EXACT_MATCH_THRESHOLD;
  const titleId = useId();

  useFooterFocusTrap(isOpen, containerRef);

  // Autofocus Merge for exact matches per ADR-008 §D2. For fuzzy matches the
  // operator must consciously pick — no autofocus.
  useEffect(() => {
    if (!isOpen) return;
    if (!isExactMatch) return;
    // Defer one frame so the button is mounted and the browser owns focus.
    const id = window.requestAnimationFrame(() => {
      mergeButtonRef.current?.focus();
    });
    return () => window.cancelAnimationFrame(id);
  }, [isOpen, isExactMatch]);

  // Reset transient state whenever the modal re-opens or the candidate changes
  // — a stale error from a previous candidate would otherwise mislead the
  // operator on the next prompt.
  useEffect(() => {
    if (isOpen) {
      setError(null);
      setSubmitting(false);
    }
  }, [isOpen, candidate?.channel_id]);

  const handleMerge = useCallback(async () => {
    if (!candidate) return;
    setSubmitting(true);
    setError(null);
    try {
      await onMerge(candidate.channel_id);
    } catch (err) {
      // Surface the backend detail verbatim. Per bd-7j6v1 / bd-9q9z0 the
      // operator-facing detail string is the source of truth; collapsing it
      // into a generic "Merge failed" hides the actionable bit (e.g., "target
      // channel no longer exists — dismiss this pending merge and refresh").
      const detail = err instanceof Error ? err.message : 'Merge failed';
      setError(detail);
    } finally {
      setSubmitting(false);
    }
  }, [candidate, onMerge]);

  const handleCreateNew = useCallback(async () => {
    setSubmitting(true);
    setError(null);
    try {
      await onCreateNew();
    } catch (err) {
      const detail = err instanceof Error ? err.message : 'Create new failed';
      setError(detail);
    } finally {
      setSubmitting(false);
    }
  }, [onCreateNew]);

  if (!isOpen) return null;

  const containerClass = [
    'modal-container',
    'modal-md',
    'stream-dedup-modal',
    prefersReducedMotion ? 'is-reduced-motion' : '',
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <ModalOverlay onClose={onCancel} role="dialog" aria-modal="true" aria-labelledby={titleId}>
      <div ref={containerRef} className={containerClass}>
        <div className="modal-header">
          <h2 id={titleId}>Stream matches an existing channel</h2>
          <button
            className="modal-close-btn"
            onClick={onCancel}
            title="Close"
            aria-label="Close"
            type="button"
          >
            <span className="material-icons">close</span>
          </button>
        </div>

        <div className="modal-body">
          {error && (
            <div className="modal-error-banner" role="alert">
              <span className="material-icons">error</span>
              <span>{error}</span>
            </div>
          )}

          <div className="modal-form-group">
            <label>Incoming stream</label>
            <div className="stream-dedup-stream-name">{streamName}</div>
          </div>

          {candidate ? (
            <div className="modal-form-group">
              <label>Candidate channel</label>
              <div className="stream-dedup-candidate-row">
                <span className="stream-dedup-candidate-name">{candidate.channel_name}</span>
                {isExactMatch ? (
                  <span
                    className="confidence-badge stream-dedup-exact-badge"
                    aria-label="Exact match"
                  >
                    Exact match
                  </span>
                ) : (
                  <span
                    className="confidence-badge stream-dedup-confidence-badge"
                    aria-label={`Confidence: ${Math.round(candidate.confidence * 100)} percent`}
                  >
                    {formatConfidencePercent(candidate.confidence)} match
                  </span>
                )}
              </div>
            </div>
          ) : (
            <div className="modal-empty-state stream-dedup-empty">
              <span className="material-icons">search_off</span>
              <p>No candidate found above the confidence floor — create a new channel or cancel.</p>
            </div>
          )}
        </div>

        <div className="modal-footer">
          {/* Tab order: Cancel → Create New → Merge per ADR-008 §D1. */}
          <button
            className="modal-btn modal-btn-secondary"
            onClick={onCancel}
            type="button"
            disabled={submitting}
          >
            Cancel
          </button>
          <button
            className="modal-btn modal-btn-secondary"
            onClick={handleCreateNew}
            type="button"
            disabled={submitting}
          >
            Create New
          </button>
          <button
            ref={mergeButtonRef}
            className={
              isExactMatch
                ? 'modal-btn modal-btn-primary'
                : 'modal-btn modal-btn-secondary'
            }
            onClick={handleMerge}
            type="button"
            disabled={submitting || candidate === null}
          >
            {submitting ? 'Merging...' : 'Merge'}
          </button>
        </div>
      </div>
    </ModalOverlay>
  );
}
