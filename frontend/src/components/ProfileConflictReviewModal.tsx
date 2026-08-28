import { useCallback, useEffect, useRef, useState } from 'react';

import { acceptProfileConflictReview, getProfileConflictReviews } from '../services/api';
import type { ProfileConflictChoice, ProfileConflictReview } from '../types/profileConflict';
import { useOwnedDialog } from '../hooks/useOwnedDialog';
import './ProfileConflictReviewModal.css';

export const PROFILE_CONFLICT_REVIEW_EVENT = 'ecm:open-profile-conflict-review';
export const PROFILE_CONFLICT_REVIEW_STORAGE_KEY = 'ecm:profile-conflicts:dismissed';

interface OpenReviewIntent {
  reviewId?: number;
  fingerprint?: string;
  forceFirst?: boolean;
}

function readDismissed(): Set<string> {
  try {
    const value = JSON.parse(sessionStorage.getItem(PROFILE_CONFLICT_REVIEW_STORAGE_KEY) || '[]');
    return new Set(Array.isArray(value) ? value.filter((item) => typeof item === 'string') : []);
  } catch {
    return new Set();
  }
}

function sourceNames(choice: ProfileConflictChoice): string {
  if (!Array.isArray(choice.sources)) return 'Unknown';
  const names = choice.sources
    .map((source) => source?.source_group_name)
    .filter((name): name is string => typeof name === 'string' && Boolean(name.trim()));
  return names.join(', ') || 'Unknown';
}

function accountNames(choice: ProfileConflictChoice): string {
  if (!Array.isArray(choice.sources)) return 'Unknown';
  const names = choice.sources
    .map((source) => source?.m3u_account_name)
    .filter((name): name is string => typeof name === 'string' && Boolean(name.trim()));
  return [...new Set(names)].join(', ') || 'Unknown';
}

function profileNames(choice: ProfileConflictChoice): string {
  if (!Array.isArray(choice.profile_names)) return 'No profiles';
  const names = choice.profile_names.filter(
    (name): name is string => typeof name === 'string' && Boolean(name.trim()),
  );
  return names.join(' + ') || 'No profiles';
}

function reviewIntent(event: Event): OpenReviewIntent {
  const detail = (event as CustomEvent<unknown>).detail;
  if (!detail || typeof detail !== 'object') return { forceFirst: true };
  const candidate = detail as Record<string, unknown>;
  return {
    reviewId: typeof candidate.review_id === 'number' ? candidate.review_id : undefined,
    fingerprint: typeof candidate.fingerprint === 'string' ? candidate.fingerprint : undefined,
  };
}

