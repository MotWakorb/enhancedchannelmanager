import { useState, useEffect, useCallback } from 'react';
import type { M3UAccount, M3UFilter, M3UFilterCreateRequest } from '../types';
import * as api from '../services/api';
import './M3UFiltersModal.css';

interface M3UFiltersModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSaved: () => void;
  account: M3UAccount;
}

type FilterType = 'group' | 'name' | 'url';

interface EditingFilter {
  id?: number;
  filter_type: FilterType;
  regex_pattern: string;
  exclude: boolean;
  order: number;
}

const emptyFilter: EditingFilter = {
  filter_type: 'group',
  regex_pattern: '',
  exclude: true,
  order: 0,
};

export function M3UFiltersModal({
  isOpen,
  onClose,
  onSaved,
  account,
}: M3UFiltersModalProps) {
  const [filters, setFilters] = useState<M3UFilter[]>([]);
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editingFilter, setEditingFilter] = useState<EditingFilter | null>(null);
  const [isAddingNew, setIsAddingNew] = useState(false);

  const loadFilters = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.getM3UFilters(account.id);
      setFilters(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load filters');
    } finally {
      setLoading(false);
    }
  }, [account.id]);

  // Load filters when modal opens
  useEffect(() => {
    if (isOpen) {
      loadFilters();
      setEditingFilter(null);
      setIsAddingNew(false);
    }
  }, [isOpen, loadFilters]);

  const handleAddNew = () => {
    const nextOrder = filters.length > 0
      ? Math.max(...filters.map(f => f.order)) + 1
      : 1;
    setEditingFilter({ ...emptyFilter, order: nextOrder });
    setIsAddingNew(true);
  };

  const handleEdit = (filter: M3UFilter) => {
    setEditingFilter({
      id: filter.id,
      filter_type: filter.filter_type,
      regex_pattern: filter.regex_pattern,
      exclude: filter.exclude,
      order: filter.order,
    });
    setIsAddingNew(false);
  };

  const handleCancelEdit = () => {
    setEditingFilter(null);
    setIsAddingNew(false);
  };

  const handleSaveFilter = async () => {
    if (!editingFilter) return;

    if (!editingFilter.regex_pattern.trim()) {
      setError('Regex pattern is required');
      return;
    }

    setSaving(true);
    setError(null);

    try {
      const filterData: M3UFilterCreateRequest = {
        filter_type: editingFilter.filter_type,
        regex_pattern: editingFilter.regex_pattern.trim(),
        exclude: editingFilter.exclude,
        order: editingFilter.order,
      };

      if (isAddingNew) {
        await api.createM3UFilter(account.id, filterData);
      } else if (editingFilter.id) {
        await api.updateM3UFilter(account.id, editingFilter.id, filterData);
      }

      await loadFilters();
      setEditingFilter(null);
      setIsAddingNew(false);
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save filter');
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (filter: M3UFilter) => {
    if (!confirm(`Are you sure you want to delete this filter?`)) {
      return;
    }

    setError(null);
    try {
      await api.deleteM3UFilter(account.id, filter.id);
      await loadFilters();
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to delete filter');
    }
  };

  const getFilterTypeLabel = (type: FilterType) => {
    switch (type) {
      case 'group': return 'Group';
      case 'name': return 'Name';
      case 'url': return 'URL';
    }
  };

  if (!isOpen) return null;

  return (
    <div className="modal-overlay">
      <div className="modal-content m3u-filters-modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <div className="header-info">
            <h2>Manage Filters</h2>
            <span className="account-name">{account.name}</span>
          </div>
          <button className="close-btn" onClick={onClose}>
            &times;
          </button>
        </div>

        <div className="modal-toolbar">
          <p className="filter-help">
            Filters control which streams are imported from this M3U source.
          </p>
          <button className="btn-primary btn-small" onClick={handleAddNew} disabled={editingFilter !== null}>
            <span className="material-icons">add</span>
            Add Filter
          </button>
        </div>

        <div className="modal-body">
          {loading ? (
            <div className="loading-state">
              <span className="material-icons spinning">sync</span>
              <p>Loading filters...</p>
            </div>
          ) : (
            <>
              {/* Filter Form */}
              {editingFilter && (
                <div className="filter-form">
                  <h3>{isAddingNew ? 'Add Filter' : 'Edit Filter'}</h3>

                  <div className="form-row">
                    <div className="form-group">
                      <label>Filter Type</label>
                      <select
                        value={editingFilter.filter_type}
                        onChange={(e) => setEditingFilter({
                          ...editingFilter,
                          filter_type: e.target.value as FilterType,
                        })}
                      >
                        <option value="group">Group Name</option>
                        <option value="name">Stream Name</option>
                        <option value="url">Stream URL</option>
                      </select>
                    </div>

                    <div className="form-group">
                      <label>Action</label>
                      <select
                        value={editingFilter.exclude ? 'exclude' : 'include'}
                        onChange={(e) => setEditingFilter({
                          ...editingFilter,
                          exclude: e.target.value === 'exclude',
                        })}
                      >
                        <option value="exclude">Exclude matches</option>
                        <option value="include">Include only matches</option>
                      </select>
                    </div>

                    <div className="form-group">
                      <label>Order</label>
                      <input
                        type="number"
                        min="1"
                        value={editingFilter.order}
                        onChange={(e) => setEditingFilter({
                          ...editingFilter,
                          order: parseInt(e.target.value) || 1,
                        })}
                      />
                    </div>
                  </div>

                  <div className="form-group">
                    <label>Regex Pattern</label>
                    <input
                      type="text"
                      placeholder="e.g., Adult.* or ^PPV"
                      value={editingFilter.regex_pattern}
                      onChange={(e) => setEditingFilter({
                        ...editingFilter,
                        regex_pattern: e.target.value,
                      })}
                    />
                    <span className="form-hint">
                      Use regex patterns. Examples: "Adult.*" matches anything starting with "Adult",
                      "^PPV" matches strings starting with "PPV", "ESPN" matches anything containing "ESPN"
                    </span>
                  </div>

                  <div className="form-actions">
                    <button className="btn-secondary" onClick={handleCancelEdit} disabled={saving}>
                      Cancel
                    </button>
                    <button className="btn-primary" onClick={handleSaveFilter} disabled={saving}>
                      {saving ? 'Saving...' : isAddingNew ? 'Create Filter' : 'Save Changes'}
                    </button>
                  </div>
                </div>
              )}

              {/* Filters List */}
              {filters.length === 0 && !editingFilter ? (
                <div className="empty-state">
                  <span className="material-icons">filter_alt_off</span>
                  <p>No filters configured for this account.</p>
                  <p className="hint">Add a filter to control which streams are imported.</p>
                </div>
              ) : filters.length > 0 && (
                <div className="filters-list">
                  <div className="filters-header">
                    <span className="col-type">Type</span>
                    <span className="col-pattern">Pattern</span>
                    <span className="col-action">Action</span>
                    <span className="col-order">Order</span>
                    <span className="col-actions">Actions</span>
                  </div>
                  {filters.sort((a, b) => a.order - b.order).map(filter => (
                    <div key={filter.id} className="filter-row">
                      <div className="filter-type">
                        <span className={`type-badge ${filter.filter_type}`}>
                          {getFilterTypeLabel(filter.filter_type)}
                        </span>
                      </div>
                      <div className="filter-pattern" title={filter.regex_pattern}>
                        <code>{filter.regex_pattern}</code>
                      </div>
                      <div className="filter-action">
                        <span className={`action-badge ${filter.exclude ? 'exclude' : 'include'}`}>
                          {filter.exclude ? 'Exclude' : 'Include'}
                        </span>
                      </div>
                      <div className="filter-order">
                        {filter.order}
                      </div>
                      <div className="filter-actions">
                        <button
                          className="action-btn"
                          onClick={() => handleEdit(filter)}
                          title="Edit"
                          disabled={editingFilter !== null}
                        >
                          <span className="material-icons">edit</span>
                        </button>
                        <button
                          className="action-btn delete"
                          onClick={() => handleDelete(filter)}
                          title="Delete"
                          disabled={editingFilter !== null}
                        >
                          <span className="material-icons">delete</span>
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </>
          )}

          {error && <div className="error-message">{error}</div>}
        </div>

        <div className="modal-footer">
          <button className="btn-secondary" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
