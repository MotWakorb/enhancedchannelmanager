/**
 * Unit tests for StreamDedupModal — operator decision surface for
 * stream-to-channel deduplication (BD-G / bd-4vxjj, ADR-008 §D1 + §D2).
 *
 * This modal asks the operator: "we found this candidate channel for the
 * incoming stream — merge into it, create a new channel, or cancel?". It is
 * a DECISION surface, distinct from MergeChannelsModal which is an EDITING
 * surface for an already-decided merge.
 *
 * The tests below lock the ADR-008 §D1 ratifications:
 *
 *   - No-candidate state shows "no candidate found", Merge disabled.
 *   - Fuzzy candidates render confidence and DO NOT autofocus Merge.
 *   - Exact-match (100%) candidates DO autofocus Merge (§D2).
 *   - Tab order: Cancel → Create New → Merge.
 *   - Focus is trapped inside the modal (Tab wraps from last → first).
 *   - Merge button calls onMerge with the candidate's channel_id.
 *   - When onMerge rejects, the modal stays open, the inline error banner
 *     shows the backend detail, and the Merge button re-enables for retry.
 *   - prefers-reduced-motion disables modal-open animation.
 *   - Confidence is rendered as a whole-percent badge (0.92 → "92%").
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { StreamDedupModal, type DedupCandidate } from './StreamDedupModal';

const FUZZY_CANDIDATE: DedupCandidate = {
  channel_id: 'channel-uuid-abc',
  channel_name: 'ESPN HD',
  confidence: 0.92,
};

const EXACT_CANDIDATE: DedupCandidate = {
  channel_id: 'channel-uuid-xyz',
  channel_name: 'ESPN HD',
  confidence: 1.0,
};

function makeProps(overrides: Partial<React.ComponentProps<typeof StreamDedupModal>> = {}) {
  return {
    isOpen: true,
    streamName: 'ESPN HD',
    candidate: FUZZY_CANDIDATE,
    trigger: 'drag_drop' as const,
    onMerge: vi.fn().mockResolvedValue(undefined),
    onCreateNew: vi.fn().mockResolvedValue(undefined),
    onCancel: vi.fn(),
    ...overrides,
  };
}

describe('StreamDedupModal — render gates (ADR-008 §D1)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders nothing when isOpen is false', () => {
    const props = makeProps({ isOpen: false });
    const { container } = render(<StreamDedupModal {...props} />);
    expect(container.firstChild).toBeNull();
  });

  it('shows the incoming stream name in the body', () => {
    render(<StreamDedupModal {...makeProps({ streamName: 'CNN HD' })} />);
    expect(screen.getByText(/CNN HD/)).toBeInTheDocument();
  });
});

describe('StreamDedupModal — no-candidate state (ADR-008 §D2 floor)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the "no candidate found" empty state when candidate is null', () => {
    render(<StreamDedupModal {...makeProps({ candidate: null })} />);
    expect(screen.getByText(/no candidate found/i)).toBeInTheDocument();
  });

  it('disables the Merge button when there is no candidate', () => {
    render(<StreamDedupModal {...makeProps({ candidate: null })} />);
    const merge = screen.getByRole('button', { name: /^merge$/i });
    expect(merge).toBeDisabled();
  });

  it('still renders Cancel and Create New when there is no candidate', () => {
    render(<StreamDedupModal {...makeProps({ candidate: null })} />);
    expect(screen.getByRole('button', { name: /cancel/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /create new/i })).toBeInTheDocument();
  });
});

describe('StreamDedupModal — confidence display and focus (ADR-008 §D1 + §D2)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders confidence as a whole-percent badge (0.92 → "92%")', () => {
    render(<StreamDedupModal {...makeProps({ candidate: FUZZY_CANDIDATE })} />);
    // Badge text is "92% match"; the percent number is the load-bearing
    // piece — match it via the aria-label which is also exercised by AT.
    const badge = screen.getByLabelText(/Confidence: 92 percent/i);
    expect(badge).toBeInTheDocument();
    expect(badge.textContent).toContain('92%');
  });

  it('does NOT autofocus the Merge button on fuzzy matches', () => {
    render(<StreamDedupModal {...makeProps({ candidate: FUZZY_CANDIDATE })} />);
    const merge = screen.getByRole('button', { name: /^merge$/i });
    expect(merge).not.toHaveFocus();
  });

  it('autofocuses the Merge button on exact (100%) matches', async () => {
    render(<StreamDedupModal {...makeProps({ candidate: EXACT_CANDIDATE })} />);
    const merge = screen.getByRole('button', { name: /^merge$/i });
    await waitFor(() => {
      expect(merge).toHaveFocus();
    });
  });

  it('renders an "Exact match" badge for 100% candidates', () => {
    render(<StreamDedupModal {...makeProps({ candidate: EXACT_CANDIDATE })} />);
    expect(screen.getByLabelText('Exact match')).toBeInTheDocument();
  });
});

describe('StreamDedupModal — tab order and focus trap (ADR-008 §D1)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('places action buttons in tab order: Cancel → Create New → Merge', () => {
    render(<StreamDedupModal {...makeProps()} />);
    const cancel = screen.getByRole('button', { name: /cancel/i });
    const createNew = screen.getByRole('button', { name: /create new/i });
    const merge = screen.getByRole('button', { name: /^merge$/i });

    // All three live in the same footer container; document order encodes
    // the tab order under default tabindex=0.
    const buttons = Array.from(
      document.querySelectorAll<HTMLButtonElement>('.modal-footer button'),
    );
    expect(buttons).toEqual([cancel, createNew, merge]);
  });

  it('traps focus inside the modal — Tab from the last button wraps to the first', async () => {
    const user = userEvent.setup();
    render(<StreamDedupModal {...makeProps({ candidate: FUZZY_CANDIDATE })} />);

    const cancel = screen.getByRole('button', { name: /cancel/i });
    const merge = screen.getByRole('button', { name: /^merge$/i });

    // Start focus on the last interactive element (Merge) and Tab forward.
    merge.focus();
    expect(merge).toHaveFocus();

    await user.tab();
    expect(cancel).toHaveFocus();
  });

  it('traps focus inside the modal — Shift+Tab from the first wraps to the last', async () => {
    const user = userEvent.setup();
    render(<StreamDedupModal {...makeProps({ candidate: FUZZY_CANDIDATE })} />);

    const cancel = screen.getByRole('button', { name: /cancel/i });
    const merge = screen.getByRole('button', { name: /^merge$/i });

    cancel.focus();
    expect(cancel).toHaveFocus();

    await user.tab({ shift: true });
    expect(merge).toHaveFocus();
  });
});

describe('StreamDedupModal — action callbacks (ADR-008 §D1)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('Merge button calls onMerge with the candidate.channel_id', async () => {
    const onMerge = vi.fn().mockResolvedValue(undefined);
    render(<StreamDedupModal {...makeProps({ onMerge })} />);

    fireEvent.click(screen.getByRole('button', { name: /^merge$/i }));

    await waitFor(() => {
      expect(onMerge).toHaveBeenCalledWith(FUZZY_CANDIDATE.channel_id);
    });
  });

  it('Create New button calls onCreateNew', async () => {
    const onCreateNew = vi.fn().mockResolvedValue(undefined);
    render(<StreamDedupModal {...makeProps({ onCreateNew })} />);

    fireEvent.click(screen.getByRole('button', { name: /create new/i }));

    await waitFor(() => {
      expect(onCreateNew).toHaveBeenCalledTimes(1);
    });
  });

  it('Cancel button calls onCancel (parent controls isOpen)', () => {
    const onCancel = vi.fn();
    render(<StreamDedupModal {...makeProps({ onCancel })} />);

    fireEvent.click(screen.getByRole('button', { name: /cancel/i }));

    expect(onCancel).toHaveBeenCalledTimes(1);
  });
});

describe('StreamDedupModal — async merge error (ADR-008 §D1)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('keeps the modal open and surfaces the backend detail when onMerge rejects', async () => {
    const detail = 'target channel no longer exists — dismiss this pending merge and refresh';
    const onMerge = vi.fn().mockRejectedValue(new Error(detail));
    const onCancel = vi.fn();
    render(<StreamDedupModal {...makeProps({ onMerge, onCancel })} />);

    fireEvent.click(screen.getByRole('button', { name: /^merge$/i }));

    await waitFor(() => {
      expect(screen.getByText(detail)).toBeInTheDocument();
    });
    // Parent's onCancel must NOT fire on a rejected merge — the modal stays
    // open so the operator can retry.
    expect(onCancel).not.toHaveBeenCalled();
  });

  it('re-enables the Merge button after a rejection so the operator can retry', async () => {
    const onMerge = vi.fn().mockRejectedValue(new Error('flaky network'));
    render(<StreamDedupModal {...makeProps({ onMerge })} />);

    const merge = screen.getByRole('button', { name: /^merge$/i });
    fireEvent.click(merge);

    await waitFor(() => {
      expect(screen.getByText('flaky network')).toBeInTheDocument();
    });
    expect(merge).not.toBeDisabled();
  });
});

describe('StreamDedupModal — prefers-reduced-motion (ADR-008 §D1)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('applies the reduced-motion class when matchMedia matches', () => {
    // setup.ts mocks matchMedia to always return matches=false; override here.
    const originalMatchMedia = window.matchMedia;
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: query.includes('prefers-reduced-motion'),
      media: query,
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })) as unknown as typeof window.matchMedia;

    try {
      render(<StreamDedupModal {...makeProps()} />);
      const container = document.querySelector('.stream-dedup-modal');
      expect(container).not.toBeNull();
      expect(container?.classList.contains('is-reduced-motion')).toBe(true);
    } finally {
      window.matchMedia = originalMatchMedia;
    }
  });

  it('does NOT apply the reduced-motion class when matchMedia does not match', () => {
    // Default setup.ts behavior already returns matches=false.
    render(<StreamDedupModal {...makeProps()} />);
    const container = document.querySelector('.stream-dedup-modal');
    expect(container?.classList.contains('is-reduced-motion')).toBe(false);
  });
});
