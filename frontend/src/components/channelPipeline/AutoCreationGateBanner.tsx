/**
 * AutoCreationGateBanner — "your run-on-refresh rules will never fire" nudge
 * (bead vkktd.4, reusing the BackupScheduleBanner pattern from bead ikv8z).
 *
 * Context: the Channel Pipeline task (task_id=auto_creation) is OPT-IN and
 * ships disabled. An operator can configure run-on-refresh rules — a clear
 * statement of intent — while the task gate is off, in which case the rules
 * silently never fire (epic vkktd: this cost a multi-hour field
 * investigation). This banner makes that state visible on the Channel
 * Pipeline tab itself.
 *
 * Behaviour:
 *   - Shows ONLY when >=1 ENABLED run_on_refresh rule exists AND the
 *     auto_creation task is not effective-enabled (parent gate AND >=1
 *     enabled child schedule — GET /api/tasks effective_enabled).
 *   - Dismissal persists in localStorage keyed to a fingerprint of the
 *     current run-on-refresh rule-set, so it RE-ARMS when the rule-set
 *     changes (new intent deserves a fresh nudge).
 *   - The CTA reuses the task-editor navigation contract (sessionStorage
 *     `ecm:open-task-editor` + window event App.tsx listens for).
 */
import { useEffect, useMemo, useState } from 'react';
import * as api from '../../services/api';
import type { ChannelPipelineRule } from '../../types/channelPipeline';
import { logger } from '../../utils/logger';
import './AutoCreationGateBanner.css';

/** localStorage key — stores the rule-set fingerprint the dismissal applies to. */
const AUTO_CREATION_GATE_DISMISS_KEY = 'ecm:auto-creation-gate-banner-dismissed';
/** The scheduled task that fires Channel Pipeline rules on M3U refresh. */
const AUTO_CREATION_TASK_ID = 'auto_creation';

interface AutoCreationGateBannerProps {
  rules: ChannelPipelineRule[];
}

export function AutoCreationGateBanner({ rules }: AutoCreationGateBannerProps) {
  // `null` = unknown (still checking / check failed) — banner stays hidden.
  const [effectiveEnabled, setEffectiveEnabled] = useState<boolean | null>(null);
  const [dismissedFor, setDismissedFor] = useState<string | null>(
    () => localStorage.getItem(AUTO_CREATION_GATE_DISMISS_KEY),
  );

  const runOnRefreshRules = useMemo(
    () => rules.filter((r) => r.enabled && r.run_on_refresh),
    [rules],
  );

  // Fingerprint of the current run-on-refresh rule-set. Dismissal is stored
  // against this, so adding/removing such a rule re-arms the banner.
  const fingerprint = useMemo(
    () => runOnRefreshRules.map((r) => r.id).sort((a, b) => a - b).join(','),
    [runOnRefreshRules],
  );

  useEffect(() => {
    // Nothing to warn about without run-on-refresh intent.
    if (runOnRefreshRules.length === 0) return;

    let cancelled = false;
    api
      .getTask(AUTO_CREATION_TASK_ID)
      .then((task) => {
        if (cancelled) return;
        setEffectiveEnabled(task.effective_enabled ?? task.enabled);
      })
      .catch((err) => {
        // Fail quiet: a transient task-load error must not nag the operator
        // with a misleading "won't run" banner.
        logger.warn('Failed to check auto_creation task state for gate banner', err);
        if (!cancelled) setEffectiveEnabled(null);
      });

    return () => {
      cancelled = true;
    };
  }, [runOnRefreshRules.length, fingerprint]);

  if (runOnRefreshRules.length === 0) return null;
  if (effectiveEnabled !== false) return null;
  if (dismissedFor === fingerprint) return null;

  const handleDismiss = () => {
    localStorage.setItem(AUTO_CREATION_GATE_DISMISS_KEY, fingerprint);
    setDismissedFor(fingerprint);
  };

  const handleEnableTask = () => {
    // Same contract NotificationCenter/BackupScheduleBanner use: stash intent
    // + fire the event App.tsx listens for to switch to Settings > Scheduled
    // Tasks and open the editor for this task.
    sessionStorage.setItem(
      'ecm:open-task-editor',
      JSON.stringify({ taskId: AUTO_CREATION_TASK_ID }),
    );
    window.dispatchEvent(
      new CustomEvent('ecm:open-task-editor', { detail: { taskId: AUTO_CREATION_TASK_ID } }),
    );
  };

  const ruleCount = runOnRefreshRules.length;
  return (
    <div
      className="auto-creation-gate-banner"
      data-testid="auto-creation-gate-banner"
      role="alert"
    >
      <span className="material-icons auto-creation-gate-banner-icon" aria-hidden="true">
        warning
      </span>
      <div className="auto-creation-gate-banner-body">
        <span className="auto-creation-gate-banner-title">
          Run-on-refresh rules will never fire
        </span>
        <span className="auto-creation-gate-banner-detail">
          {ruleCount === 1
            ? '1 rule is set to run on M3U refresh'
            : `${ruleCount} rules are set to run on M3U refresh`}
          , but the Channel Pipeline scheduled task is not enabled, so they will
          never run automatically. Enable the task (and a schedule for it) to
          make refresh-triggered runs work.
        </span>
        <button
          type="button"
          className="auto-creation-gate-banner-cta"
          data-testid="auto-creation-gate-banner-cta"
          onClick={handleEnableTask}
        >
          Enable the task
          <span className="material-icons auto-creation-gate-banner-cta-icon" aria-hidden="true">
            arrow_forward
          </span>
        </button>
      </div>
      <button
        type="button"
        className="auto-creation-gate-banner-dismiss"
        data-testid="auto-creation-gate-banner-dismiss"
        aria-label="Dismiss"
        title="Dismiss"
        onClick={handleDismiss}
      >
        <span className="material-icons" aria-hidden="true">
          close
        </span>
      </button>
    </div>
  );
}
