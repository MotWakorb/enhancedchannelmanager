/**
 * Tests for RestoreCompleteSummary — the aggregate restore-result surface
 * (bead 0i2vt.20). Renders the tri-state outcome banner + per-entity
 * created/updated/skipped/failed breakdown, and mirrors the dry-run preview
 * shape (same component, `mode` prop).
 */
import { describe, it, expect } from 'vitest';
import { render, screen, within, fireEvent } from '@testing-library/react';
import { RestoreCompleteSummary } from './RestoreCompleteSummary';
import type {
  EntityCategoryReport,
  RestoreReport,
} from '../services/api';

function category(overrides: Partial<EntityCategoryReport>): EntityCategoryReport {
  return {
    entity_type: 'channel',
    created: 0,
    updated: 0,
    skipped: 0,
    failed: 0,
    would_create: 0,
    would_update: 0,
    would_skip: 0,
    skip_details: [],
    failure_details: [],
    ...overrides,
  };
}

function appliedReport(overrides: Partial<RestoreReport> = {}): RestoreReport {
  return {
    contract_version: 1,
    is_dry_run: false,
    outcome: 'success',
    logo_misses: 0,
    started_at: null,
    completed_at: null,
    notes: [],
    categories: [
      category({
        entity_type: 'channel',
        created: 12,
        updated: 3,
        skipped: 2,
        failed: 0,
        skip_details: [
          { reason: 'already_exists_identical', label: 'CNN HD' },
          { reason: 'excluded_by_operator', label: 'Local Access 5' },
        ],
      }),
    ],
    ...overrides,
  };
}

function dryRunReport(overrides: Partial<RestoreReport> = {}): RestoreReport {
  return {
    contract_version: 1,
    is_dry_run: true,
    outcome: null,
    logo_misses: 0,
    started_at: null,
    completed_at: null,
    notes: [],
    categories: [
      category({
        entity_type: 'channel',
        would_create: 8,
        would_update: 1,
        would_skip: 4,
        skip_details: [{ reason: 'already_exists_identical', label: 'ESPN' }],
      }),
    ],
    ...overrides,
  };
}

describe('RestoreCompleteSummary — per-entity counts', () => {
  it('renders created/updated/skipped/failed counts for an applied category', () => {
    render(<RestoreCompleteSummary report={appliedReport()} />);
    const row = screen.getByTestId('rcs-category-channel');
    expect(within(row).getByTestId('rcs-count-created')).toHaveTextContent('12');
    expect(within(row).getByTestId('rcs-count-updated')).toHaveTextContent('3');
    expect(within(row).getByTestId('rcs-count-skipped')).toHaveTextContent('2');
    expect(within(row).getByTestId('rcs-count-failed')).toHaveTextContent('0');
  });

  it('labels the category by its human-readable entity name', () => {
    render(<RestoreCompleteSummary report={appliedReport()} />);
    const row = screen.getByTestId('rcs-category-channel');
    expect(within(row).getByText('Channels')).toBeInTheDocument();
  });

  it('renders a row per category', () => {
    const report = appliedReport({
      categories: [
        category({ entity_type: 'm3u_account', created: 1 }),
        category({ entity_type: 'epg_source', created: 2 }),
        category({ entity_type: 'channel', created: 3 }),
      ],
    });
    render(<RestoreCompleteSummary report={report} />);
    expect(screen.getByTestId('rcs-category-m3u_account')).toBeInTheDocument();
    expect(screen.getByTestId('rcs-category-epg_source')).toBeInTheDocument();
    expect(screen.getByTestId('rcs-category-channel')).toBeInTheDocument();
  });

  it('handles an empty/zero-count category gracefully', () => {
    const report = appliedReport({
      categories: [category({ entity_type: 'logo' })],
    });
    render(<RestoreCompleteSummary report={report} />);
    const row = screen.getByTestId('rcs-category-logo');
    expect(within(row).getByTestId('rcs-count-created')).toHaveTextContent('0');
    // A zero-count category exposes no expandable reason detail.
    expect(within(row).queryByTestId('rcs-skip-details')).not.toBeInTheDocument();
    expect(within(row).queryByTestId('rcs-failure-details')).not.toBeInTheDocument();
  });

  it('renders gracefully with no categories at all', () => {
    const report = appliedReport({ categories: [] });
    render(<RestoreCompleteSummary report={report} />);
    expect(screen.getByTestId('rcs-empty')).toBeInTheDocument();
  });

  it('labels the settings category (core settings / comskip apply counts)', () => {
    // Bead lc6zu: the backend report carries an EntityType.SETTINGS category
    // (updated/skipped apply counts for core_settings + comskip) — it must
    // render with a human label, not an undefined lookup.
    // Bead dfkbn renamed the label to "Dispatcharr settings" because a SECOND
    // settings category now exists (`ecm_settings`), and a report showing two
    // rows both labelled "Settings" is exactly the ambiguity that let the drill
    // read `settings updated=7` as proof ECM's own settings had been restored.
    const report = appliedReport({
      categories: [category({ entity_type: 'settings', updated: 2, skipped: 1 })],
    });
    render(<RestoreCompleteSummary report={report} />);
    const row = screen.getByTestId('rcs-category-settings');
    expect(within(row).getByText('Dispatcharr settings')).toBeInTheDocument();
    expect(within(row).getByTestId('rcs-count-updated')).toHaveTextContent('2');
  });

  it('labels ECM settings distinctly from Dispatcharr settings', () => {
    // Bead dfkbn item 4: ECM's own settings.json is a DIFFERENT namespace and
    // must be readable as such — the drill's `settings updated=7` was
    // Dispatcharr's, while user_timezone / stats_poll_interval silently reverted.
    const report = appliedReport({
      categories: [category({ entity_type: 'ecm_settings', updated: 2 })],
    });
    render(<RestoreCompleteSummary report={report} />);
    const row = screen.getByTestId('rcs-category-ecm_settings');
    expect(within(row).getByText('ECM settings')).toBeInTheDocument();
    expect(within(row).getByTestId('rcs-count-updated')).toHaveTextContent('2');
  });
});

