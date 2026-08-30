/**
 * useDedupOnDrop — drag-drop integration for the stream-to-channel dedup
 * modal (bd-u6ftw / BD-H, ADR-008 §D1 + §D5).
 *
 * Wraps the existing single-stream drop-into-group path with the BD-D
 * candidates lookup. When the matcher returns no candidate above the §D2
 * floor (empty `candidates` array), this hook invokes the caller-supplied
 * fallback — the unchanged drag-drop creation path. When a candidate is
 * returned, the modal opens for an operator decision.
 *
 * The cancel branch tags the dropped stream's id in `returningStreamIds`
 * for the brief outline pulse (`.is-dedup-returning`), then clears it
 * after {@link DEDUP_RETURNING_HIGHLIGHT_MS} so a subsequent drop can
 * re-trigger the animation. `prefers-reduced-motion: reduce` short-circuits
 * the pulse — the stream just snaps back to its source location (ADR-008
 * §D5 accessibility guidance).
 *
 * Scope: this hook only owns the single-stream drop case. Multi-stream
 * drops keep the existing bulk-create flow untouched — bulk dedup is a
 * separate epic surface (BD-I / bulk M3U dedup hook).
 */
import { useCallback, useRef, useState } from 'react';
import * as api from '../services/api';
import type { DedupCandidate } from '../services/api';
import { logger } from '../utils/logger';

/** Duration of the `.is-dedup-returning` outline pulse in ms. */
export const DEDUP_RETURNING_HIGHLIGHT_MS = 500;

export interface DedupDropRequest {
  streamId: number;
  streamName: string;
  /** Target channel group id, or null for the "ungrouped" bucket. */
  targetGroupId: number | null;
}

export interface DedupModalState {
  streamId: number;
  streamName: string;
  targetGroupId: number | null;
  candidate: DedupCandidate;
  /** The original drag-drop create path, retained so onCreateNew can run it. */
  fallback: () => void;
}

export interface UseDedupOnDropOptions {
  /**
   * Called after a successful merge so the channels list reflects the new
   * stream assignment. Awaited so the modal-close handoff happens after
   * the UI is consistent.
   */
  reloadChannels: () => Promise<void> | void;
  /**
   * Stage the stream assignment instead of writing it, when Edit Mode is on
   * (bead enhancedchannelmanager-kz089).
   *
   * The drag handle that starts this flow renders ONLY in Edit Mode, so every
   * merge this hook performs happens inside the staging area — and it went
   * straight to the server, uncounted, un-discardable, and followed by a full
   * channel refetch that fought the working copy. Supplied only when Edit Mode
   * is active; absent, the immediate write below is the correct behaviour.
   */
  stageAddStream?: (channelId: number, streamId: number, description: string) => void;
}

/**
 * What the drag-drop duplicate check actually did, reported back to the
 * caller (bead enhancedchannelmanager-ok8tj).
 *
 * The same defect `941d9087` fixed on the "Create in…" trigger path lived
 * here unchanged: all three outcomes looked identical from outside the hook
 * — the create path ran and nothing was said — so an operator who saw no
 * merge prompt could not tell "nothing in the group was similar enough"
 * from "the check never happened". Same vocabulary as
 * {@link import('./useAddStreamDedup').AddStreamDedupOutcome} deliberately:
 * this is one feature with two trigger paths, and the two must not drift.
 */
export type DedupDropOutcome =
  /** A candidate cleared the threshold and the modal is open. */
  | 'candidate'
  /** The lookup ran and nothing in the target group cleared the threshold. */
  | 'no_candidate'
  /** The lookup itself failed; the create path ran without a dedup check. */
  | 'lookup_failed';

/**
 * The full set of cases the operator can land in on a drop, including the
 * two the CALLER decides before this hook is ever reached: a multi-stream
 * drop (bulk dedup is a separate surface) and a drop whose stream metadata
 * the client cannot read (nothing to look up).
 */
export type DedupDropReportOutcome =
  | DedupDropOutcome
  | 'skipped_multi_stream'
  | 'skipped_unknown_stream';

/**
 * One drop's outcome plus the facts needed to describe it.
 *
 * The drop is initiated in `ChannelsPane`, which knows the target group's
 * name but not the dropped stream's; the dedup lookup is driven from
 * `App`, which knows the stream but cannot call `useNotifications` (it
 * renders `NotificationProvider` rather than living inside it). So the
 * producer names the case and carries the facts it alone holds, and the
 * consumer writes the sentence — the same division of labour as the
 * `useAddStreamDedup` → `StreamsPane` path, one hop longer.
 */
export interface DedupDropReport {
  outcome: DedupDropReportOutcome;
  /** Provider name of the dropped stream, or null when it could not be read. */
  streamName: string | null;
  /** How many streams the drop carried — the multi-stream skip needs it. */
  streamCount: number;
}

