import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as api from '../services/api';
import {
  PROFILE_CONFLICT_REVIEW_EVENT,
  PROFILE_CONFLICT_REVIEW_STORAGE_KEY,
  ProfileConflictReviewModal,
} from './ProfileConflictReviewModal';

vi.mock('../services/api', async () => {
  const actual = await vi.importActual<typeof import('../services/api')>('../services/api');
  return {
    ...actual,
    getProfileConflictReviews: vi.fn(),
    acceptProfileConflictReview: vi.fn(),
  };
});

const review = {
  id: 9,
  fingerprint: 'fingerprint-a',
  effective_group_id: 665,
  status: 'pending' as const,
  accepted_choice_key: null,
  accepted_profile_ids: null,
  created_at: 1,
  last_seen_at: 2,
  resolved_at: null,
  applied_at: null,
  retry_error: null,
  evidence: {
    fingerprint_version: 1,
    target: { effective_group_id: 665, name: 'NBA' },
    choices: [
      {
        choice_key: 'choice-sports',
        profile_ids: [6, 7],
        profile_names: ['Sports', 'Family'],
        sources: [
          { source_group_id: 823, source_group_name: 'NBA US', m3u_account_id: 1, m3u_account_name: 'Stryker' },
          { source_group_id: 825, source_group_name: 'NBA CA', m3u_account_id: 1, m3u_account_name: 'Stryker' },
        ],
      },
      {
        choice_key: 'choice-strong',
        profile_ids: [14],
        profile_names: ['Strong only'],
        sources: [
          { source_group_id: 2866, source_group_name: 'NBA Strong', m3u_account_id: 2, m3u_account_name: 'Strong' },
        ],
      },
    ],
  },
};

