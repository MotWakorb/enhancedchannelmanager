/**
 * Provider-scoped Event Sync group picker (bead 38dzi / Option A P4).
 *
 * Channel-group NAMES are globally unique in Dispatcharr, so two M3U providers
 * that both carry "MLB PPV" share ONE channel_group_id — but their
 * auto_channel_sync / enabled settings live on the per-(provider, group)
 * junction. This picker lets the operator select a group SCOPED to a provider:
 * "MLB PPV — Provider A (auto-sync ON)" as the master, "MLB PPV — Provider B
 * (auto-sync OFF)" as a secondary.
 *
 * UX (ux-designer spec): collapse-by-default, expand-on-conflict. A group
 * carried by ONE provider renders as a flat row (the provider is
 * unambiguous). A group carried by 2+ providers renders as an expandable
 * parent whose sub-rows are the per-provider scopes plus an explicit
 * "Any provider (whole group)" scope (m3u_account_id = null). Sync-status
 * valence is ROLE-RELATIVE: a master should be auto-sync ON (OFF warns), a
 * secondary should be OFF (ON warns); the warning offers the existing Fix.
 */
import { useMemo, useState } from 'react';
import type { EventSyncGroupScope } from '../../types/eventSync';
import { scopeKey, type GroupProviderRow } from './providerScopedGroups';
import './ProviderScopedGroupPicker.css';

interface GroupNode {
  groupId: number;
  groupName: string;
  providers: GroupProviderRow[];
}

export interface ProviderScopedGroupPickerProps {
  role: 'master' | 'secondary';
  rows: GroupProviderRow[];
  /** master: a single scope or null; secondary: a list of scopes. */
  value: EventSyncGroupScope | EventSyncGroupScope[] | null;
  onChange: (next: EventSyncGroupScope | EventSyncGroupScope[] | null) => void;
  /** When false, junction rows whose provider setting is disabled are hidden
   *  (unless already referenced by the rule — the round-trip guard). */
  showAll: boolean;
  /** secondary: the master's exact scope, excluded so a group+provider cannot
   *  be both master and secondary (the SAME group with a DIFFERENT provider IS
   *  allowed). */
  excludeScope?: EventSyncGroupScope | null;
  /** Offer the "Fix" affordance for a sync mismatch (reuses the editor's
   *  existing auto-sync fix dialog). */
  onRequestFix?: (target: { accountId: number; groupId: number }) => void;
  disabled?: boolean;
}

