import { useState, useEffect, useCallback } from 'react';
import * as api from '../../services/api';
import { useNotifications } from '../../contexts/NotificationContext';
import { getDateLocale } from '../../utils/formatting';
import './SyncTargetsCard.css';

/**
 * Cross-Instance Sync card (epic i39wu / bead nnl9s) — the first operator-facing
 * surface for one-way config sync from this instance (A) to a managed replica (B).
 *
 * Mirrors the EncryptedBackupCard precedent: a focused card inside Settings ->
 * Backup & Restore, admin-gated by the section it mounts into.
 *
 * Load-bearing operator-expectation copy (ADR-013) is surfaced prominently:
 *   - sync is ONE-WAY — edits on B are overwritten by A on the next apply;
 *   - credentials are NOT synced — they must be re-entered on B.
 *
 * Safety model for "Sync now": the default action is a counts-only DRY-RUN
 * preview (confirm_apply:false). Only after a successful preview does an
 * explicit, confirm-gated "Apply" (confirm_apply:true) become available — apply
 * is a source-wins overwrite of B, so we make the operator confirm it.
 */

/** Tri-state badge derived from SyncTarget.last_outcome. */
type OutcomeKind = 'success' | 'partial' | 'failure' | 'none';

function outcomeKind(outcome?: string | null): OutcomeKind {
  if (!outcome) return 'none';
  const o = outcome.toLowerCase();
  if (o.includes('success') || o === 'ok') return 'success';
  if (o.includes('partial') || o.includes('skip')) return 'partial';
  return 'failure';
}

function outcomeLabel(kind: OutcomeKind, raw?: string | null): string {
  if (kind === 'none') return 'Never synced';
  return raw || kind;
}

function formatTimestamp(ts?: string | null): string {
  if (!ts) return 'never';
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return ts;
  return d.toLocaleString(getDateLocale());
}

interface AddForm {
  name: string;
  baseUrl: string;
  authMode: 'password' | 'api_key';
  username: string;
  password: string;
  apiKey: string;
  insecure: boolean;
}

const EMPTY_FORM: AddForm = {
  name: '',
  baseUrl: '',
  authMode: 'password',
  username: '',
  password: '',
  apiKey: '',
  insecure: false,
};