describe('RestoreCompleteSummary — expandable reasons', () => {
  it('expands skipped to its reasons + human labels', () => {
    render(<RestoreCompleteSummary report={appliedReport()} />);
    const row = screen.getByTestId('rcs-category-channel');
    fireEvent.click(within(row).getByTestId('rcs-skip-toggle'));
    const details = within(row).getByTestId('rcs-skip-details');
    expect(within(details).getByText('CNN HD')).toBeInTheDocument();
    expect(within(details).getByText('Local Access 5')).toBeInTheDocument();
    // Human-readable reason label, not the raw enum value.
    expect(within(details).getByText('Already exists (identical)')).toBeInTheDocument();
    expect(within(details).getByText('Excluded by operator')).toBeInTheDocument();
    expect(within(details).queryByText('already_exists_identical')).not.toBeInTheDocument();
  });

  it('expands failed to its reasons + sanitized messages', () => {
    const report = appliedReport({
      outcome: 'partial_failed_rolled_back',
      categories: [
        category({
          entity_type: 'channel',
          failed: 1,
          failure_details: [
            {
              reason: 'upstream_api_error',
              label: 'BBC One',
              message: 'Dispatcharr returned 500',
            },
          ],
        }),
      ],
    });
    render(<RestoreCompleteSummary report={report} />);
    const row = screen.getByTestId('rcs-category-channel');
    fireEvent.click(within(row).getByTestId('rcs-failure-toggle'));
    const details = within(row).getByTestId('rcs-failure-details');
    expect(within(details).getByText('BBC One')).toBeInTheDocument();
    expect(within(details).getByText('Upstream API error')).toBeInTheDocument();
    expect(within(details).getByText('Dispatcharr returned 500')).toBeInTheDocument();
  });
});