export interface UseDedupOnDropReturn {
  /** Open state + candidate for the StreamDedupModal, or null when closed. */
  modalState: DedupModalState | null;
  /** Stream ids currently rendering the cancel-pulse class. */
  returningStreamIds: Set<number>;
  /**
   * Entry point. Looks up candidates and either opens the modal or runs the
   * caller-supplied fallback (the unchanged single-stream creation path).
   * Backend errors are logged and still run the fallback so a failing
   * candidates endpoint never blocks a drop the operator already initiated —
   * but they are now reported as `lookup_failed` rather than passed off as
   * "no candidate", which is what made the two indistinguishable.
   *
   * @returns which of the three outcomes happened, so the caller can make it
   *   visible to the operator (bead enhancedchannelmanager-ok8tj).
   */
  handleSingleStreamDrop: (
    request: DedupDropRequest,
    fallback: () => void,
  ) => Promise<DedupDropOutcome>;
  /** Modal callback: merge into the candidate channel. */
  handleMerge: (channelId: string) => Promise<void>;
  /** Modal callback: proceed with the original create path. */
  handleCreateNew: () => Promise<void>;
  /** Modal callback: abort and trigger the cancel-pulse animation. */
  handleCancel: () => void;
}

function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return false;
  }
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

export function useDedupOnDrop({
  reloadChannels,
  stageAddStream,
}: UseDedupOnDropOptions): UseDedupOnDropReturn {
  const [modalState, setModalState] = useState<DedupModalState | null>(null);
  const [returningStreamIds, setReturningStreamIds] = useState<Set<number>>(
    () => new Set(),
  );
  // Track per-stream cancel-pulse timers so a rapid-fire cancel on the same
  // stream resets the window rather than firing two overlapping timeouts.
  const pulseTimersRef = useRef<Map<number, ReturnType<typeof setTimeout>>>(
    new Map(),
  );

  const handleSingleStreamDrop = useCallback(
    async (request: DedupDropRequest, fallback: () => void): Promise<DedupDropOutcome> => {
      let response;
      try {
        response = await api.getDedupCandidates(
          request.streamName,
          request.targetGroupId,
        );
      } catch (err) {
        // Candidates lookup must NOT block the existing drag-drop creation
        // path (bead spec: "unchanged behavior" when no match). Log loudly
        // so the failure is visible in the SLI feed, then fall through.
        logger.warn(
          '[DEDUP] candidates lookup failed; falling through to create path',
          err,
        );
        fallback();
        return 'lookup_failed';
      }

      if (response.candidates.length === 0) {
        // No candidate cleared the §D2 floor — proceed with the original
        // single-stream creation flow exactly as before.
        fallback();
        return 'no_candidate';
      }

      setModalState({
        streamId: request.streamId,
        streamName: request.streamName,
        targetGroupId: request.targetGroupId,
        candidate: response.candidates[0],
        fallback,
      });
      return 'candidate';
    },
    [],
  );

  const handleMerge = useCallback(
    async (channelId: string) => {
      const current = modalState;
      if (!current) return;

      // The candidates endpoint returns channel_id as a string (the
      // Dispatcharr UUID-as-string per ADR-008 §D8). Our merge surface is
      // the existing add-stream endpoint which takes a numeric id; the
      // conversion is safe here because Dispatcharr ids are integers
      // serialized as strings on the wire.
      const numericId = parseInt(channelId, 10);
      if (Number.isNaN(numericId)) {
        throw new Error(`Invalid channel id from dedup candidate: ${channelId}`);
      }

      if (stageAddStream) {
        // Staged: no write, and deliberately no reloadChannels() — refetching
        // the server list mid-session would overwrite the working copy that
        // holds every other staged change.
        stageAddStream(
          numericId,
          current.streamId,
          `Add stream to channel ${numericId}`,
        );
        setModalState(null);
        return;
      }

      await api.addStreamToChannel(numericId, current.streamId);
      await reloadChannels();
      setModalState(null);
    },
    [modalState, reloadChannels, stageAddStream],
  );

  const handleCreateNew = useCallback(async () => {
    const current = modalState;
    if (!current) return;
    current.fallback();
    setModalState(null);
  }, [modalState]);

  const handleCancel = useCallback(() => {
    const current = modalState;
    setModalState(null);
    if (!current) return;

    if (prefersReducedMotion()) {
      // Reduced motion — snap back, no pulse class. The stream's return to
      // its source location is implicit (we never moved it from the streams
      // pane; the modal was the only side effect of the drop).
      return;
    }

    const streamId = current.streamId;

    // Reset any in-flight pulse timer for this stream so a rapid cancel
    // (drop → cancel → drop → cancel) cleanly re-runs the animation.
    const existing = pulseTimersRef.current.get(streamId);
    if (existing) {
      clearTimeout(existing);
    }

    setReturningStreamIds((prev) => {
      const next = new Set(prev);
      next.add(streamId);
      return next;
    });

    const timer = setTimeout(() => {
      setReturningStreamIds((prev) => {
        if (!prev.has(streamId)) return prev;
        const next = new Set(prev);
        next.delete(streamId);
        return next;
      });
      pulseTimersRef.current.delete(streamId);
    }, DEDUP_RETURNING_HIGHLIGHT_MS);
    pulseTimersRef.current.set(streamId, timer);
  }, [modalState]);

  return {
    modalState,
    returningStreamIds,
    handleSingleStreamDrop,
    handleMerge,
    handleCreateNew,
    handleCancel,
  };
}
