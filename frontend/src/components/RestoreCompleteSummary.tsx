/**
 * Aggregate restore-result surface for a DBAS Phase-2 restore (bead 0i2vt.20).
 *
 * Renders, from a single {@link RestoreReport}:
 *
 *   1. A tri-state OUTCOME banner ({@link RestoreOutcome}). The contract is hard:
 *      a rolled-back restore is NEVER labeled "success"/"complete". `success`
 *      gets a positive banner; `partial_failed_rolled_back` reads
 *      "Restore failed — your configuration was rolled back"; and
 *      `failed_rollback_incomplete` gets the loudest treatment plus the ledger
 *      residue note so an operator can finish cleanup manually.
 *   2. A PER-ENTITY breakdown — one row per {@link EntityCategoryReport} with
 *      created / updated / skipped / failed counts; skipped and failed rows
 *      expand to their reasons (human-readable labels, not raw enum values).
 *
 * The SAME component renders both the dry-run counts-only preview (bead .16)
 * and the realized restore result. The `mode` prop switches the framing
 * ("Will create" vs "Created") and which counts are read (would_* vs the
 * realized counts); the per-entity machinery is identical so an operator
 * recognizes apply-vs-preview at a glance. When `mode` is omitted it is
 * inferred from `report.is_dry_run`.
 *
 * SEAM for bead .19 (logo-miss RED banner): the `bannerSlot` prop renders at the
 * very top of the summary, above the outcome banner. Bead .19 slots its
 * prominent red "N channels are missing logos" banner there (driven by
 * `report.logo_misses`) without restructuring this component.
 *
 * Styling reuses shared `.badge-*` classes from `common.css`; the
 * summary/category layout lives in `RestoreCompleteSummary.css`.
 */
import { useState } from 'react';
import type {
  EntityCategoryReport,
  RestoreEntityType,
  RestoreFailureReason,
  RestoreReport,
  RestoreSkipReason,
} from '../services/api';
import './RestoreCompleteSummary.css';

/** Which framing the summary renders. `dry-run` reads would_* counts. */
export type RestoreSummaryMode = 'dry-run' | 'applied';

interface RestoreCompleteSummaryProps {
  /** The aggregate restore result / dry-run plan to render. */
  report: RestoreReport;
  /**
   * Framing override. When omitted, inferred from `report.is_dry_run`
   * (`true` → 'dry-run', `false` → 'applied').
   */
  mode?: RestoreSummaryMode;
  /**
   * SEAM for bead .19 — content rendered at the very top of the summary, above
   * the outcome banner. Bead .19 injects its logo-miss RED banner here.
   */
  bannerSlot?: React.ReactNode;
}

const ENTITY_LABELS: Record<RestoreEntityType, string> = {
  m3u_account: 'M3U accounts',
  epg_source: 'EPG sources',
  channel_group: 'Channel groups',
  channel_profile: 'Channel profiles',
  stream_profile: 'Stream profiles',
  channel: 'Channels',
  stream: 'Streams',
  user_agent: 'User agents',
  dvr_rule: 'DVR rules',
  settings: 'Settings',
  user: 'Users',
  logo: 'Logos',
};

const SKIP_REASON_LABELS: Record<RestoreSkipReason, string> = {
  already_exists_identical: 'Already exists (identical)',
  excluded_by_operator: 'Excluded by operator',
  current_admin_preserved: 'Current admin preserved',
  unsupported_in_this_version: 'Unsupported in this version',
  dependency_unresolved: 'Dependency unresolved',
};

const FAILURE_REASON_LABELS: Record<RestoreFailureReason, string> = {
  validation_error: 'Validation error',
  dependency_unresolved: 'Dependency unresolved',
  upstream_api_error: 'Upstream API error',
  upstream_timeout: 'Upstream timeout',
  conflict: 'Conflict',
  password_hash_unsupported: 'Password hash unsupported',
  internal_error: 'Internal error',
};

/** Count labels per mode — applied reads past tense, dry-run reads "will …". */
const COUNT_LABELS: Record<RestoreSummaryMode, { created: string; updated: string; skipped: string; failed: string }> = {
  applied: { created: 'Created', updated: 'Updated', skipped: 'Skipped', failed: 'Failed' },
  'dry-run': { created: 'Will create', updated: 'Will update', skipped: 'Will skip', failed: 'Failed' },
};

interface OutcomePresentation {
  tone: 'success' | 'warning' | 'error';
  icon: string;
  title: string;
  detail?: string;
}

/**
 * Map the outcome to its banner treatment. The rolled-back states are presented
 * as FAILURES (error tone, explicit "failed" copy) — never as success, and
 * `completed_with_failures` is presented as a WARNING: the applied state is real
 * and kept, so calling it a failure would send the operator hunting for a
 * rollback that never happened.
 */
const OUTCOME_PRESENTATION: Record<NonNullable<RestoreReport['outcome']>, OutcomePresentation> = {
  success: {
    tone: 'success',
    icon: 'check_circle',
    title: 'Restore complete',
    detail: 'Your configuration was restored.',
  },
  completed_with_failures: {
    tone: 'warning',
    icon: 'warning',
    title: 'Restore complete — some items could not be restored',
    detail:
      'Everything else was restored and nothing was rolled back. Expand the categories below to see which items failed and why.',
  },
  partial_failed_rolled_back: {
    tone: 'error',
    icon: 'cancel',
    title: 'Restore failed — your configuration was rolled back',
    detail: 'One or more items failed, so the restore was undone. Your instance is back to its pre-restore state.',
  },
  failed_rollback_incomplete: {
    tone: 'error',
    icon: 'error',
    title: 'Restore failed — state could NOT be fully rolled back',
    detail: 'A failure occurred and the automatic rollback could not remove everything it created. Your instance is in an indeterminate state. Review the residue below and finish cleanup manually.',
  },
};