describe('RestoreCompleteSummary — tri-state outcome banner', () => {
  it('renders a positive banner for success', () => {
    render(<RestoreCompleteSummary report={appliedReport({ outcome: 'success' })} />);
    const banner = screen.getByTestId('rcs-outcome-banner');
    expect(banner).toHaveAttribute('data-outcome', 'success');
    expect(banner).toHaveTextContent('Restore complete');
  });

  it('labels partial_failed_rolled_back as a FAILURE that was rolled back — never success', () => {
    render(
      <RestoreCompleteSummary
        report={appliedReport({ outcome: 'partial_failed_rolled_back' })}
      />
    );
    const banner = screen.getByTestId('rcs-outcome-banner');
    expect(banner).toHaveAttribute('data-outcome', 'partial_failed_rolled_back');
    expect(banner).toHaveTextContent(/restore failed/i);
    expect(banner).toHaveTextContent(/rolled back/i);
    // Hard contract: a rolled-back restore is NEVER labeled success/complete.
    expect(banner.textContent ?? '').not.toMatch(/\bsuccess\b/i);
    expect(banner.textContent ?? '').not.toMatch(/restore complete/i);
  });

  it('labels completed_with_failures as applied-but-degraded — not a rollback, not a success', () => {
    // Bead …-y65si: a non-fatal (dispatcharr_users) failure leaves the restore
    // APPLIED. Telling the operator it "failed and was rolled back" would send
    // them looking for a rollback that never ran; telling them it succeeded
    // would hide the failed rows.
    render(
      <RestoreCompleteSummary
        report={appliedReport({
          outcome: 'completed_with_failures',
          categories: [
            category({
              entity_type: 'user',
              failed: 1,
              failure_details: [
                {
                  reason: 'upstream_api_error',
                  label: 'drilladmin',
                  message: 'User creation failed: 500 - Server Error (500)',
                },
              ],
            }),
          ],
        })}
      />
    );
    const banner = screen.getByTestId('rcs-outcome-banner');
    expect(banner).toHaveAttribute('data-outcome', 'completed_with_failures');
    expect(banner).toHaveTextContent(/could not be restored/i);
    expect(banner).toHaveTextContent(/nothing was rolled back/i);
    expect(banner.textContent ?? '').not.toMatch(/restore failed/i);
    // The failed row is still visible and counted.
    const row = screen.getByTestId('rcs-category-user');
    expect(within(row).getByTestId('rcs-count-failed')).toHaveTextContent('1');
    // No residue/manual-cleanup note — nothing was left behind by a rollback.
    expect(screen.queryByTestId('rcs-residue-note')).not.toBeInTheDocument();
  });

  it('gives failed_rollback_incomplete the loudest treatment + residue note', () => {
    render(
      <RestoreCompleteSummary
        report={appliedReport({
          outcome: 'failed_rollback_incomplete',
          notes: ['2 entities could not be removed: channel id=44, channel id=51'],
        })}
      />
    );
    const banner = screen.getByTestId('rcs-outcome-banner');
    expect(banner).toHaveAttribute('data-outcome', 'failed_rollback_incomplete');
    expect(banner).toHaveTextContent(/could not be fully rolled back|not be fully rolled back|NOT be fully rolled back/i);
    expect(banner.textContent ?? '').not.toMatch(/\bsuccess\b/i);
    // The ledger residue note is surfaced for manual cleanup.
    const residue = screen.getByTestId('rcs-residue-note');
    expect(residue).toHaveTextContent('2 entities could not be removed: channel id=44, channel id=51');
  });

  it('renders no outcome banner on a dry-run (a plan has no realized outcome)', () => {
    render(<RestoreCompleteSummary report={dryRunReport()} mode="dry-run" />);
    expect(screen.queryByTestId('rcs-outcome-banner')).not.toBeInTheDocument();
  });
});

describe('RestoreCompleteSummary — dry-run vs applied framing (shared shape)', () => {
  it('renders applied framing (created/updated/skipped) in applied mode', () => {
    render(<RestoreCompleteSummary report={appliedReport()} mode="applied" />);
    const summary = screen.getByTestId('restore-complete-summary');
    expect(summary).toHaveAttribute('data-mode', 'applied');
    const row = screen.getByTestId('rcs-category-channel');
    expect(within(row).getByTestId('rcs-count-created')).toHaveTextContent('12');
    expect(within(row).getByTestId('rcs-label-created')).toHaveTextContent(/^Created$/);
  });

  it('renders preview framing (will create/update/skip) in dry-run mode from the same shape', () => {
    render(<RestoreCompleteSummary report={dryRunReport()} mode="dry-run" />);
    const summary = screen.getByTestId('restore-complete-summary');
    expect(summary).toHaveAttribute('data-mode', 'dry-run');
    const row = screen.getByTestId('rcs-category-channel');
    // Dry-run reads the would_* counts.
    expect(within(row).getByTestId('rcs-count-created')).toHaveTextContent('8');
    expect(within(row).getByTestId('rcs-count-updated')).toHaveTextContent('1');
    expect(within(row).getByTestId('rcs-count-skipped')).toHaveTextContent('4');
    // Dry-run framing reads "Will create", not "Created".
    expect(within(row).getByTestId('rcs-label-created')).toHaveTextContent(/^Will create$/);
  });

  it('infers mode from is_dry_run when no mode prop is given', () => {
    render(<RestoreCompleteSummary report={dryRunReport()} />);
    expect(screen.getByTestId('restore-complete-summary')).toHaveAttribute('data-mode', 'dry-run');
  });
});

