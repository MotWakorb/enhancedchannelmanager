/**
 * Tests for LogoMissBanner — the restore-complete logo-miss RED banner
 * (bead 0i2vt.19, ADR-012 D9 "unmissable surfacing").
 *
 * Contract note: the merged `RestoreReport` (docs/dbas_restore_contracts.md)
 * carries the logo-miss signal as an AGGREGATE count only — `logo_misses: number`
 * — not a per-channel list. ADR-012 D9 mandates exactly that: "a WARN log + an
 * aggregate count + a prominent red banner". So the banner names the count and
 * links to the Dispatcharr Channels page where the operator fixes the logos
 * one-off; it does NOT (because it cannot) enumerate per-channel rows — there is
 * no per-channel data in the contract. The "detail view" the banner offers is
 * the Dispatcharr Channels admin page, reached via the same `${dispatcharrUrl}/…`
 * link pattern the rest of the app uses (ChannelsPane).
 */
import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { LogoMissBanner } from './LogoMissBanner';
import { RestoreCompleteSummary } from './RestoreCompleteSummary';
import type { RestoreReport } from '../services/api';

function report(overrides: Partial<RestoreReport> = {}): RestoreReport {
  return {
    contract_version: 1,
    is_dry_run: false,
    outcome: 'success',
    logo_misses: 0,
    started_at: null,
    completed_at: null,
    notes: [],
    categories: [],
    ...overrides,
  };
}

const DISPATCHARR_URL = 'https://dispatcharr.example.test';

describe('LogoMissBanner — render gating', () => {
  it('does NOT render when logo_misses is 0 (all logos attached)', () => {
    const { container } = render(
      <LogoMissBanner report={report({ logo_misses: 0 })} dispatcharrUrl={DISPATCHARR_URL} />,
    );
    expect(container).toBeEmptyDOMElement();
    expect(screen.queryByTestId('logo-miss-banner')).not.toBeInTheDocument();
  });

  it('renders when logo_misses > 0', () => {
    render(<LogoMissBanner report={report({ logo_misses: 12 })} dispatcharrUrl={DISPATCHARR_URL} />);
    expect(screen.getByTestId('logo-miss-banner')).toBeInTheDocument();
  });
});

describe('LogoMissBanner — copy names the count (plain language, colorblind-safe)', () => {
  it('names the exact count in plain language', () => {
    render(<LogoMissBanner report={report({ logo_misses: 12 })} dispatcharrUrl={DISPATCHARR_URL} />);
    const banner = screen.getByTestId('logo-miss-banner');
    // Count is surfaced and the word "missing" carries the meaning (not red-only;
    // WCAG 1.4.1 — colorblind-safe).
    expect(within(banner).getByText(/12 channels are missing their logo/i)).toBeInTheDocument();
    expect(within(banner).getByText(/missing/i)).toBeInTheDocument();
  });

  it('uses a singular phrasing for a single miss', () => {
    render(<LogoMissBanner report={report({ logo_misses: 1 })} dispatcharrUrl={DISPATCHARR_URL} />);
    const banner = screen.getByTestId('logo-miss-banner');
    expect(within(banner).getByText(/1 channel is missing its logo/i)).toBeInTheDocument();
  });

  it('renders a warning icon (icon + text, never colour alone)', () => {
    render(<LogoMissBanner report={report({ logo_misses: 5 })} dispatcharrUrl={DISPATCHARR_URL} />);
    const banner = screen.getByTestId('logo-miss-banner');
    expect(within(banner).getByTestId('logo-miss-icon')).toBeInTheDocument();
  });
});

describe('LogoMissBanner — accessibility', () => {
  it('has role="alert" so it is announced', () => {
    render(<LogoMissBanner report={report({ logo_misses: 3 })} dispatcharrUrl={DISPATCHARR_URL} />);
    expect(screen.getByRole('alert')).toBe(screen.getByTestId('logo-miss-banner'));
  });
});

describe('LogoMissBanner — detail/fix link to the Dispatcharr Channels page', () => {
  it('links to the Dispatcharr Channels page built from the base url (existing ${dispatcharrUrl}/… pattern)', () => {
    render(<LogoMissBanner report={report({ logo_misses: 4 })} dispatcharrUrl={DISPATCHARR_URL} />);
    const link = screen.getByTestId('logo-miss-detail-link');
    expect(link).toHaveAttribute('href', `${DISPATCHARR_URL}/channels`);
    // Opens the Dispatcharr UI in a new tab, hardened against reverse-tabnabbing.
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', expect.stringContaining('noopener'));
  });

  it('strips a trailing slash on the base url so the link is not doubled', () => {
    render(
      <LogoMissBanner
        report={report({ logo_misses: 2 })}
        dispatcharrUrl={`${DISPATCHARR_URL}/`}
      />,
    );
    expect(screen.getByTestId('logo-miss-detail-link')).toHaveAttribute(
      'href',
      `${DISPATCHARR_URL}/channels`,
    );
  });

  it('omits the fix link (but still warns) when no Dispatcharr base url is known', () => {
    render(<LogoMissBanner report={report({ logo_misses: 7 })} />);
    expect(screen.getByTestId('logo-miss-banner')).toBeInTheDocument();
    expect(within(screen.getByTestId('logo-miss-banner')).getByText(/7 channels/i)).toBeInTheDocument();
    expect(screen.queryByTestId('logo-miss-detail-link')).not.toBeInTheDocument();
  });
});

describe('LogoMissBanner — integration through RestoreCompleteSummary bannerSlot', () => {
  it('appears above the outcome banner when passed through the summary bannerSlot', () => {
    const r = report({ logo_misses: 9 });
    render(
      <RestoreCompleteSummary
        report={r}
        mode="applied"
        bannerSlot={<LogoMissBanner report={r} dispatcharrUrl={DISPATCHARR_URL} />}
      />,
    );

    const logoBanner = screen.getByTestId('logo-miss-banner');
    const outcomeBanner = screen.getByTestId('rcs-outcome-banner');
    expect(logoBanner).toBeInTheDocument();
    expect(outcomeBanner).toBeInTheDocument();

    // The logo-miss banner must precede the outcome banner in document order.
    expect(logoBanner.compareDocumentPosition(outcomeBanner) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('does not inject a banner through the slot when there are no logo misses', () => {
    const r = report({ logo_misses: 0 });
    render(
      <RestoreCompleteSummary
        report={r}
        mode="applied"
        bannerSlot={<LogoMissBanner report={r} dispatcharrUrl={DISPATCHARR_URL} />}
      />,
    );
    expect(screen.queryByTestId('logo-miss-banner')).not.toBeInTheDocument();
    // The summary itself is unaffected.
    expect(screen.getByTestId('restore-complete-summary')).toBeInTheDocument();
  });
});
