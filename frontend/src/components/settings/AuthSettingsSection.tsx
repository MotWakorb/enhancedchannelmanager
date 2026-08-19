/**
 * AuthSettingsSection Component
 *
 * Admin panel for configuring authentication providers and settings.
 * Allows enabling/disabling auth providers and configuring their options.
 */
import { logger } from '../../utils/logger';
import { useState, useEffect, useCallback } from 'react';
import * as api from '../../services/api';
import type { AuthSettingsPublic, AuthSettingsUpdate } from '../../types';
import { useNotifications } from '../../contexts/NotificationContext';
import { TypeToConfirmDialog } from '../TypeToConfirmDialog';
import {
  SettingsSectionHeader,
  SettingsSectionPlaceholders,
  type SettingsSectionMeta,
} from './SettingsSectionHeader';
import './AuthSettingsSection.css';

/**
 * The sections this page always has, in render order. Single authority for
 * both the loading placeholders and the loaded cards, so the Settings section
 * rail is complete from first paint and its anchor ids never move
 * (see SettingsSectionHeader.tsx; bead enhancedchannelmanager-b32co).
 */
const SECTIONS = {
  global: { icon: 'security', label: 'Global Settings' },
  local: { icon: 'password', label: 'Local Authentication' },
  dispatcharr: { icon: 'link', label: 'Dispatcharr SSO' },
} as const satisfies Record<string, SettingsSectionMeta>;

const ALWAYS_PRESENT: readonly SettingsSectionMeta[] = [
  SECTIONS.global, SECTIONS.local, SECTIONS.dispatcharr,
];

interface Props {
  isAdmin: boolean;
}