describe('RestoreCompleteSummary — logo-miss banner seam (bead .19)', () => {
  it('renders the bannerSlot at the top of the summary (insertion point for .19)', () => {
    render(
      <RestoreCompleteSummary
        report={appliedReport()}
        bannerSlot={<div data-testid="injected-banner">RED BANNER</div>}
      />
    );
    expect(screen.getByTestId('injected-banner')).toBeInTheDocument();
  });
});

/**
 * Credential re-entry action item (bead 6pilh).
 *
 * A restore from a STANDARD (redact-by-default) artifact creates the M3U/EPG
 * accounts but leaves their credentials UNSET — the archive carried only the
 * `***REDACTED***` placeholder and the importers refuse to write it through.
 * The counts are perfect and the outcome is `success`, so this is the ONLY
 * place the operator learns the instance will not fetch a single stream.
 */
describe('RestoreCompleteSummary — credential re-entry', () => {
  it('does NOT render when no credential needs re-entering', () => {
    render(<RestoreCompleteSummary report={appliedReport({ credentials_needing_reentry: 0 })} />);

    expect(screen.queryByTestId('credential-reentry-notice')).toBeNull();
  });

  it('does NOT render when the field is absent (report from an older build)', () => {
    render(<RestoreCompleteSummary report={appliedReport()} />);

    expect(screen.queryByTestId('credential-reentry-notice')).toBeNull();
  });

  it('names the count and every affected entity when credentials were redacted', () => {
    render(
      <RestoreCompleteSummary
        report={appliedReport({
          credentials_needing_reentry: 2,
          credential_reentry_details: [
            { entity_type: 'm3u_account', label: 'Infinity', fields: ['password'], destination_id: 3 },
            { entity_type: 'epg_source', label: 'SD Sports', fields: ['password'], destination_id: 7 },
          ],
        })}
      />,
    );

    const notice = screen.getByTestId('credential-reentry-notice');
    expect(notice.textContent).toContain('2 accounts');
    const rows = within(notice).getAllByTestId('credential-reentry-row');
    expect(rows).toHaveLength(2);
    expect(rows[0].textContent).toContain('Infinity');
    expect(rows[0].textContent).toContain('password');
    expect(rows[1].textContent).toContain('SD Sports');
  });

  it('uses singular copy for a single account', () => {
    render(
      <RestoreCompleteSummary
        report={appliedReport({
          credentials_needing_reentry: 1,
          credential_reentry_details: [
            { entity_type: 'm3u_account', label: 'Infinity', fields: ['password'], destination_id: 3 },
          ],
        })}
      />,
    );

    expect(screen.getByTestId('credential-reentry-notice').textContent).toContain('1 account');
  });

  it('warns on the dry-run preview too — the operator can otherwise not tell the artifact variants apart', () => {
    render(
      <RestoreCompleteSummary
        report={dryRunReport({
          credentials_needing_reentry: 1,
          credential_reentry_details: [
            { entity_type: 'm3u_account', label: 'Infinity', fields: ['password'], destination_id: null },
          ],
        })}
      />,
    );

    expect(screen.getByTestId('credential-reentry-notice').textContent).toContain('will need');
  });

  it('announces itself to assistive tech', () => {
    render(
      <RestoreCompleteSummary
        report={appliedReport({
          credentials_needing_reentry: 1,
          credential_reentry_details: [
            { entity_type: 'm3u_account', label: 'Infinity', fields: ['password'], destination_id: 3 },
          ],
        })}
      />,
    );

    expect(screen.getByTestId('credential-reentry-notice').getAttribute('role')).toBe('alert');
  });
});

/**
 * Stream-reattach action item (bead d0bd3).
 *
 * The sibling of the credentials panel, and the one the drill found missing: a
 * redacted restore reported `channels_with_no_playable_stream: 12` with all
 * twelve channels named, and the restore-complete dialog rendered only the
 * credentials panel plus the count grid. An operator following the product's own
 * advice — read the panels rather than re-derive the information — walked away
 * believing a credential was the only outstanding item, on an instance that
 * played nothing.
 *
 * These pin the WIRING (the panel reaches the modal from the report alone); the
 * panel's own copy and population split are pinned in
 * StreamReattachNotice.test.tsx.
 */
