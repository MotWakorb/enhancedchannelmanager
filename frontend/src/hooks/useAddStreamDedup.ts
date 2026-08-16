/**
 * useAddStreamDedup — BD-I integration hook for the "Add Stream" surface
 * (ADR-008 §D1, trigger_context='add_stream').
 *
 * Wires `GET /api/channel-merges/candidates` (BD-D) to `StreamDedupModal`
 * (BD-G) for the single-stream "Add Stream" flow on channel-less stream
 * cards. The hook owns the candidate-lookup + modal-state machine so the
 * StreamsPane render path stays small and the dedup behavior is unit-
 * testable in isolation.
 *
 * Flow (mirrors BD-H drag-drop minus the cancel-bounce animation):
 *
 *   requestAddStream(stream, groupId, onProceed)
 *     → fetch candidates
 *     → empty list  ⇒ call onProceed (auto-creation rules / bulk-create
 *                       modal — caller decides), modal stays closed.
 *     → candidate   ⇒ flip modalState to open with the candidate; the
 *                       operator then drives one of:
 *                       - handleMerge(channelId) → addStreamToChannel,
 *                       - handleCreateNew()       → calls onProceed,
 *                       - handleCancel()          → just closes the modal.
 *
 * Lookup failure (network / 5xx) intentionally falls through to onProceed
 * so the operator's "Add Stream" click is never silently dropped — better
 * to skip the dedup prompt than to leave the user staring at a button
 * that did nothing.
 *
 * The string→number conversion on `channel_id` lives here because that is
 * the only place the modal's ADR-008 §D8 string contract meets the
 * `/api/channels/{id}/add-stream` numeric-id endpoint. Centralizing the
 * cast keeps the consumer ignorant of the type mismatch.
 */
import { useCallback, useState } from 'react';
import {
  addStreamToChannel,
  getChannelMergeCandidates,
  type ChannelMergeCandidate,
} from '../services/api';
import { logger } from '../utils/logger';

export interface AddStreamDedupModalState {
  isOpen: boolean;
  streamName: string;
  candidate: ChannelMergeCandidate | null;
}

export interface AddStreamRequestStream {
  /** Dispatcharr stream id used as the `stream_id` body field on /add-stream. */
  id: number;
  /** Raw incoming stream name; the candidates lookup matches against this. */
  name: string;
}

/** Caller-supplied callback that runs the original "Add Stream" creation path. */
export type OnProceedCreate = () => void | Promise<void>;

/**
 * What the dedup lookup actually did, reported back to the caller
 * (bead enhancedchannelmanager-ok8tj).
 *
 * All three outcomes used to look identical from outside the hook: the
 * create path ran and nothing was said. An operator who saw no merge prompt
 * could not tell "nothing in the group was similar enough" from "the check
 * never happened", which is exactly the report this bead was filed on. The
 * caller decides what to show; the hook only says which case it was.
 */
export type AddStreamDedupOutcome =
  /** A candidate cleared the threshold and the modal is open. */
  | 'candidate'
  /** The lookup ran and nothing in the target group cleared the threshold. */
  | 'no_candidate'
  /** The lookup itself failed; the create path ran without a dedup check. */
  | 'lookup_failed';

export interface UseAddStreamDedupReturn {
  modalState: AddStreamDedupModalState;
  /**
   * Entry point called when the operator clicks "Add Stream" on a channel-
   * less stream card. Performs the candidate lookup, then either opens the
   * modal or falls through to `onProceed` (the original create-channel
   * path).
   *
   * @param stream The incoming stream the operator is adding.
   * @param groupId Target channel group; pass `null` to search all groups.
   * @param onProceed Original create-channel callback — fired when no
   *   candidate is returned, when the operator picks "Create New", or when
   *   the candidate lookup itself fails (so the operator action is never
   *   silently swallowed).
   * @returns which of the three outcomes happened, so the caller can make it
   *   visible to the operator (bead enhancedchannelmanager-ok8tj).
   */
  requestAddStream: (
    stream: AddStreamRequestStream,
    groupId: number | null,
    onProceed: OnProceedCreate,
  ) => Promise<AddStreamDedupOutcome>;
  /** Operator chose Merge: append the source stream to the candidate channel. */
  handleMerge: (channelId: string) => Promise<void>;
  /** Operator chose Create New: run the original create-channel path. */
  handleCreateNew: () => Promise<void>;
  /** Operator cancelled: close the modal, no downstream effect. */
  handleCancel: () => void;
}

