/**
 * SecurityFirstRunModal — the one-time first-run choice for where ECM may send
 * backups (bead nngkg).
 *
 * Pattern: modal-once-then-Settings. It appears on the first run only (gated by
 * a localStorage flag) and asks the operator to pick where backups can go. After
 * that, the choice is changed in Settings > Security — never re-prompted.
 *
 * The DEFAULT is the home-network choice (lan_friendly), so a backup to a local
 * NAS works out of the box. Dismissing the modal (Escape, the backdrop, or
 * "Not now") ACCEPTS that default and toasts the operator about where to change
 * it — escaping never leaves the choice unrecorded.
 *
 * UX: PLAIN LANGUAGE — no "SSRF" / "RFC1918" jargon. This is a thin client over
 * the backend chokepoint; the always-on safety blocks are enforced server-side
 * regardless of this choice and are never surfaced as a toggle.
 */
import { useState } from 'react';
import * as api from '../services/api';
import type { OutboundPolicyMode } from '../services/api';
import { useNotifications } from '../contexts/NotificationContext';
import { ModalOverlay } from './ModalOverlay';
import { logger } from '../utils/logger';
import './SecurityFirstRunModal.css';

/** localStorage flag — set once the operator has made (or skipped) the choice. */
export const SECURITY_FIRST_RUN_KEY = 'ecm:security-first-run-seen';

const DEFAULT_MODE: OutboundPolicyMode = 'lan_friendly';

const OPTIONS: { value: OutboundPolicyMode; title: string; detail: string }[] = [
  {
    value: 'lan_friendly',
    title: 'My home network (recommended)',
    detail: 'Back up to a device on my own network, like a NAS, or to cloud storage.',
  },
  {
    value: 'public_only',
    title: 'Public internet only',
    detail: 'Back up only to cloud storage. Keep local network addresses off-limits.',
  },
];

export function SecurityFirstRunModal() {
  const notifications = useNotifications();
  const [open, setOpen] = useState(
    () => localStorage.getItem(SECURITY_FIRST_RUN_KEY) !== '1',
  );
  const [selected, setSelected] = useState<OutboundPolicyMode>(DEFAULT_MODE);
  const [saving, setSaving] = useState(false);

  if (!open) return null;

  const persist = async (mode: OutboundPolicyMode) => {
    localStorage.setItem(SECURITY_FIRST_RUN_KEY, '1');
    try {
      await api.saveSecurityMode(mode);
    } catch (err) {
      // Non-blocking: the backend default is already the safe LAN-friendly mode,
      // and the operator can set it explicitly later in Settings > Security.
      logger.warn('Failed to persist first-run security choice', err);
    }
    setOpen(false);
  };

  const handleConfirm = async () => {
    if (saving) return;
    setSaving(true);
    await persist(selected);
    setSaving(false);
  };

  // Escape / backdrop / "Not now" — accept the default and tell the operator
  // where to change it.
  const handleDismiss = async () => {
    if (saving) return;
    setSaving(true);
    await persist(DEFAULT_MODE);
    notifications.info(
      'Using your home network for backups. You can change this in Settings > Security.',
      'Security',
    );
    setSaving(false);
  };

  return (
    <ModalOverlay
      onClose={handleDismiss}
      onClick={handleDismiss}
      data-testid="security-first-run-modal"
      aria-labelledby="security-first-run-title"
    >
      <div
        className="modal-container modal-md security-first-run"
        onClick={(e) => {
          // Clicks inside the dialog must not trigger the backdrop dismiss.
          e.stopPropagation();
        }}
      >
        <div className="modal-header">
          <h2 id="security-first-run-title">Where can ECM send backups?</h2>
        </div>

        <div className="modal-body">
          <p className="modal-description">
            ECM can upload backups to keep them safe. Choose where it&apos;s allowed to send
            them. ECM always blocks risky internal addresses no matter which you pick — you can
            change this later in Settings.
          </p>

          <div className="security-first-run-options" role="radiogroup" aria-label="Backup destinations">
            {OPTIONS.map((opt) => {
              const checked = selected === opt.value;
              return (
                <label
                  key={opt.value}
                  className={`security-first-run-option${checked ? ' is-selected' : ''}`}
                >
                  <input
                    type="radio"
                    name="first-run-mode"
                    value={opt.value}
                    data-testid={`first-run-mode-${opt.value}`}
                    checked={checked}
                    disabled={saving}
                    onChange={() => setSelected(opt.value)}
                  />
                  <span className="security-first-run-text">
                    <span className="security-first-run-option-title">{opt.title}</span>
                    <span className="security-first-run-option-detail">{opt.detail}</span>
                  </span>
                </label>
              );
            })}
          </div>
        </div>

        <div className="modal-footer">
          <button
            type="button"
            className="btn-secondary"
            disabled={saving}
            onClick={handleDismiss}
          >
            Not now
          </button>
          <button
            type="button"
            className="btn-primary"
            data-testid="security-first-run-confirm"
            disabled={saving}
            onClick={handleConfirm}
          >
            Save choice
          </button>
        </div>
      </div>
    </ModalOverlay>
  );
}
