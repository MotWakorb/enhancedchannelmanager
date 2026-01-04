import { useState, useMemo } from 'react';
import type { M3UAccount } from '../types';
import './M3ULinkedAccountsModal.css';

interface M3ULinkedAccountsModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSave: (linkGroups: number[][]) => void;
  accounts: M3UAccount[];
  linkGroups: number[][];
}

export function M3ULinkedAccountsModal({
  isOpen,
  onClose,
  onSave,
  accounts,
  linkGroups: initialLinkGroups,
}: M3ULinkedAccountsModalProps) {
  const [linkGroups, setLinkGroups] = useState<number[][]>(initialLinkGroups);
  const [editingGroupIndex, setEditingGroupIndex] = useState<number | null>(null);
  const [selectedAccountIds, setSelectedAccountIds] = useState<Set<number>>(new Set());
  const [hasChanges, setHasChanges] = useState(false);

  // Reset state when modal opens
  useState(() => {
    if (isOpen) {
      setLinkGroups(initialLinkGroups);
      setEditingGroupIndex(null);
      setSelectedAccountIds(new Set());
      setHasChanges(false);
    }
  });

  // Create a map of account ID to account for quick lookups
  const accountMap = useMemo(() => {
    const map = new Map<number, M3UAccount>();
    accounts.forEach(a => map.set(a.id, a));
    return map;
  }, [accounts]);

  // Get account IDs that are already in a link group (for filtering)
  const accountsInLinkGroups = useMemo(() => {
    const ids = new Set<number>();
    linkGroups.forEach(group => group.forEach(id => ids.add(id)));
    return ids;
  }, [linkGroups]);

  // Available accounts for adding to a new/edited link group
  const availableAccounts = useMemo(() => {
    if (editingGroupIndex === null) {
      // Creating new group - exclude accounts already in any group
      return accounts.filter(a => !accountsInLinkGroups.has(a.id));
    } else {
      // Editing existing group - include accounts in this group + unlinked accounts
      const currentGroupIds = new Set(linkGroups[editingGroupIndex]);
      return accounts.filter(a => !accountsInLinkGroups.has(a.id) || currentGroupIds.has(a.id));
    }
  }, [accounts, accountsInLinkGroups, editingGroupIndex, linkGroups]);

  const handleStartCreate = () => {
    setEditingGroupIndex(-1); // -1 means creating new
    setSelectedAccountIds(new Set());
  };

  const handleStartEdit = (index: number) => {
    setEditingGroupIndex(index);
    setSelectedAccountIds(new Set(linkGroups[index]));
  };

  const handleCancelEdit = () => {
    setEditingGroupIndex(null);
    setSelectedAccountIds(new Set());
  };

  const handleToggleAccount = (accountId: number) => {
    setSelectedAccountIds(prev => {
      const next = new Set(prev);
      if (next.has(accountId)) {
        next.delete(accountId);
      } else {
        next.add(accountId);
      }
      return next;
    });
  };

  const handleSaveGroup = () => {
    if (selectedAccountIds.size < 2) {
      return; // Need at least 2 accounts to form a link group
    }

    const newGroup = Array.from(selectedAccountIds).sort((a, b) => a - b);

    if (editingGroupIndex === -1) {
      // Creating new group
      setLinkGroups(prev => [...prev, newGroup]);
    } else if (editingGroupIndex !== null) {
      // Editing existing group
      setLinkGroups(prev => prev.map((group, i) => i === editingGroupIndex ? newGroup : group));
    }

    setEditingGroupIndex(null);
    setSelectedAccountIds(new Set());
    setHasChanges(true);
  };

  const handleDeleteGroup = (index: number) => {
    setLinkGroups(prev => prev.filter((_, i) => i !== index));
    setHasChanges(true);
  };

  const handleSaveAll = () => {
    // Filter out any groups that have become invalid (less than 2 accounts)
    const validGroups = linkGroups.filter(group => group.length >= 2);
    onSave(validGroups);
    onClose();
  };

  if (!isOpen) return null;

  const isEditing = editingGroupIndex !== null;

  return (
    <div className="modal-overlay">
      <div className="modal-content m3u-linked-accounts-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Manage Linked Accounts</h2>
          <button className="close-btn" onClick={onClose}>
            &times;
          </button>
        </div>

        <div className="modal-body">
          {isEditing ? (
            // Edit/Create mode
            <div className="edit-mode">
              <h3>{editingGroupIndex === -1 ? 'Create Link Group' : 'Edit Link Group'}</h3>
              <p className="edit-description">
                Select 2 or more accounts to link together. When you change group settings for one account,
                the same changes will be applied to all linked accounts.
              </p>

              <div className="account-selection">
                {availableAccounts.length === 0 ? (
                  <p className="no-accounts">All accounts are already in link groups.</p>
                ) : (
                  availableAccounts.map(account => (
                    <label key={account.id} className="account-checkbox">
                      <input
                        type="checkbox"
                        checked={selectedAccountIds.has(account.id)}
                        onChange={() => handleToggleAccount(account.id)}
                      />
                      <span className="account-name">{account.name}</span>
                    </label>
                  ))
                )}
              </div>

              <div className="edit-actions">
                <button className="btn-secondary" onClick={handleCancelEdit}>
                  Cancel
                </button>
                <button
                  className="btn-primary"
                  onClick={handleSaveGroup}
                  disabled={selectedAccountIds.size < 2}
                  title={selectedAccountIds.size < 2 ? 'Select at least 2 accounts' : ''}
                >
                  {editingGroupIndex === -1 ? 'Create Group' : 'Update Group'}
                </button>
              </div>
            </div>
          ) : (
            // View mode
            <>
              {linkGroups.length === 0 ? (
                <div className="empty-state">
                  <span className="material-icons">link</span>
                  <p>No linked accounts configured.</p>
                  <p className="hint">
                    Link accounts from the same provider to sync group settings across them.
                  </p>
                </div>
              ) : (
                <div className="link-groups-list">
                  {linkGroups.map((group, index) => (
                    <div key={index} className="link-group-card">
                      <div className="link-group-header">
                        <span className="link-group-label">Link Group {index + 1}</span>
                        <div className="link-group-actions">
                          <button
                            className="btn-icon"
                            onClick={() => handleStartEdit(index)}
                            title="Edit group"
                          >
                            <span className="material-icons">edit</span>
                          </button>
                          <button
                            className="btn-icon delete"
                            onClick={() => handleDeleteGroup(index)}
                            title="Delete group"
                          >
                            <span className="material-icons">delete</span>
                          </button>
                        </div>
                      </div>
                      <div className="link-group-accounts">
                        {group.map(accountId => {
                          const account = accountMap.get(accountId);
                          return (
                            <span key={accountId} className="account-chip">
                              {account?.name ?? `Account ${accountId}`}
                            </span>
                          );
                        })}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              <button
                className="btn-create-group"
                onClick={handleStartCreate}
                disabled={availableAccounts.length < 2}
                title={availableAccounts.length < 2 ? 'Need at least 2 unlinked accounts' : ''}
              >
                <span className="material-icons">add</span>
                Create Link Group
              </button>
            </>
          )}
        </div>

        <div className="modal-footer">
          <button className="btn-secondary" onClick={onClose}>
            {hasChanges ? 'Discard' : 'Close'}
          </button>
          {hasChanges && !isEditing && (
            <button className="btn-primary" onClick={handleSaveAll}>
              Save Changes
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