describe('RestoreCompleteSummary — stream reattach', () => {
  it('does NOT render when every restored channel plays', () => {
    render(
      <RestoreCompleteSummary
        report={appliedReport({
          channels_needing_stream_reattach: 0,
          channels_with_no_playable_stream: 0,
        })}
      />,
    );

    expect(screen.queryByTestId('stream-reattach-notice')).toBeNull();
  });

  it('does NOT render when the fields are absent (report from an older build)', () => {
    render(<RestoreCompleteSummary report={appliedReport()} />);

    expect(screen.queryByTestId('stream-reattach-notice')).toBeNull();
  });

  it('surfaces "no channel can play" alongside the credentials panel', () => {
    render(
      <RestoreCompleteSummary
        report={appliedReport({
          outcome: 'completed_with_failures',
          credentials_needing_reentry: 2,
          credential_reentry_details: [
            { entity_type: 'm3u_account', label: 'Infinity', fields: ['password'], destination_id: 2 },
            { entity_type: 'ecm_settings', label: 'ECM settings', fields: ['mcp_api_key'] },
          ],
          channels_needing_stream_reattach: 12,
          channels_with_no_playable_stream: 12,
          stream_reattach_details: Array.from({ length: 12 }, (_, index) => ({
            channel_id: 101 + index,
            name: `Channel ${index + 1}`,
            placeholder_streams: ['placeholder'],
            has_playable_stream: false,
          })),
        })}
      />,
    );

    // Both panels, not one.
    expect(screen.getByTestId('credential-reentry-notice')).toBeInTheDocument();
    const notice = screen.getByTestId('stream-reattach-notice');
    expect(notice.textContent).toContain('12 channels have no playable stream');
    expect(within(notice).getAllByTestId('stream-reattach-unplayable-row')).toHaveLength(12);
  });
  // -----------------------------------------------------------------------
  // Honest category reporting (beads 3t74w / tddmw)
  // -----------------------------------------------------------------------

  it('labels a name-only channel-group match as matched by name, not identical', () => {
    // The restore adopts a destination group on its NAME and compares nothing
    // else; "Already exists (identical)" was a claim it never checked.
    render(
      <RestoreCompleteSummary
        report={appliedReport({
          categories: [
            category({
              entity_type: 'channel_group',
              skipped: 1,
              skip_details: [
                { reason: 'already_exists_name_match', label: 'Drill Movies' },
              ],
            }),
          ],
        })}
      />,
    );
    fireEvent.click(screen.getByTestId('rcs-skip-toggle'));
    const details = screen.getByTestId('rcs-skip-details');
    expect(within(details).getByText('Already exists (matched by name)')).toBeInTheDocument();
    expect(within(details).queryByText('Already exists (identical)')).not.toBeInTheDocument();
  });

  it('renders a NOT-PREDICTED category as such instead of as four zeroes', () => {
    // Drill run 12: the apply reported "Streams 9 CREATED" and the preview had
    // no Streams row at all. Emitting the row with zeroes would just swap an
    // absent claim for a confident wrong one.
    render(
      <RestoreCompleteSummary
        report={dryRunReport({
          categories: [
            category({
              entity_type: 'stream',
              predicted: false,
              caveat: 'Streams cannot be previewed.',
            }),
          ],
        })}
      />,
    );
    const row = screen.getByTestId('rcs-category-stream');
    expect(within(row).getByTestId('rcs-not-predicted')).toBeInTheDocument();
    expect(within(row).queryByTestId('rcs-count-created')).not.toBeInTheDocument();
    expect(row.textContent).toContain('Streams cannot be previewed.');
  });

  it('shows a category caveat beside counts it still renders', () => {
    render(
      <RestoreCompleteSummary
        report={dryRunReport({
          categories: [
            category({
              entity_type: 'channel_group',
              would_create: 378,
              caveat: 'Restoring an M3U account makes its provider groups appear first.',
            }),
          ],
        })}
      />,
    );
    const row = screen.getByTestId('rcs-category-channel_group');
    expect(within(row).getByTestId('rcs-count-created').textContent).toBe('378');
    expect(within(row).getByTestId('rcs-category-caveat').textContent).toContain(
      'provider groups appear first',
    );
  });

  it('shows no caveat and full counts for a category that carries neither', () => {
    render(<RestoreCompleteSummary report={appliedReport()} />);
    const row = screen.getByTestId('rcs-category-channel');
    expect(within(row).queryByTestId('rcs-category-caveat')).not.toBeInTheDocument();
    expect(within(row).queryByTestId('rcs-not-predicted')).not.toBeInTheDocument();
    expect(within(row).getByTestId('rcs-count-created').textContent).toBe('12');
  });
});