function CountCell({ kind, value, label }: { kind: string; value: number; label: string }) {
  return (
    <div className="rcs-count" data-testid={`rcs-count-cell-${kind}`}>
      <span className="rcs-count-value" data-testid={`rcs-count-${kind}`}>
        {value}
      </span>
      <span className="rcs-count-label" data-testid={`rcs-label-${kind}`}>
        {label}
      </span>
    </div>
  );
}

function CategoryRow({ category, mode }: { category: EntityCategoryReport; mode: RestoreSummaryMode }) {
  const [skipOpen, setSkipOpen] = useState(false);
  const [failureOpen, setFailureOpen] = useState(false);

  const isDryRun = mode === 'dry-run';
  const created = isDryRun ? category.would_create : category.created;
  const updated = isDryRun ? category.would_update : category.updated;
  const skipped = isDryRun ? category.would_skip : category.skipped;
  const failed = category.failed;

  const labels = COUNT_LABELS[mode];
  const hasSkipDetails = category.skip_details.length > 0;
  const hasFailureDetails = category.failure_details.length > 0;

  return (
    <div className="rcs-category" data-testid={`rcs-category-${category.entity_type}`}>
      <div className="rcs-category-header">
        <span className="rcs-category-name">{ENTITY_LABELS[category.entity_type]}</span>
        <div className="rcs-counts">
          <CountCell kind="created" value={created} label={labels.created} />
          <CountCell kind="updated" value={updated} label={labels.updated} />
          <CountCell kind="skipped" value={skipped} label={labels.skipped} />
          <CountCell kind="failed" value={failed} label={labels.failed} />
        </div>
      </div>

      {hasSkipDetails && (
        <div className="rcs-detail-block">
          <button
            type="button"
            className="rcs-detail-toggle"
            data-testid="rcs-skip-toggle"
            aria-expanded={skipOpen}
            onClick={() => setSkipOpen((v) => !v)}
          >
            <span className="material-icons">{skipOpen ? 'expand_less' : 'expand_more'}</span>
            {labels.skipped} reasons ({category.skip_details.length})
          </button>
          {skipOpen && (
            <ul className="rcs-detail-list" data-testid="rcs-skip-details">
              {category.skip_details.map((d, i) => (
                <li key={`${d.label}-${i}`} className="rcs-detail-item">
                  <span className="badge badge-warning badge-sm">{SKIP_REASON_LABELS[d.reason]}</span>
                  <span className="rcs-detail-label">{d.label}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {hasFailureDetails && (
        <div className="rcs-detail-block">
          <button
            type="button"
            className="rcs-detail-toggle is-failure"
            data-testid="rcs-failure-toggle"
            aria-expanded={failureOpen}
            onClick={() => setFailureOpen((v) => !v)}
          >
            <span className="material-icons">{failureOpen ? 'expand_less' : 'expand_more'}</span>
            Failed reasons ({category.failure_details.length})
          </button>
          {failureOpen && (
            <ul className="rcs-detail-list" data-testid="rcs-failure-details">
              {category.failure_details.map((d, i) => (
                <li key={`${d.label}-${i}`} className="rcs-detail-item rcs-detail-item-failure">
                  <span className="badge badge-error badge-sm">{FAILURE_REASON_LABELS[d.reason]}</span>
                  <span className="rcs-detail-label">{d.label}</span>
                  {d.message && <span className="rcs-detail-message">{d.message}</span>}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}

export function RestoreCompleteSummary({ report, mode, bannerSlot }: RestoreCompleteSummaryProps) {
  const effectiveMode: RestoreSummaryMode = mode ?? (report.is_dry_run ? 'dry-run' : 'applied');

  // Outcome banner only renders for a realized restore — a dry-run plan has no
  // realized outcome (report.outcome is null), so no banner.
  const presentation = report.outcome ? OUTCOME_PRESENTATION[report.outcome] : null;
  const isResidueIncomplete = report.outcome === 'failed_rollback_incomplete';

  return (
    <div
      className="restore-complete-summary"
      data-testid="restore-complete-summary"
      data-mode={effectiveMode}
    >
      {/* SEAM for bead .19 — logo-miss RED banner slots here, above everything. */}
      {bannerSlot}

      {presentation && (
        <div
          className={`rcs-outcome-banner rcs-tone-${presentation.tone}`}
          data-testid="rcs-outcome-banner"
          data-outcome={report.outcome ?? undefined}
          role={presentation.tone === 'error' ? 'alert' : 'status'}
        >
          <span className="material-icons rcs-outcome-icon">{presentation.icon}</span>
          <div className="rcs-outcome-text">
            <span className="rcs-outcome-title">{presentation.title}</span>
            {presentation.detail && <span className="rcs-outcome-detail">{presentation.detail}</span>}
          </div>
        </div>
      )}

      {isResidueIncomplete && report.notes.length > 0 && (
        <div className="rcs-residue-note" data-testid="rcs-residue-note" role="alert">
          <span className="rcs-residue-title">Manual cleanup required</span>
          <ul className="rcs-residue-list">
            {report.notes.map((note, i) => (
              <li key={i}>{note}</li>
            ))}
          </ul>
        </div>
      )}

      <div className="rcs-categories">
        {report.categories.length === 0 ? (
          <div className="rcs-empty" data-testid="rcs-empty">
            No entity categories were included in this {effectiveMode === 'dry-run' ? 'preview' : 'restore'}.
          </div>
        ) : (
          report.categories.map((cat) => (
            <CategoryRow key={cat.entity_type} category={cat} mode={effectiveMode} />
          ))
        )}
      </div>
    </div>
  );
}
