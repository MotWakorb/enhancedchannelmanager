/**
 * SecuritySettingsSection — the reversible home for the backup outbound-policy
 * choice (bead nngkg).
 *
 * The first-run wizard asks the operator once where ECM is allowed to send
 * backups; this section is where they can review and change that choice later.
 * It is a THIN CLIENT over the backend chokepoint (security/ssrf.py): the UI
 * only moves the LAN-vs-public band. The always-on safety blocks (metadata
 * endpoints, link-local, etc.) are enforced unconditionally in the backend and
 * are intentionally NOT exposed as a toggle here.
 *
 * UX: PLAIN LANGUAGE. The wire values are `lan_friendly` / `public_only`, but
 * the operator never sees those terms — they see "your home network" vs "the
 * public internet only". No "SSRF" / "RFC1918" jargon anywhere.
 */
import { useState, useEffect } from 'react';
import * as api from '../../services/api';
import type { OutboundPolicyMode } from '../../services/api';
import { useNotifications } from '../../contexts/NotificationContext';
import { logger } from '../../utils/logger';
import './SecuritySettingsSection.css';

interface Props {
  isAdmin: boolean;
}

const OPTIONS: {
  value: OutboundPolicyMode;
  title: string;
  detail: string;
}[] = [
  {
    value: 'lan_friendly',
    title: 'Allow your home network (recommended)',
    detail:
      'ECM can send backups to a device on your own network, like a NAS, as well as to ' +
      'cloud storage. Choose this if you back up to a local server.',
  },
  {
    value: 'public_only',
    title: 'Public internet only',
    detail:
      'ECM can only send backups to public cloud storage, never to addresses on your local ' +
      'network. Choose this if you only back up to a cloud provider and want to keep local ' +
      'addresses off-limits.',
  },
];

export function SecuritySettingsSection({ isAdmin }: Props) {
  const notifications = useNotifications();
  const [mode, setMode] = useState<OutboundPolicyMode | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!isAdmin) return;
    api
      .getSettings()
      .then((settings) => setMode(settings.ssrf_outbound_mode))
      .catch((err) => {
        logger.warn('Failed to load security settings', err);
        // Default to the safe, available choice so the UI is still usable.
        setMode('lan_friendly');
      });
  }, [isAdmin]);

  if (!isAdmin) {
    return (
      <div className="security-settings-no-access">
        <span className="material-icons">lock</span>
        Only administrators can change security settings.
      </div>
    );
  }

  const handleSelect = async (next: OutboundPolicyMode) => {
    if (saving || next === mode) return;
    const previous = mode;
    setMode(next); // optimistic
    setSaving(true);
    try {
      await api.saveSecurityMode(next);
      notifications.success('Backup destination setting saved', 'Security');
    } catch (err) {
      setMode(previous); // roll back on failure
      notifications.error(
        err instanceof Error ? err.message : 'Failed to save setting',
        'Security',
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="security-settings-section">
      <h2 className="settings-page-header">Security</h2>

      <div className="backup-card">
        <div className="backup-card-header">
          <span className="material-icons">shield</span>
          <h3>Where backups can be sent</h3>
        </div>
        <p className="backup-card-description">
          Control which destinations ECM is allowed to upload backups to. This is a safety
          setting — ECM always blocks risky internal addresses no matter what you choose here.
        </p>

        <div className="security-mode-options" role="radiogroup" aria-label="Backup destinations">
          {OPTIONS.map((opt) => {
            const checked = mode === opt.value;
            return (
              <label
                key={opt.value}
                className={`security-mode-option${checked ? ' is-selected' : ''}`}
              >
                <input
                  type="radio"
                  name="outbound-mode"
                  value={opt.value}
                  data-testid={`outbound-mode-${opt.value}`}
                  checked={checked}
                  disabled={saving || mode === null}
                  onChange={() => handleSelect(opt.value)}
                />
                <span className="security-mode-text">
                  <span className="security-mode-title">{opt.title}</span>
                  <span className="security-mode-detail">{opt.detail}</span>
                </span>
              </label>
            );
          })}
        </div>
      </div>
    </div>
  );
}