describe('ProfileConflictReviewModal', () => {
  beforeEach(() => {
    sessionStorage.clear();
    vi.clearAllMocks();
    vi.mocked(api.getProfileConflictReviews).mockResolvedValue({ reviews: [review], total: 1 });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('shows target, profile names, source groups, and M3U account provenance', async () => {
    render(<ProfileConflictReviewModal />);
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('NBA')).toBeInTheDocument();
    expect(within(dialog).getByText('Sports + Family')).toBeInTheDocument();
    expect(within(dialog).getByText(/NBA US.*NBA CA/)).toBeInTheDocument();
    expect(within(dialog).getAllByText(/Stryker/).length).toBeGreaterThan(0);
    await waitFor(() => expect(dialog.contains(document.activeElement)).toBe(true));
    expect(within(dialog).getByRole('button', { name: 'Apply selected choice' })).toBeDisabled();
  });

  it('requires an explicit choice and applies its opaque key', async () => {
    const user = userEvent.setup();
    vi.mocked(api.acceptProfileConflictReview).mockResolvedValue({
      status: 'accepted', applied: true, updated_account_ids: [1, 2],
      failed_account_ids: [], retry_error: null,
    });
    render(<ProfileConflictReviewModal />);
    await user.click(await screen.findByRole('radio', { name: /Sports \+ Family/ }));
    await user.click(screen.getByRole('button', { name: 'Apply selected choice' }));
    await waitFor(() => expect(api.acceptProfileConflictReview).toHaveBeenCalledWith(
      9, 'choice-sports', expect.any(AbortSignal),
    ));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('Decide later, close, and Escape dismiss only this fingerprint for the session', async () => {
    const user = userEvent.setup();
    const { unmount } = render(<ProfileConflictReviewModal />);
    await user.click(await screen.findByRole('button', { name: 'Decide later' }));
    expect(JSON.parse(sessionStorage.getItem(PROFILE_CONFLICT_REVIEW_STORAGE_KEY) || '[]')).toEqual(['fingerprint-a']);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    unmount();

    sessionStorage.clear();
    render(<ProfileConflictReviewModal />);
    await screen.findByRole('dialog');
    fireEvent.keyDown(document, { key: 'Escape' });
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('a changed fingerprint prompts again and a notification action reopens a dismissed review', async () => {
    const user = userEvent.setup();
    render(<ProfileConflictReviewModal />);
    await user.click(await screen.findByRole('button', { name: 'Decide later' }));

    window.dispatchEvent(new CustomEvent(PROFILE_CONFLICT_REVIEW_EVENT));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Decide later' }));

    vi.mocked(api.getProfileConflictReviews).mockResolvedValue({
      reviews: [{ ...review, id: 10, fingerprint: 'fingerprint-b' }], total: 1,
    });
    window.dispatchEvent(new CustomEvent(PROFILE_CONFLICT_REVIEW_EVENT));
    expect(await screen.findByRole('dialog')).toBeInTheDocument();
  });

  it('does not wedge or close on request failure, including stale review responses', async () => {
    const user = userEvent.setup();
    vi.mocked(api.acceptProfileConflictReview).mockRejectedValue(new Error('This conflict changed'));
    render(<ProfileConflictReviewModal />);
    await user.click(await screen.findByRole('radio', { name: /Strong only/ }));
    await user.click(screen.getByRole('button', { name: 'Apply selected choice' }));
    expect(await screen.findByText(/could not be applied/i)).toBeInTheDocument();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Decide later' })).toBeEnabled();
    expect(screen.getByRole('radio', { name: /Strong only/ })).toBeChecked();
  });

  it('keeps a partial account failure visible while explaining automatic retry', async () => {
    const user = userEvent.setup();
    vi.mocked(api.acceptProfileConflictReview).mockResolvedValue({
      status: 'accepted', applied: false, updated_account_ids: [2],
      failed_account_ids: [1], retry_error: 'account 1: down',
    });
    render(<ProfileConflictReviewModal />);
    await user.click(await screen.findByRole('radio', { name: /Sports \+ Family/ }));
    await user.click(screen.getByRole('button', { name: 'Apply selected choice' }));
    expect(await screen.findByText(/saved.*retry/i)).toBeInTheDocument();
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /Sports \+ Family/ })).toBeChecked();
    expect(screen.getByRole('radio', { name: /Strong only/ })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Retry saved choice' })).toBeEnabled();
  });

  it('reopens an accepted partial decision as a retry without allowing a different choice', async () => {
    const user = userEvent.setup();
    vi.mocked(api.getProfileConflictReviews).mockResolvedValue({
      reviews: [{
        ...review,
        status: 'accepted',
        accepted_choice_key: 'choice-sports',
        accepted_profile_ids: [6, 7],
        retry_error: 'account 1: down',
      }],
      total: 1,
    });
    vi.mocked(api.acceptProfileConflictReview).mockResolvedValue({
      status: 'accepted', applied: true, updated_account_ids: [1],
      failed_account_ids: [], retry_error: null,
    });
    render(<ProfileConflictReviewModal />);
    const savedChoice = await screen.findByRole('radio', { name: /Sports \+ Family/ });
    await waitFor(() => expect(savedChoice).toBeChecked());
    expect(screen.getByRole('radio', { name: /Strong only/ })).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Retry saved choice' }));
    expect(api.acceptProfileConflictReview).toHaveBeenCalledWith(
      9, 'choice-sports', expect.any(AbortSignal),
    );
  });

  it('allows Decide later during a stalled accept and ignores its stale completion', async () => {
    const user = userEvent.setup();
    let resolveAccept!: (value: api.AcceptProfileConflictOutcome) => void;
    vi.mocked(api.acceptProfileConflictReview).mockImplementation(() => new Promise((resolve) => {
      resolveAccept = resolve;
    }));
    render(<ProfileConflictReviewModal />);
    await user.click(await screen.findByRole('radio', { name: /Sports \+ Family/ }));
    await user.click(screen.getByRole('button', { name: 'Apply selected choice' }));

    const decideLater = screen.getByRole('button', { name: 'Decide later' });
    expect(decideLater).toBeEnabled();
    expect(screen.getByRole('button', { name: 'Close' })).toBeEnabled();
    await user.click(decideLater);
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    resolveAccept({
      status: 'accepted', applied: true, updated_account_ids: [1, 2],
      failed_account_ids: [], retry_error: null,
    });
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
  });

  it('dismisses even when sessionStorage rejects the write', async () => {
    const user = userEvent.setup();
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new DOMException('blocked');
    });
    render(<ProfileConflictReviewModal />);

    await user.click(await screen.findByRole('button', { name: 'Decide later' }));

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('reopens the review named by notification metadata instead of the first row', async () => {
    vi.mocked(api.getProfileConflictReviews).mockResolvedValue({
      reviews: [
        review,
        {
          ...review,
          id: 10,
          fingerprint: 'fingerprint-b',
          effective_group_id: 777,
          evidence: { ...review.evidence, target: { effective_group_id: 777, name: 'NFL' } },
        },
      ],
      total: 2,
    });
    sessionStorage.setItem(
      PROFILE_CONFLICT_REVIEW_STORAGE_KEY,
      JSON.stringify(['fingerprint-a', 'fingerprint-b']),
    );
    render(<ProfileConflictReviewModal />);
    await waitFor(() => expect(api.getProfileConflictReviews).toHaveBeenCalled());

    window.dispatchEvent(new CustomEvent(PROFILE_CONFLICT_REVIEW_EVENT, {
      detail: { review_id: 10, fingerprint: 'fingerprint-b' },
    }));

    expect(await screen.findByText('NFL')).toBeInTheDocument();
    expect(screen.queryByText('NBA')).not.toBeInTheDocument();
  });

  it('offers recovery when a notification points at a stale review id', async () => {
    const user = userEvent.setup();
    render(<ProfileConflictReviewModal />);
    await waitFor(() => expect(api.getProfileConflictReviews).toHaveBeenCalled());

    window.dispatchEvent(new CustomEvent(PROFILE_CONFLICT_REVIEW_EVENT, {
      detail: { review_id: 999, fingerprint: 'gone' },
    }));

    expect(await screen.findByText(/no longer available/i)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Show current reviews' }));
    expect(await screen.findByText('NBA')).toBeInTheDocument();
  });

  it('shows a retry action when a notification-driven list load fails', async () => {
    const user = userEvent.setup();
    vi.mocked(api.getProfileConflictReviews).mockRejectedValue(new Error('offline'));
    render(<ProfileConflictReviewModal />);
    window.dispatchEvent(new CustomEvent(PROFILE_CONFLICT_REVIEW_EVENT, {
      detail: { review_id: 9, fingerprint: 'fingerprint-a' },
    }));

    expect(await screen.findByText(/could not load profile conflicts/i)).toBeInTheDocument();
    vi.mocked(api.getProfileConflictReviews).mockResolvedValue({ reviews: [review], total: 1 });
    await user.click(screen.getByRole('button', { name: 'Retry loading' }));
    expect(await screen.findByText('NBA')).toBeInTheDocument();
  });

  it('renders empty profile choices and malformed evidence safely', async () => {
    vi.mocked(api.getProfileConflictReviews).mockResolvedValue({
      reviews: [{
        ...review,
        evidence: {
          ...review.evidence,
          choices: [{ ...review.evidence.choices[0], profile_ids: [], profile_names: [] }],
        },
      }],
      total: 1,
    });
    const { unmount } = render(<ProfileConflictReviewModal />);
    expect(await screen.findByText('No profiles')).toBeInTheDocument();
    unmount();

    vi.mocked(api.getProfileConflictReviews).mockResolvedValue({
      reviews: [{ ...review, evidence: {} as never }], total: 1,
    });
    render(<ProfileConflictReviewModal />);
    expect(await screen.findByText('Group 665')).toBeInTheDocument();
    expect(screen.getByText(/no profile choices are available/i)).toBeInTheDocument();
  });
});
