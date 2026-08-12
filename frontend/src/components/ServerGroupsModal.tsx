import { useState, useEffect, useCallback } from 'react';
import type { ServerGroup } from '../types';
import * as api from '../services/api';
import { useNotifications } from '../contexts/NotificationContext';
import { ModalOverlay } from './ModalOverlay';
import { useOwnedDialog } from '../hooks/useOwnedDialog';
import './ModalBase.css';
import './ServerGroupsModal.css';

interface ServerGroupsModalProps {
  onClose: () => void;
  /** Called after any create/rename/delete so the caller can refresh its own server-groups list. */
  onChanged: () => void;
}

interface RowState extends ServerGroup {
  isEditing?: boolean;
  editName?: string;
}

/**
 * Create/rename/delete M3U server groups (enhancedchannelmanager-hq3de.c).
 *
 * Server-group MEMBERSHIP (which M3U accounts belong to a group) is managed
 * from the account side, via the existing "Server Group" dropdown in
 * M3UAccountModal (PATCH /api/m3u/accounts/{id} with `server_group`) — that
 * UI already existed and works. This modal only manages the group records
 * themselves (name), which is what was missing: there was previously no way
 * to create a new server group at all, only select among ones that already
 * existed. `account_ids` on the create/update payload is intentionally left
 * empty here to avoid a second, possibly-inconsistent path for a
 * relationship the account-side UI already owns.
 */
export function ServerGroupsModal({ onClose, onChanged }: ServerGroupsModalProps) {
  const { titleId, containerRef } = useOwnedDialog();
  const notifications = useNotifications();
  const [groups, setGroups] = useState<RowState[]>([]);
  const [loading, setLoading] = useState(true);
  const [newName, setNewName] = useState('');
  const [creating, setCreating] = useState(false);
  const [deletingId, setDeletingId] = useState<number | null>(null);

  const loadGroups = useCallback(async () => {
    setLoading(true);
    try {
      const result = await api.getServerGroups();
      setGroups(result);
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Failed to load server groups', 'Server Groups');
    } finally {
      setLoading(false);
    }
  }, [notifications]);

  useEffect(() => {
    loadGroups();
  }, [loadGroups]);

  const handleCreate = async () => {
    const name = newName.trim();
    if (!name) return;
    setCreating(true);
    try {
      await api.createServerGroup({ name });
      setNewName('');
      await loadGroups();
      onChanged();
      notifications.success(`Created server group "${name}"`, 'Server Groups');
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Failed to create server group', 'Server Groups');
    } finally {
      setCreating(false);
    }
  };

  const startEdit = (group: RowState) => {
    setGroups((prev) => prev.map((g) =>
      g.id === group.id ? { ...g, isEditing: true, editName: g.name } : { ...g, isEditing: false }
    ));
  };

  const cancelEdit = (id: number) => {
    setGroups((prev) => prev.map((g) => (g.id === id ? { ...g, isEditing: false, editName: undefined } : g)));
  };

  const saveEdit = async (group: RowState) => {
    const name = group.editName?.trim();
    if (!name || name === group.name) {
      cancelEdit(group.id);
      return;
    }
    try {
      await api.updateServerGroup(group.id, { name });
      setGroups((prev) => prev.map((g) => (g.id === group.id ? { ...g, name, isEditing: false, editName: undefined } : g)));
      onChanged();
      notifications.success('Server group renamed', 'Server Groups');
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Failed to rename server group', 'Server Groups');
    }
  };

  const handleDelete = async (group: RowState) => {
    if (!confirm(`Delete server group "${group.name}"? M3U accounts assigned to it will lose that grouping.`)) {
      return;
    }
    setDeletingId(group.id);
    try {
      await api.deleteServerGroup(group.id);
      setGroups((prev) => prev.filter((g) => g.id !== group.id));
      onChanged();
      notifications.success(`Deleted server group "${group.name}"`, 'Server Groups');
    } catch (err) {
      notifications.error(err instanceof Error ? err.message : 'Failed to delete server group', 'Server Groups');
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <ModalOverlay onClose={onClose} role="dialog" aria-modal="true" aria-labelledby={titleId}>
      <div className="modal-container modal-md server-groups-modal" ref={containerRef}>
        <div className="modal-header">
          <h2 className="modal-title" id={titleId}>Manage Server Groups</h2>
          <button className="modal-close-btn" onClick={onClose} aria-label="Close" title="Close">
            <span className="material-icons" aria-hidden="true">close</span>
          </button>
        </div>

        <div className="modal-body">
          <p className="server-groups-hint">
            Server groups appear in the "Server Group" dropdown when editing an M3U account.
            Assign an account to a group from that dropdown.
          </p>

          <div className="server-groups-create-row">
            <input
              type="text"
              placeholder="New server group name..."
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
              disabled={creating}
            />
            <button
              className="modal-btn modal-btn-primary"
              onClick={handleCreate}
              disabled={!newName.trim() || creating}
            >
              <span className="material-icons">add</span>
              Create
            </button>
          </div>

          {loading ? (
            <div className="modal-loading">
              <span className="material-icons spinning">sync</span>
              <p>Loading server groups...</p>
            </div>
          ) : groups.length === 0 ? (
            <div className="modal-empty-state">
              <p>No server groups yet. Create one using the field above.</p>
            </div>
          ) : (
            <div className="server-groups-list">
              {groups.map((group) => (
                <div key={group.id} className="server-group-row">
                  {group.isEditing ? (
                    <input
                      type="text"
                      className="server-group-edit-input"
                      value={group.editName ?? ''}
                      onChange={(e) =>
                        setGroups((prev) => prev.map((g) => (g.id === group.id ? { ...g, editName: e.target.value } : g)))
                      }
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') saveEdit(group);
                        if (e.key === 'Escape') cancelEdit(group.id);
                      }}
                      autoFocus
                    />
                  ) : (
                    <span className="server-group-name">{group.name}</span>
                  )}
                  <div className="server-group-actions">
                    {group.isEditing ? (
                      <>
                        <button className="modal-icon-btn" onClick={() => saveEdit(group)} title="Save" aria-label="Save name">
                          <span className="material-icons" aria-hidden="true">check</span>
                        </button>
                        <button className="modal-icon-btn" onClick={() => cancelEdit(group.id)} title="Cancel" aria-label="Cancel rename">
                          <span className="material-icons" aria-hidden="true">close</span>
                        </button>
                      </>
                    ) : (
                      <>
                        <button className="modal-icon-btn" onClick={() => startEdit(group)} title="Rename" aria-label={`Rename ${group.name}`}>
                          <span className="material-icons" aria-hidden="true">edit</span>
                        </button>
                        <button
                          className="modal-icon-btn danger"
                          onClick={() => handleDelete(group)}
                          disabled={deletingId === group.id}
                          title="Delete"
                          aria-label={`Delete ${group.name}`}
                        >
                          <span className="material-icons" aria-hidden="true">
                            {deletingId === group.id ? 'hourglass_empty' : 'delete'}
                          </span>
                        </button>
                      </>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="modal-footer">
          <button className="modal-btn modal-btn-secondary" onClick={onClose}>Close</button>
        </div>
      </div>
    </ModalOverlay>
  );
}