export function ProfileConflictReviewModal() {
  const [reviews, setReviews] = useState<ProfileConflictReview[]>([]);
  const [dismissed, setDismissed] = useState(readDismissed);
  const [forcedFingerprint, setForcedFingerprint] = useState<string | null>(null);
  const [selectedChoice, setSelectedChoice] = useState('');
  const [applying, setApplying] = useState(false);
  const [message, setMessage] = useState<{ kind: 'error' | 'warning'; text: string } | null>(null);
  const [recovery, setRecovery] = useState<'load-error' | 'not-found' | null>(null);
  const acceptControllerRef = useRef<AbortController | null>(null);
  const latestLoadRef = useRef(0);
  const openIntentRef = useRef<OpenReviewIntent | null>(null);
  const current = recovery ? undefined : (
    reviews.find((review) => review.fingerprint === forcedFingerprint)
      ?? reviews.find((review) => !dismissed.has(review.fingerprint))
  );
  const activeFingerprint = current?.fingerprint;
  const savedChoice = current?.status === 'accepted' ? current.accepted_choice_key ?? '' : '';
  const { titleId, containerRef } = useOwnedDialog(Boolean(current || recovery));

  const loadReviews = useCallback(async (
    intent?: OpenReviewIntent,
    recoverInitialFailure = false,
  ) => {
    if (!intent && openIntentRef.current) return;
    if (intent) openIntentRef.current = intent;
    const requestId = ++latestLoadRef.current;
    let completedIntent = false;
    try {
      const response = await getProfileConflictReviews();
      if (requestId !== latestLoadRef.current) return;
      setReviews(response.reviews);
      if (!intent) return;

      const requested = response.reviews.find((review) => {
        if (intent.reviewId !== undefined && review.id !== intent.reviewId) return false;
        if (intent.fingerprint !== undefined && review.fingerprint !== intent.fingerprint) return false;
        return intent.reviewId !== undefined || intent.fingerprint !== undefined;
      });
      if (requested) {
        setForcedFingerprint(requested.fingerprint);
        setRecovery(null);
      } else if (intent.forceFirst) {
        setForcedFingerprint(response.reviews[0]?.fingerprint ?? null);
        setRecovery(null);
      } else {
        setForcedFingerprint(null);
        setRecovery('not-found');
      }
      completedIntent = true;
    } catch {
      if (requestId === latestLoadRef.current && (intent || recoverInitialFailure)) {
        setRecovery('load-error');
      }
    } finally {
      if (
        completedIntent
        && requestId === latestLoadRef.current
        && openIntentRef.current === intent
      ) {
        openIntentRef.current = null;
      }
    }
  }, []);

  useEffect(() => {
    void loadReviews(undefined, true);
    const timer = window.setInterval(() => void loadReviews(), 30_000);
    const reopen = (event: Event) => {
      const intent = reviewIntent(event);
      openIntentRef.current = intent;
      void loadReviews(intent);
    };
    window.addEventListener(PROFILE_CONFLICT_REVIEW_EVENT, reopen);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener(PROFILE_CONFLICT_REVIEW_EVENT, reopen);
      acceptControllerRef.current?.abort();
    };
  }, [loadReviews]);

  useEffect(() => {
    setSelectedChoice(savedChoice);
  }, [activeFingerprint, savedChoice]);

  useEffect(() => {
    setMessage(null);
  }, [activeFingerprint]);

  const dismissCurrent = useCallback(() => {
    if (!current) return;
    const next = new Set(dismissed).add(current.fingerprint);
    try {
      sessionStorage.setItem(PROFILE_CONFLICT_REVIEW_STORAGE_KEY, JSON.stringify([...next]));
    } catch {
      // Dismissal remains available when browser storage is blocked.
    }
    acceptControllerRef.current?.abort();
    acceptControllerRef.current = null;
    setApplying(false);
    setDismissed(next);
    setForcedFingerprint(null);
  }, [current, dismissed]);

  useEffect(() => {
    if (!current) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') dismissCurrent();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [current, dismissCurrent]);

  const applyChoice = async () => {
    if (!current || !selectedChoice || applying) return;
    const controller = new AbortController();
    acceptControllerRef.current = controller;
    setApplying(true);
    setMessage(null);
    try {
      const outcome = await acceptProfileConflictReview(current.id, selectedChoice, controller.signal);
      if (acceptControllerRef.current !== controller || controller.signal.aborted) return;
      if (outcome.applied) {
        setReviews((items) => items.filter((review) => review.id !== current.id));
        setForcedFingerprint(null);
      } else {
        setReviews((items) => items.map((review) => review.id === current.id ? {
          ...review,
          status: 'accepted',
          accepted_choice_key: selectedChoice,
          accepted_profile_ids: review.evidence?.choices?.find(
            (choice) => choice.choice_key === selectedChoice,
          )?.profile_ids ?? [],
          retry_error: outcome.retry_error,
        } : review));
        setMessage({
          kind: 'warning',
          text: 'Your choice was saved, but one or more M3U accounts failed. ECM will retry automatically.',
        });
      }
    } catch {
      if (acceptControllerRef.current !== controller || controller.signal.aborted) return;
      setMessage({
        kind: 'error',
        text: 'The choice could not be applied. The conflict may have changed; review the current options and try again.',
      });
    } finally {
      if (acceptControllerRef.current === controller) {
        acceptControllerRef.current = null;
        setApplying(false);
      }
    }
  };

  if (!current && recovery) {
    const loadFailed = recovery === 'load-error';
    const dismissRecovery = () => {
      setRecovery(null);
      openIntentRef.current = null;
    };
    const recover = () => {
      if (loadFailed) {
        void loadReviews(openIntentRef.current ?? { forceFirst: true });
      } else {
        setForcedFingerprint(reviews[0]?.fingerprint ?? null);
        setRecovery(null);
      }
    };
    return (
      <div className="modal-overlay profile-conflict-overlay" data-modal-overlay>
        <div
          ref={containerRef}
          className="modal-container profile-conflict-modal"
          role="dialog"
          aria-modal="true"
          aria-labelledby={titleId}
        >
          <header className="modal-header profile-conflict-header">
            <h2 id={titleId}>Profile conflict review</h2>
            <button className="modal-close-btn" type="button" onClick={dismissRecovery} aria-label="Close">
              <span className="material-icons" aria-hidden="true">close</span>
            </button>
          </header>
          <div className="modal-body profile-conflict-body">
            <div className="profile-conflict-message is-error" role="alert">
              {loadFailed
                ? 'Could not load profile conflicts. Check the connection and try again.'
                : 'This profile conflict is no longer available.'}
            </div>
          </div>
          <footer className="modal-footer profile-conflict-footer">
            <button className="modal-btn modal-btn-secondary" type="button" onClick={dismissRecovery}>Close</button>
            <button className="modal-btn modal-btn-primary" type="button" onClick={recover}>
              {loadFailed ? 'Retry loading' : 'Show current reviews'}
            </button>
          </footer>
        </div>
      </div>
    );
  }
  if (!current) return null;
  const retryingSavedChoice = current.status === 'accepted' && !current.applied_at;
  const choices = Array.isArray(current.evidence?.choices) ? current.evidence.choices : [];
  const targetName = typeof current.evidence?.target?.name === 'string' && current.evidence.target.name.trim()
    ? current.evidence.target.name
    : `Group ${current.effective_group_id}`;
  const visibleMessage = message ?? (retryingSavedChoice ? {
    kind: 'warning' as const,
    text: 'Your choice is saved, but one or more M3U accounts still need that setting. Retry the saved choice now or let ECM retry automatically.',
  } : null);

  return (
    <div className="modal-overlay profile-conflict-overlay" data-modal-overlay>
      <div
        ref={containerRef}
        className="modal-container modal-lg profile-conflict-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <header className="modal-header profile-conflict-header">
          <div>
            <h2 id={titleId}>
              <span className="material-icons" aria-hidden="true">report_problem</span>
              Profile choice required
            </h2>
            <p className="modal-subtitle">Channel membership is frozen until this conflict is resolved.</p>
          </div>
          <button className="modal-close-btn" type="button" onClick={dismissCurrent} aria-label="Close">
            <span className="material-icons" aria-hidden="true">close</span>
          </button>
        </header>

        <div className="modal-body profile-conflict-body">
          <div className="profile-conflict-target">
            <span>Effective channel group</span>
            <strong>{targetName}</strong>
          </div>
          <p className="profile-conflict-intro">
            {retryingSavedChoice
              ? 'The decision below is final and saved. Some source accounts still need the selected setting.'
              : 'Source groups feeding this target disagree about which channel profiles to use. Choose one profile set to apply consistently across every source account.'}
          </p>

          <fieldset className="profile-conflict-choices" disabled={applying}>
            <legend className="sr-only">Choose the channel profiles to apply</legend>
            {choices.map((choice) => (
              <label key={choice.choice_key} className={`profile-conflict-choice${selectedChoice === choice.choice_key ? ' is-selected' : ''}`}>
                <input
                  type="radio"
                  name={`profile-conflict-${current.id}`}
                  value={choice.choice_key}
                  checked={selectedChoice === choice.choice_key}
                  disabled={retryingSavedChoice && current.accepted_choice_key !== choice.choice_key}
                  onChange={() => setSelectedChoice(choice.choice_key)}
                />
                <span className="profile-conflict-choice-copy">
                  <strong>{profileNames(choice)}</strong>
                  <span><b>Source groups:</b> {sourceNames(choice)}</span>
                  <span><b>M3U accounts:</b> {accountNames(choice)}</span>
                </span>
              </label>
            ))}
            {choices.length === 0 && <p>No profile choices are available for this conflict.</p>}
          </fieldset>

          {visibleMessage && (
            <div className={`profile-conflict-message is-${visibleMessage.kind}`} role="alert">
              <span className="material-icons" aria-hidden="true">
                {visibleMessage.kind === 'error' ? 'error_outline' : 'sync_problem'}
              </span>
              {visibleMessage.text}
            </div>
          )}
        </div>

        <footer className="modal-footer profile-conflict-footer">
          <button className="modal-btn modal-btn-secondary" type="button" onClick={dismissCurrent}>
            Decide later
          </button>
          <button className="modal-btn modal-btn-primary" type="button" onClick={() => void applyChoice()} disabled={!selectedChoice || applying}>
            {applying ? 'Applying...' : retryingSavedChoice ? 'Retry saved choice' : 'Apply selected choice'}
          </button>
        </footer>
      </div>
    </div>
  );
}