export function AuthSettingsSection({ isAdmin }: Props) {
  const notifications = useNotifications();
  const [settings, setSettings] = useState<AuthSettingsPublic | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  // Scoped confirmation for the one setting on this page that can lock nobody
  // out and let everybody in (bead enhancedchannelmanager-04c0u.12). Every
  // other field shares the same generic Save button, so the danger is invisible
  // at the point of click without this.
  const [confirmDisableAuth, setConfirmDisableAuth] = useState(false);

  // Form state for each provider
  const [localEnabled, setLocalEnabled] = useState(true);
  const [localMinPasswordLength, setLocalMinPasswordLength] = useState(8);

  const [dispatcharrEnabled, setDispatcharrEnabled] = useState(false);
  const [dispatcharrAutoCreate, setDispatcharrAutoCreate] = useState(true);

  const [requireAuth, setRequireAuth] = useState(true);

  // Load settings on mount
  useEffect(() => {
    if (!isAdmin) return;

    const loadSettings = async () => {
      try {
        setLoading(true);
        const data = await api.getAuthSettings();
        setSettings(data);

        // Populate form state
        setLocalEnabled(data.local_enabled);
        setLocalMinPasswordLength(data.local_min_password_length);

        setDispatcharrEnabled(data.dispatcharr_enabled);
        setDispatcharrAutoCreate(data.dispatcharr_auto_create_users);

        setRequireAuth(data.require_auth);
      } catch (err) {
        notifications.error('Failed to load authentication settings', 'Auth Settings');
        logger.error('Failed to load auth settings:', err);
      } finally {
        setLoading(false);
      }
    };

    loadSettings();
  }, [isAdmin, notifications]);

  const saveSettings = useCallback(async () => {
    setSaving(true);

    const update: AuthSettingsUpdate = {
      require_auth: requireAuth,
      local_enabled: localEnabled,
      local_min_password_length: localMinPasswordLength,
      dispatcharr_enabled: dispatcharrEnabled,
      dispatcharr_auto_create_users: dispatcharrAutoCreate,
    };

    try {
      await api.updateAuthSettings(update);
      // Move the persisted snapshot the confirmation gate reads. Without this
      // the very next save re-prompts for a disable that already happened.
      setSettings((previous) => (previous ? { ...previous, ...update } : previous));
      notifications.success('Authentication settings saved');
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to save settings';
      notifications.error(message, 'Auth Settings');
    } finally {
      setSaving(false);
    }
  }, [
    requireAuth,
    localEnabled, localMinPasswordLength,
    dispatcharrEnabled, dispatcharrAutoCreate,
    notifications,
  ]);

  // Confirm the *transition* into open mode, not the state of being in it.
  // Gating on `!requireAuth` alone fired this dialog on every unrelated save
  // while authentication was already off, which trains the operator to type
  // the phrase without reading it — on precisely the instances that have the
  // least protection left (bead enhancedchannelmanager-04c0u.12).
  const handleSave = useCallback(() => {
    const isDisablingAuth = (settings?.require_auth ?? true) && !requireAuth;
    if (isDisablingAuth) {
      setConfirmDisableAuth(true);
      return;
    }
    void saveSettings();
  }, [settings, requireAuth, saveSettings]);

  if (!isAdmin) {
    return (
      <div className="auth-settings-section">
        <p className="auth-settings-no-access">Admin access required to view authentication settings.</p>
      </div>
    );
  }

  // The placeholders are what keep this page's three rail entries — and the
  // anchors a shared `?section=` link names — present while the fetch is in
  // flight. Without them the rail appears from nothing when it settles, and a
  // deep link scrolls the reader away from wherever they were reading. The
  // `!isAdmin` branch above deliberately has none: that page really has no
  // sections, and it never resolves into one that does.
  if (loading) {
    return (
      <div className="auth-settings-section">
        <div className="loading-state">
          <span className="material-icons spinning">sync</span>
          Loading authentication settings...
        </div>
        <SettingsSectionPlaceholders sections={ALWAYS_PRESENT} />
      </div>
    );
  }

  return (
    <div className="auth-settings-section">

      {/* Global Settings */}
      <div className="settings-section">
        <SettingsSectionHeader section={SECTIONS.global} />
        <div className="form-group-vertical">
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={requireAuth}
              onChange={(e) => setRequireAuth(e.target.checked)}
            />
            <span>Require Authentication</span>
          </label>
          <p className="form-description">
            When disabled, the application runs in open mode (no login required).
          </p>
        </div>
      </div>

      {/* Local Authentication */}
      <div className="settings-section">
        <SettingsSectionHeader section={SECTIONS.local} />
        <div className="form-group-vertical">
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={localEnabled}
              onChange={(e) => setLocalEnabled(e.target.checked)}
            />
            <span>Enable local authentication</span>
          </label>
          <p className="form-description">
            Allow users to log in with a username and password stored locally.
          </p>
        </div>
        {localEnabled && (
          <div className="form-group-vertical">
            <label htmlFor="localMinPasswordLength">Minimum Password Length</label>
            <span className="form-description" id="localMinPasswordLengthHint">Minimum number of characters required for user passwords (6-32).</span>
            <input
              id="localMinPasswordLength"
              type="number"
              min={6}
              max={32}
              aria-describedby="localMinPasswordLengthHint"
              value={localMinPasswordLength}
              onChange={(e) => setLocalMinPasswordLength(Number(e.target.value))}
            />
          </div>
        )}
      </div>

      {/* Dispatcharr SSO */}
      <div className="settings-section">
        <SettingsSectionHeader section={SECTIONS.dispatcharr} />
        <div className="form-group-vertical">
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={dispatcharrEnabled}
              onChange={(e) => setDispatcharrEnabled(e.target.checked)}
            />
            <span>Enable Dispatcharr SSO</span>
          </label>
          <p className="form-description">
            Allow users to log in using their Dispatcharr credentials. The Dispatcharr URL is configured in General settings.
          </p>
        </div>
        {dispatcharrEnabled && (
          <div className="form-group-vertical">
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={dispatcharrAutoCreate}
                onChange={(e) => setDispatcharrAutoCreate(e.target.checked)}
              />
              <span>Auto-create Users</span>
            </label>
            <p className="form-description">
              Automatically create local accounts for Dispatcharr users on first login.
            </p>
          </div>
        )}
      </div>

      {/* Save Button */}
      <div className="auth-settings-actions">
        <button
          className="auth-save-button"
          onClick={handleSave}
          disabled={saving}
        >
          {saving ? 'Saving...' : 'Save Authentication Settings'}
        </button>
      </div>

      {confirmDisableAuth && (
        <TypeToConfirmDialog
          title="Disable Authentication"
          message={
            <>
              Anyone who can reach this ECM instance over the network will be able
              to use it — including every administrative page — without signing
              in. Existing accounts are kept, but they stop protecting anything
              until you turn this back on.
              {' '}
              This also removes most of what the MCP integration guide promises a
              stolen MCP key cannot do: taking, downloading or restoring backups,
              and creating, changing or deleting outbound destinations, become
              reachable without any credential. Testing an outbound destination
              does not. That, along with rotating the MCP key and changing TLS
              certificate material, stays administrator-only once this instance
              has an operator identity. Account administration stays closed to
              anonymous callers and to the MCP key itself in this mode.
            </>
          }
          confirmText="DISABLE AUTHENTICATION"
          confirmLabel="Disable Authentication"
          busy={saving}
          onCancel={() => setConfirmDisableAuth(false)}
          onConfirm={async () => {
            await saveSettings();
            setConfirmDisableAuth(false);
          }}
        />
      )}
    </div>
  );
}

export default AuthSettingsSection;