export function SyncTargetsCard() {
  const notifications = useNotifications();
  const [targets, setTargets] = useState<api.SyncTarget[]>([]);
  const [loading, setLoading] = useState(false);
  const [showAdd, setShowAdd] = useState(false);
  const [form, setForm] = useState<AddForm>(EMPTY_FORM);
  const [creating, setCreating] = useState(false);
  // Per-target id currently busy with a sync/preview/apply/toggle/delete op.
  const [busyId, setBusyId] = useState<number | null>(null);
  // Targets that have a successful preview pending, so Apply is offered.
  const [previewedIds, setPreviewedIds] = useState<Set<number>>(new Set());

  const loadTargets = useCallback(async () => {
    setLoading(true);
    try {
      const list = await api.listSyncTargets();
      setTargets(list);
    } catch (err) {
      notifications.error(
        err instanceof Error ? err.message : 'Failed to load sync targets',
        'Cross-Instance Sync',
      );
    } finally {
      setLoading(false);
    }
  }, [notifications]);

  useEffect(() => {
    loadTargets();
  }, [loadTargets]);

  const handleCreate = useCallback(async () => {
    if (!form.name.trim() || !form.baseUrl.trim() || creating) return;
    setCreating(true);
    try {
      const credentials: Record<string, string> =
        form.authMode === 'api_key'
          ? { api_key: form.apiKey }
          : { username: form.username, password: form.password };
      await api.createSyncTarget({
        name: form.name.trim(),
        base_url: form.baseUrl.trim(),
        credentials,
        insecure: form.insecure,
      });
      notifications.success(`Added sync target '${form.name.trim()}'`, 'Cross-Instance Sync');
      setForm(EMPTY_FORM);
      setShowAdd(false);
      await loadTargets();
    } catch (err) {
      notifications.error(
        err instanceof Error ? err.message : 'Failed to add sync target',
        'Cross-Instance Sync',
      );
    } finally {
      setCreating(false);
    }
  }, [form, creating, notifications, loadTargets]);

  const handleToggleEnabled = useCallback(
    async (target: api.SyncTarget) => {
      setBusyId(target.id);
      try {
        await api.updateSyncTarget(target.id, { enabled: !target.enabled });
        notifications.success(
          `${target.name} ${target.enabled ? 'disabled' : 'enabled'}`,
          'Cross-Instance Sync',
        );
        await loadTargets();
      } catch (err) {
        notifications.error(
          err instanceof Error ? err.message : 'Failed to update sync target',
          'Cross-Instance Sync',
        );
      } finally {
        setBusyId(null);
      }
    },
    [notifications, loadTargets],
  );

  const handleDelete = useCallback(
    async (target: api.SyncTarget) => {
      const ok = window.confirm(
        `Delete sync target '${target.name}'? This stops future syncs to it. ` +
          `It does NOT undo any config already pushed to B.`,
      );
      if (!ok) return;
      setBusyId(target.id);
      try {
        await api.deleteSyncTarget(target.id);
        notifications.success(`Deleted sync target '${target.name}'`, 'Cross-Instance Sync');
        setPreviewedIds((prev) => {
          const next = new Set(prev);
          next.delete(target.id);
          return next;
        });
        await loadTargets();
      } catch (err) {
        notifications.error(
          err instanceof Error ? err.message : 'Failed to delete sync target',
          'Cross-Instance Sync',
        );
      } finally {
        setBusyId(null);
      }
    },
    [notifications, loadTargets],
  );

  const handlePreview = useCallback(
    async (target: api.SyncTarget) => {
      setBusyId(target.id);
      try {
        const result = await api.runTask('dbas_sync', undefined, {
          sync_target_id: target.id,
          confirm_apply: false,
        });
        if (result.success) {
          setPreviewedIds((prev) => new Set(prev).add(target.id));
          notifications.success(
            result.message || `Preview complete for ${target.name} — review, then Apply.`,
            'Sync Preview (dry run)',
          );
        } else {
          notifications.error(
            result.error || result.message || 'Preview failed',
            'Sync Preview (dry run)',
          );
        }
      } catch (err) {
        notifications.error(
          err instanceof Error ? err.message : 'Preview failed',
          'Sync Preview (dry run)',
        );
      } finally {
        setBusyId(null);
      }
    },
    [notifications],
  );

  const handleApply = useCallback(
    async (target: api.SyncTarget) => {
      const ok = window.confirm(
        `Apply sync to '${target.name}'? This OVERWRITES B's config with A's ` +
          `(source-wins). Any edits made directly on B will be lost. ` +
          `Credentials are NOT pushed — re-enter them on B.`,
      );
      if (!ok) return;
      setBusyId(target.id);
      try {
        const result = await api.runTask('dbas_sync', undefined, {
          sync_target_id: target.id,
          confirm_apply: true,
        });
        if (result.success) {
          notifications.success(
            result.message || `Sync applied to ${target.name}`,
            'Cross-Instance Sync',
          );
          setPreviewedIds((prev) => {
            const next = new Set(prev);
            next.delete(target.id);
            return next;
          });
          await loadTargets();
        } else {
          notifications.error(
            result.error || result.message || 'Apply failed',
            'Cross-Instance Sync',
          );
        }
      } catch (err) {
        notifications.error(
          err instanceof Error ? err.message : 'Apply failed',
          'Cross-Instance Sync',
        );
      } finally {
        setBusyId(null);
      }
    },
    [notifications, loadTargets],
  );

  const updateForm = <K extends keyof AddForm>(key: K, value: AddForm[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  return (
    <div className="backup-card sync-targets-card">
      <div className="backup-card-header">
        <span className="material-icons">sync_alt</span>
        <h3>Cross-Instance Sync</h3>
      </div>
      <p className="backup-card-description">
        Push this instance&apos;s configuration to one or more remote Dispatcharr instances. Use this
        to keep a replica in step with your primary install.
      </p>

      <div className="stc-warning stc-warning-oneway">
        <span className="material-icons">warning</span>
        <span>
          <strong>One-way sync.</strong> B is a <strong>managed replica</strong> — edits on B are
          overwritten by A on the next apply. Do not edit B directly and expect it to stick.
        </span>
      </div>

      <div className="stc-warning stc-warning-creds">
        <span className="material-icons">key_off</span>
        <span>
          <strong>Credentials are not synced.</strong> Provider passwords and API keys are never
          pushed to B — re-enter them on B after the first sync.
        </span>
      </div>

      {loading ? (
        <div className="backup-loading">
          <span className="material-icons spinning">sync</span>
          Loading sync targets...
        </div>
      ) : targets.length === 0 ? (
        <div className="stc-empty">No sync targets configured yet.</div>
      ) : (
        <div className="stc-target-list">
          {targets.map((target) => {
            const kind = outcomeKind(target.last_outcome);
            const busy = busyId === target.id;
            const previewed = previewedIds.has(target.id);
            return (
              <div
                key={target.id}
                className={`stc-target-row${target.enabled ? '' : ' is-disabled'}`}
                data-testid={`sync-target-row-${target.id}`}
              >
                <div className="stc-target-main">
                  <div className="stc-target-name">{target.name}</div>
                  <div className="stc-target-url">{target.base_url}</div>
                  <div className="stc-target-status">
                    <span
                      className={`stc-badge stc-badge-${kind}`}
                      data-testid={`sync-target-status-${target.id}`}
                    >
                      {outcomeLabel(kind, target.last_outcome)}
                    </span>
                    {target.insecure && (
                      <span
                        className="stc-badge stc-badge-insecure"
                        data-testid={`sync-target-insecure-${target.id}`}
                        title="Certificate checking is turned off for this target. Only safe on a trusted instance with a self-signed certificate."
                      >
                        <span className="material-icons" aria-hidden="true">
                          gpp_maybe
                        </span>
                        Certificate check off
                      </span>
                    )}
                    <span className="stc-last-synced">
                      Last synced: {formatTimestamp(target.last_full_sync_at)}
                    </span>
                  </div>
                </div>

                <div className="stc-target-actions">
                  <button
                    type="button"
                    className={`stc-toggle${target.enabled ? ' is-on' : ''}`}
                    aria-pressed={target.enabled}
                    title={target.enabled ? 'Disable (kill switch)' : 'Enable'}
                    disabled={busy}
                    data-testid={`sync-target-toggle-${target.id}`}
                    onClick={() => handleToggleEnabled(target)}
                  >
                    <span className="material-icons">
                      {target.enabled ? 'toggle_on' : 'toggle_off'}
                    </span>
                    {target.enabled ? 'Enabled' : 'Disabled'}
                  </button>

                  <button
                    type="button"
                    className="btn-secondary stc-action-btn"
                    disabled={busy || !target.enabled}
                    title="Dry-run preview (no changes applied)"
                    data-testid={`sync-target-preview-${target.id}`}
                    onClick={() => handlePreview(target)}
                  >
                    <span className="material-icons">{busy ? 'hourglass_empty' : 'visibility'}</span>
                    Sync now (preview)
                  </button>

                  {previewed && (
                    <button
                      type="button"
                      className="btn-primary stc-action-btn stc-apply-btn"
                      disabled={busy || !target.enabled}
                      title="Apply — overwrites B with A (source-wins)"
                      data-testid={`sync-target-apply-${target.id}`}
                      onClick={() => handleApply(target)}
                    >
                      <span className="material-icons">publish</span>
                      Apply
                    </button>
                  )}

                  <button
                    type="button"
                    className="btn-secondary stc-action-btn stc-delete-btn"
                    disabled={busy}
                    title="Delete sync target"
                    data-testid={`sync-target-delete-${target.id}`}
                    onClick={() => handleDelete(target)}
                    aria-label="Delete sync target"
                  >
                    <span className="material-icons" aria-hidden="true">delete</span>
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {showAdd ? (
        <div className="stc-add-form">
          <div className="stc-field">
            <label htmlFor="stc-name">Name</label>
            <input
              id="stc-name"
              type="text"
              value={form.name}
              disabled={creating}
              onChange={(e) => updateForm('name', e.target.value)}
              placeholder="e.g. Living Room B"
            />
          </div>

          <div className="stc-field">
            <label htmlFor="stc-base-url">Base URL</label>
            <input
              id="stc-base-url"
              type="text"
              value={form.baseUrl}
              disabled={creating}
              onChange={(e) => updateForm('baseUrl', e.target.value)}
              placeholder="https://dispatcharr-b.example.com"
            />
          </div>

          <div className="stc-field">
            <label htmlFor="stc-auth-mode">Authentication</label>
            <select
              id="stc-auth-mode"
              value={form.authMode}
              disabled={creating}
              onChange={(e) => updateForm('authMode', e.target.value as AddForm['authMode'])}
            >
              <option value="password">Username &amp; password</option>
              <option value="api_key">API key</option>
            </select>
          </div>

          {form.authMode === 'password' ? (
            <>
              <div className="stc-field">
                <label htmlFor="stc-username">Username</label>
                <input
                  id="stc-username"
                  type="text"
                  autoComplete="off"
                  value={form.username}
                  disabled={creating}
                  onChange={(e) => updateForm('username', e.target.value)}
                />
              </div>
              <div className="stc-field">
                <label htmlFor="stc-password">Password</label>
                <input
                  id="stc-password"
                  type="password"
                  autoComplete="new-password"
                  value={form.password}
                  disabled={creating}
                  onChange={(e) => updateForm('password', e.target.value)}
                />
              </div>
            </>
          ) : (
            <div className="stc-field">
              <label htmlFor="stc-api-key">API key</label>
              <input
                id="stc-api-key"
                type="password"
                autoComplete="new-password"
                value={form.apiKey}
                disabled={creating}
                onChange={(e) => updateForm('apiKey', e.target.value)}
              />
            </div>
          )}

          <label className="stc-checkbox">
            <input
              type="checkbox"
              checked={form.insecure}
              disabled={creating}
              onChange={(e) => updateForm('insecure', e.target.checked)}
            />
            <span>
              <strong>Allow insecure TLS.</strong> Skip certificate verification when connecting to
              B. Only use this for a trusted instance with a self-signed certificate.
            </span>
          </label>

          <div className="stc-add-actions">
            <button
              type="button"
              className="btn-primary"
              disabled={creating || !form.name.trim() || !form.baseUrl.trim()}
              onClick={handleCreate}
            >
              <span className="material-icons">{creating ? 'hourglass_empty' : 'add'}</span>
              Create target
            </button>
            <button
              type="button"
              className="btn-secondary"
              disabled={creating}
              onClick={() => {
                setForm(EMPTY_FORM);
                setShowAdd(false);
              }}
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          className="btn-secondary stc-add-toggle"
          onClick={() => setShowAdd(true)}
        >
          <span className="material-icons">add</span>
          Add sync target
        </button>
      )}
    </div>
  );
}