export function ProviderScopedGroupPicker({
  role,
  rows,
  value,
  onChange,
  showAll,
  excludeScope,
  onRequestFix,
  disabled,
}: ProviderScopedGroupPickerProps) {
  const [search, setSearch] = useState('');
  const [expanded, setExpanded] = useState<Set<number>>(new Set());

  const selected: EventSyncGroupScope[] = useMemo(() => {
    if (value == null) return [];
    return Array.isArray(value) ? value : [value];
  }, [value]);
  const selectedKeys = useMemo(
    () => new Set(selected.map(scopeKey)),
    [selected],
  );

  // Group junction rows by group id -> nodes (deterministic order).
  const nodes: GroupNode[] = useMemo(() => {
    const byGroup = new Map<number, GroupNode>();
    for (const r of rows) {
      let node = byGroup.get(r.groupId);
      if (!node) {
        node = { groupId: r.groupId, groupName: r.groupName, providers: [] };
        byGroup.set(r.groupId, node);
      }
      node.providers.push(r);
    }
    const list = [...byGroup.values()];
    for (const n of list) {
      n.providers.sort((a, b) =>
        a.m3uAccountName.localeCompare(b.m3uAccountName),
      );
    }
    list.sort((a, b) => a.groupName.localeCompare(b.groupName));
    return list;
  }, [rows]);

  const isSelectedScope = (s: EventSyncGroupScope) =>
    selectedKeys.has(scopeKey(s));

  // A scope is "kept visible" (round-trip guard) if it is selected or is the
  // excluded master scope — never dropped by the enabled-only filter.
  const isReferenced = (groupId: number, providerId: number | null) => {
    const k = scopeKey({ group_id: groupId, m3u_account_id: providerId });
    return (
      selectedKeys.has(k) ||
      (excludeScope != null && scopeKey(excludeScope) === k)
    );
  };

  const q = search.trim().toLowerCase();
  const matchesSearch = (n: GroupNode) => {
    if (!q) return true;
    if (n.groupName.toLowerCase().includes(q)) return true;
    return n.providers.some(p => p.m3uAccountName.toLowerCase().includes(q));
  };

  // Desired auto-sync for this role; the OTHER value warns.
  const wantsSync = role === 'master';

  const toggleExpanded = (groupId: number) => {
    setExpanded(prev => {
      const next = new Set(prev);
      if (next.has(groupId)) next.delete(groupId);
      else next.add(groupId);
      return next;
    });
  };

  const selectScope = (scope: EventSyncGroupScope) => {
    if (role === 'master') {
      onChange(scope);
    } else {
      const key = scopeKey(scope);
      const has = selected.some(s => scopeKey(s) === key);
      onChange(
        has
          ? selected.filter(s => scopeKey(s) !== key)
          : [...selected, scope],
      );
    }
  };

  const isExcluded = (scope: EventSyncGroupScope) =>
    excludeScope != null && scopeKey(excludeScope) === scopeKey(scope);

  const syncBadge = (autoSync: boolean, roleWarns: boolean) => {
    const mismatch = autoSync !== wantsSync;
    return (
      <span
        className={
          'psg-sync-badge ' +
          (mismatch ? 'psg-sync-mismatch' : 'psg-sync-ok')
        }
      >
        <span className="material-icons" aria-hidden="true">
          {autoSync ? 'sync' : 'sync_disabled'}
        </span>
        Auto-sync {autoSync ? 'ON' : 'OFF'}
        {mismatch && roleWarns ? ' ⚠' : ' ✓'}
      </span>
    );
  };

  const renderProviderRow = (p: GroupProviderRow, showGroupName: boolean) => {
    const scope: EventSyncGroupScope = {
      group_id: p.groupId,
      m3u_account_id: p.m3uAccountId,
    };
    const excluded = isExcluded(scope);
    const checked = isSelectedScope(scope);
    const mismatch = p.autoChannelSync !== wantsSync;
    const InputTag = role === 'master' ? 'radio' : 'checkbox';
    const label = showGroupName
      ? `${p.groupName} · ${p.m3uAccountName}`
      : p.m3uAccountName;
    return (
      <li key={scopeKey(scope)} className="psg-provider-row">
        <label className={excluded ? 'psg-option psg-excluded' : 'psg-option'}>
          <input
            type={InputTag}
            checked={checked}
            disabled={disabled || excluded}
            onChange={() => selectScope(scope)}
            data-testid={`psg-${role}-${p.groupId}-${p.m3uAccountId}`}
          />
          <span className="psg-label">{label}</span>
          {syncBadge(p.autoChannelSync, true)}
          <span className="psg-streams">
            {p.streamCount == null ? '—' : `${p.streamCount} streams`}
          </span>
          {!p.enabled && (
            <span className="psg-disabled-hint">(disabled)</span>
          )}
        </label>
        {checked && mismatch && onRequestFix && (
          <div className="psg-mismatch" role="alert">
            <span>
              Auto-sync is {p.autoChannelSync ? 'ON' : 'OFF'} — {role === 'master'
                ? 'a master needs it ON so Dispatcharr owns the channels.'
                : 'a secondary needs it OFF or Dispatcharr creates duplicate channels.'}
            </span>
            <button
              type="button"
              className="psg-fix-btn"
              disabled={disabled}
              onClick={() =>
                onRequestFix({ accountId: p.m3uAccountId, groupId: p.groupId })
              }
            >
              Fix: turn auto-sync {wantsSync ? 'ON' : 'OFF'} for {p.m3uAccountName}…
            </button>
          </div>
        )}
      </li>
    );
  };

  const renderWholeGroupRow = (node: GroupNode) => {
    const scope: EventSyncGroupScope = {
      group_id: node.groupId,
      m3u_account_id: null,
    };
    const excluded = isExcluded(scope);
    return (
      <li key={scopeKey(scope)} className="psg-provider-row">
        <label className={excluded ? 'psg-option psg-excluded' : 'psg-option'}>
          <input
            type={role === 'master' ? 'radio' : 'checkbox'}
            checked={isSelectedScope(scope)}
            disabled={disabled || excluded}
            onChange={() => selectScope(scope)}
            data-testid={`psg-${role}-${node.groupId}-any`}
          />
          <span className="psg-label">Any provider (whole group)</span>
        </label>
      </li>
    );
  };

  const visibleNodes = nodes.filter(matchesSearch);

  return (
    <div className="provider-scoped-group-picker" data-testid={`psg-${role}`}>
      <input
        type="text"
        className="psg-search"
        placeholder="Filter groups…"
        value={search}
        onChange={e => setSearch(e.target.value)}
        disabled={disabled}
        aria-label={`Filter ${role} groups`}
      />
      <ul className="psg-list">
        {visibleNodes.length === 0 && (
          <li className="psg-empty">No matching groups</li>
        )}
        {visibleNodes.map(node => {
          const visibleProviders = node.providers.filter(
            p => showAll || p.enabled || isReferenced(p.groupId, p.m3uAccountId),
          );
          if (visibleProviders.length === 0) return null;

          // Single-provider group -> flat row (provider unambiguous).
          if (visibleProviders.length === 1 && node.providers.length === 1) {
            return renderProviderRow(visibleProviders[0], true);
          }

          // Multi-provider group -> expandable parent.
          const isOpen =
            expanded.has(node.groupId) ||
            !!q ||
            visibleProviders.some(
              p =>
                isSelectedScope({
                  group_id: p.groupId,
                  m3u_account_id: p.m3uAccountId,
                }) || p.autoChannelSync !== wantsSync,
            ) ||
            isSelectedScope({ group_id: node.groupId, m3u_account_id: null });
          const totalStreams = visibleProviders.reduce(
            (sum, p) => sum + (p.streamCount ?? 0),
            0,
          );
          return (
            <li key={node.groupId} className="psg-group-node" role="group"
                aria-label={`${node.groupName}, ${visibleProviders.length} providers`}>
              <button
                type="button"
                className="psg-group-parent"
                aria-expanded={isOpen}
                onClick={() => toggleExpanded(node.groupId)}
                disabled={disabled}
              >
                <span className="material-icons" aria-hidden="true">
                  {isOpen ? 'expand_more' : 'chevron_right'}
                </span>
                <span className="psg-group-name">{node.groupName}</span>
                <span className="psg-group-meta">
                  {visibleProviders.length} providers · {totalStreams} streams
                </span>
              </button>
              {isOpen && (
                <ul className="psg-sublist">
                  {visibleProviders.map(p => renderProviderRow(p, false))}
                  {renderWholeGroupRow(node)}
                </ul>
              )}
            </li>
          );
        })}
      </ul>

      {role === 'secondary' && selected.length > 0 && (
        <div className="psg-chips" aria-live="polite">
          {selected.map(s => {
            const node = nodes.find(n => n.groupId === s.group_id);
            const prov =
              s.m3u_account_id == null
                ? 'Any provider'
                : node?.providers.find(p => p.m3uAccountId === s.m3u_account_id)
                    ?.m3uAccountName ?? `Provider ${s.m3u_account_id}`;
            return (
              <span key={scopeKey(s)} className="psg-chip">
                {(node?.groupName ?? `Group ${s.group_id}`)} · {prov}
                <button
                  type="button"
                  className="psg-chip-remove"
                  aria-label={`Remove ${node?.groupName ?? s.group_id}`}
                  onClick={() => selectScope(s)}
                  disabled={disabled}
                >
                  ✕
                </button>
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}

export default ProviderScopedGroupPicker;