const CLOSED_STATE: AddStreamDedupModalState = {
  isOpen: false,
  streamName: '',
  candidate: null,
};

export interface UseAddStreamDedupOptions {
  /**
   * Stage the stream assignment instead of writing it, when Edit Mode is on
   * (bead enhancedchannelmanager-kz089).
   *
   * The "Create in..." menu this flow hangs off renders ONLY in Edit Mode, so
   * every merge it performs happens inside the staging area — and it wrote
   * straight to the server, uncounted by the change count and out of reach of
   * Discard, Cancel and Undo. Absent, the immediate write is correct: the same
   * merge outside Edit Mode has no staging promise to keep.
   */
  stageAddStream?: (channelId: number, streamId: number, description: string) => void;
}

export function useAddStreamDedup(
  { stageAddStream }: UseAddStreamDedupOptions = {},
): UseAddStreamDedupReturn {
  const [modalState, setModalState] = useState<AddStreamDedupModalState>(CLOSED_STATE);
  // Pending context for the open modal. Held in component state (not a ref)
  // so a re-render after the candidate fetch sees a consistent snapshot.
  const [pendingStream, setPendingStream] = useState<AddStreamRequestStream | null>(null);
  const [pendingProceed, setPendingProceed] = useState<OnProceedCreate | null>(null);

  const close = useCallback(() => {
    setModalState(CLOSED_STATE);
    setPendingStream(null);
    setPendingProceed(null);
  }, []);

  const requestAddStream = useCallback(
    async (
      stream: AddStreamRequestStream,
      groupId: number | null,
      onProceed: OnProceedCreate,
    ): Promise<AddStreamDedupOutcome> => {
      let response;
      try {
        response = await getChannelMergeCandidates(stream.name, groupId);
      } catch (err) {
        // Fall through: never silently swallow the operator's click. The
        // dedup prompt is a courtesy; skipping it on a lookup failure is
        // strictly safer than leaving the operator with a dead button.
        logger.warn(
          '[ADD-STREAM-DEDUP] candidates lookup failed; falling through to create-channel path:',
          err,
        );
        await onProceed();
        return 'lookup_failed';
      }

      const candidate = response.candidates[0] ?? null;
      if (!candidate) {
        // No candidate cleared the §D2 floor — proceed with the original
        // create-channel path (auto-creation rules apply there as usual).
        await onProceed();
        return 'no_candidate';
      }

      setPendingStream(stream);
      // Store the proceed callback as state. React's setState accepts a
      // functional updater for state of any shape; wrap the function so it
      // is set as the value rather than invoked as a reducer.
      setPendingProceed(() => onProceed);
      setModalState({
        isOpen: true,
        streamName: stream.name,
        candidate,
      });
      return 'candidate';
    },
    [],
  );

  const handleMerge = useCallback(
    async (channelId: string): Promise<void> => {
      if (!pendingStream) {
        // Defensive: if there is no pending stream the modal should already
        // be closed; treat this as a no-op rather than throwing.
        return;
      }
      // ADR-008 §D8: candidate.channel_id is TEXT in pending_merges and the
      // BD-D response casts to string. The /api/channels/{id}/add-stream
      // endpoint takes the numeric Dispatcharr id, so we parse here. Use
      // base-10 explicitly; a string like "0x" would otherwise produce NaN
      // and surface as a 422 from the backend, which is at least visible.
      const numericId = parseInt(channelId, 10);
      if (stageAddStream) {
        stageAddStream(
          numericId,
          pendingStream.id,
          `Add stream "${pendingStream.name}" to channel ${numericId}`,
        );
        close();
        return;
      }
      try {
        await addStreamToChannel(numericId, pendingStream.id);
      } finally {
        // Close the modal whether the backend call succeeded or threw —
        // the modal surfaces its own error banner, but the BD-I hook does
        // not retry transparently. On failure the operator can re-trigger
        // the Add Stream click.
        close();
      }
    },
    [pendingStream, close, stageAddStream],
  );

  const handleCreateNew = useCallback(async (): Promise<void> => {
    const proceed = pendingProceed;
    // Close before invoking the proceed callback so the original modal
    // path (bulk create / direct create) does not race the dedup modal's
    // unmount when both modals share overlay z-index.
    close();
    if (proceed) {
      await proceed();
    }
  }, [pendingProceed, close]);

  const handleCancel = useCallback((): void => {
    // BD-I spec: no card to bounce back, just close the modal.
    close();
  }, [close]);

  return {
    modalState,
    requestAddStream,
    handleMerge,
    handleCreateNew,
    handleCancel,
  };
}
