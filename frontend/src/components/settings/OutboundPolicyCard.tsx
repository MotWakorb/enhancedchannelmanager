/**
 * OutboundPolicyCard — the reversible home for the backup outbound-policy
 * choice (originally bead nngkg; relocated + explicit-save by bead 09x38.12).
 *
 * Relocated from the standalone Settings → Administration → "Security" page
 * into Settings → Backup & Restore: that page held exactly this one setting
 * despite its broad name, and the setting itself configures a backup
 * DESTINATION, not a general security concern. This card also adopts the
 * standard staged-save pattern every other Settings page/section uses (see
 * TLSSettingsSection, AuthSettingsSection): the radio choice is local state
 * until "Save Settings" is clicked. The old page persisted instantly on
 * radio click with no Save button — silent instant-persist on a
 * security-relevant control was the wrong inconsistency to have.
 *
 * It is a THIN CLIENT over the backend chokepoint (security/ssrf.py): the UI
 * only moves the LAN-vs-public band. The always-on safety blocks (metadata
 * endpoints, link-local, etc.) are enforced unconditionally in the backend
 * and are intentionally NOT exposed as a toggle here.
 *
 * UX: PLAIN LANGUAGE. The wire values are `lan_friendly` / `public_only`, but
 * the operator never sees those terms — they see "your home network" vs "the
 * public internet only". No "SSRF" / "RFC1918" jargon anywhere.
 *
 * No `isAdmin` prop: this card only ever mounts inside BackupRestoreSection,
 * which already gates the whole page on admin (mirrors CloudTargetsCard,
 * SyncTargetsCard, EncryptedBackupCard — the other sub-cards on that page).
 */
import { useState, useEffect } from 'react';
import * as api from '../../services/api';
import type { OutboundPolicyMode } from '../../services/api';
import { useNotifications } from '../../contexts/NotificationContext';
import { logger } from '../../utils/logger';
import './OutboundPolicyCard.css';

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

export function OutboundPolicyCard() {
  const notifications = useNotifications();
  const [savedMode, setSavedMode] = useState<OutboundPolicyMode | null>(null);
  const [selected, setSelected] = useState<OutboundPolicyMode | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    api
      .getSettings()
      .then((settings) => {
        const mode = settings.ssrf_outbound_mode ?? 'lan_friendly';
        setSavedMode(mode);
        setSelected(mode);
      })
      .catch((err) => {
        logger.warn('Failed to load backup destination policy', err);
        // Default to the safe, available choice so the UI is still usable.
        setSavedMode('lan_friendly');
        setSelected('lan_friendly');
      });
  }, []);

  const dirty = selected !== null && selected !== savedMode;

  const handleSave = async () => {
    if (saving || selected === null || !dirty) return;
    setSaving(true);
    try {
      await api.saveSecurityMode(selected);
      setSavedMode(selected);
      notifications.success('Backup destination setting saved', 'Backup & Restore');
    } catch (err) {
      notifications.error(
        err instanceof Error ? err.message : 'Failed to save setting',
        'Backup & Restore',
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="backup-card outbound-policy-card">
      <div className="backup-card-header">
        <span className="material-icons">shield</span>
        <h3>Where backups can be sent</h3>
      </div>
      <p className="backup-card-description">
        Control which destinations ECM is allowed to upload backups to. This is a safety
        setting — ECM always blocks risky internal addresses no matter what you choose here.
      </p>

      <div className="outbound-policy-options" role="radiogroup" aria-label="Backup destinations">
        {OPTIONS.map((opt) => {
          const checked = selected === opt.value;
          return (
            <label
              key={opt.value}
              className={`outbound-policy-option${checked ? ' is-selected' : ''}`}
            >
              <input
                type="radio"
                name="outbound-mode"
                value={opt.value}
                data-testid={`outbound-mode-${opt.value}`}
                checked={checked}
                disabled={saving || selected === null}
                onChange={() => setSelected(opt.value)}
              />
              <span className="outbound-policy-text">
                <span className="outbound-policy-title">{opt.title}</span>
                <span className="outbound-policy-detail">{opt.detail}</span>
              </span>
            </label>
          );
        })}
      </div>

      <button
        type="button"
        className="btn-primary outbound-policy-save"
        data-testid="outbound-policy-save"
        onClick={handleSave}
        disabled={saving || !dirty}
      >
        {saving ? 'Saving...' : 'Save Settings'}
      </button>
    </div>
  );
}
