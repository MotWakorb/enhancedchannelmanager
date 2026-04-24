/**
 * LookupTableSection
 *
 * Lookup-table management for the dummy EPG template engine's `{key|lookup:<name>}`
 * pipe. Lists all saved tables, supports create/edit/delete, and offers a
 * bulk `key=value` import on the editor modal.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';

import * as api from '../../services/api';
import type { LookupTable, LookupTableSummary } from '../../services/api';
import { useNotifications } from '../../contexts/NotificationContext';
import { ModalOverlay } from '../ModalOverlay';
import { logger } from '../../utils/logger';

import '../ModalBase.css';
import './LookupTableSection.css';

interface EntryRow {
  key: string;
  value: string;
}

function entriesToRows(entries: Record<string, string>): EntryRow[] {
  return Object.entries(entries).map(([key, value]) => ({ key, value }));
}

function rowsToEntries(rows: EntryRow[]): Record<string, string> {
  const out: Record<string, string> = {};
  for (const { key, value } of rows) {
    const trimmed = key.trim();
    if (trimmed) out[trimmed] = value;
  }
  return out;
}

interface EditorModalProps {
  initial: LookupTable | null; // null → create mode
  onClose: () => void;
  onSaved: () => void;
}

function LookupTableEditorModal({ initial, onClose, onSaved }: EditorModalProps) {
  const notifications = useNotifications();
  const [name, setName] = useState(initial?.name ?? '');
  const [description, setDescription] = useState(initial?.description ?? '');
  const [rows, setRows] = useState<EntryRow[]>(() =>
    initial ? entriesToRows(initial.entries) : [],
  );
  const [bulkInput, setBulkInput] = useState('');
  const [saving, setSaving] = useState(false);

  const isEdit = initial !== null;

  const addRow = useCallback(() => {
    setRows((prev) => [...prev, { key: '', value: '' }]);
  }, []);

  const removeRow = useCallback((idx: number) => {
    setRows((prev) => prev.filter((_, i) => i !== idx));
  }, []);

  const updateRow = useCallback((idx: number, field: 'key' | 'value', next: string) => {
    setRows((prev) =>
      prev.map((row, i) => (i === idx ? { ...row, [field]: next } : row)),
    );
  }, []);

  const handleBulkImport = useCallback(() => {
    const lines = bulkInput.split('\n').map((l) => l.trim()).filter(Boolean);
    if (lines.length === 0) return;

    const additions: EntryRow[] = [];
    let skipped = 0;
    for (const line of lines) {
      const eq = line.indexOf('=');
      if (eq === -1) {
        skipped += 1;
        continue;
      }
      const key = line.slice(0, eq).trim();
      const value = line.slice(eq + 1).trim();
      if (!key) {
        skipped += 1;
        continue;
      }
      additions.push({ key, value });
    }

    if (additions.length === 0) {
      notifications.error('No valid key=value lines found', 'Bulk Import');
      return;
    }

    setRows((prev) => [...prev, ...additions]);
    setBulkInput('');
    if (skipped > 0) {
      notifications.info(`Imported ${additions.length} entries (${skipped} skipped — missing '=')`, 'Bulk Import');
    } else {
      notifications.success(`Imported ${additions.length} entries`);
    }
  }, [bulkInput, notifications]);

  const handleSave = async () => {
    const trimmedName = name.trim();
    if (!trimmedName) {
      notifications.error('Name is required');
      return;
    }

    const entries = rowsToEntries(rows);
    setSaving(true);
    try {
      if (isEdit && initial) {
        await api.updateLookupTable(initial.id, {
          name: trimmedName,
          description: description || undefined,
          entries,
        });
        notifications.success(`Updated "${trimmedName}"`);
      } else {
        await api.createLookupTable({
          name: trimmedName,
          description: description || undefined,
          entries,
        });
        notifications.success(`Created "${trimmedName}"`);
      }
      onSaved();
      onClose();
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Save failed';
      notifications.error(message, 'Save Failed');
      logger.error('Lookup table save failed', err);
    } finally {
      setSaving(false);
    }
  };

  return (
    <ModalOverlay onClose={onClose}>
      <div className="lookup-table-editor modal-container modal-md">
        <div className="modal-header">
          <h2>{isEdit ? 'Edit Lookup Table' : 'New Lookup Table'}</h2>
          <button className="modal-close-btn" onClick={onClose} aria-label="Close">
            <span className="material-icons">close</span>
          </button>
        </div>

        <div className="modal-body">
          <div className="modal-form-group">
            <label htmlFor="lookup-name">Name</label>
            <input
              id="lookup-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. callsigns"
              autoFocus
            />
            <span className="form-hint">Used in templates as <code>{'{key|lookup:<name>}'}</code>.</span>
          </div>

          <div className="modal-form-group">
            <label htmlFor="lookup-description">Description (optional)</label>
            <input
              id="lookup-description"
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What this table maps"
            />
          </div>

          <div className="lookup-entries">
            <div className="lookup-entries-header">
              <span>Entries</span>
              <button type="button" className="btn-secondary btn-small" onClick={addRow}>
                <span className="material-icons">add</span> Add Row
              </button>
            </div>
            {rows.length === 0 ? (
              <p className="lookup-entries-empty">No entries yet. Add a row or paste below.</p>
            ) : (
              <div className="lookup-entries-list">
                {rows.map((row, idx) => (
                  <div className="lookup-entry-row" key={idx}>
                    <input
                      type="text"
                      value={row.key}
                      onChange={(e) => updateRow(idx, 'key', e.target.value)}
                      placeholder="key"
                      aria-label="key"
                    />
                    <span className="lookup-entry-arrow">→</span>
                    <input
                      type="text"
                      value={row.value}
                      onChange={(e) => updateRow(idx, 'value', e.target.value)}
                      placeholder="value"
                      aria-label="value"
                    />
                    <button
                      type="button"
                      className="lookup-entry-remove"
                      onClick={() => removeRow(idx)}
                      aria-label={`Remove ${row.key || 'row'}`}
                    >
                      <span className="material-icons">close</span>
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>

          <details className="lookup-bulk">
            <summary>Bulk import (one <code>key=value</code> per line)</summary>
            <textarea
              value={bulkInput}
              onChange={(e) => setBulkInput(e.target.value)}
              placeholder={'ESPN=espn.com\nCNN=cnn.com'}
              rows={5}
            />
            <button type="button" className="btn-secondary btn-small" onClick={handleBulkImport}>
              Append
            </button>
          </details>
        </div>

        <div className="modal-footer">
          <button className="modal-btn btn-secondary" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button
            className="modal-btn modal-btn-primary btn-primary"
            onClick={handleSave}
            disabled={saving}
          >
            {saving ? 'Saving...' : isEdit ? 'Save Changes' : 'Create'}
          </button>
        </div>
      </div>
    </ModalOverlay>
  );
}

export function LookupTableSection() {
  const notifications = useNotifications();
  const [tables, setTables] = useState<LookupTableSummary[]>([]);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [editingTable, setEditingTable] = useState<LookupTable | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const list = await api.listLookupTables();
      setTables(list);
    } catch (err) {
      logger.error('Failed to load lookup tables', err);
      notifications.error('Failed to load lookup tables');
    } finally {
      setLoading(false);
    }
  }, [notifications]);

  useEffect(() => {
    load();
  }, [load]);

  const openEditor = useCallback(async (id: number) => {
    try {
      const full = await api.getLookupTable(id);
      setEditingTable(full);
    } catch (err) {
      logger.error('Failed to load lookup table', err);
      notifications.error('Failed to load lookup table');
    }
  }, [notifications]);

  const handleDelete = useCallback(async (table: LookupTableSummary) => {
    if (!window.confirm(`Delete lookup table "${table.name}"? This cannot be undone.`)) return;
    try {
      await api.deleteLookupTable(table.id);
      notifications.success(`Deleted "${table.name}"`);
      load();
    } catch (err) {
      logger.error('Failed to delete lookup table', err);
      notifications.error('Failed to delete lookup table');
    }
  }, [load, notifications]);

  const sortedTables = useMemo(
    () => [...tables].sort((a, b) => a.name.localeCompare(b.name)),
    [tables],
  );

  return (
    <div className="settings-page">
      <div className="settings-page-header">
        <h2>Lookup Tables</h2>
        <p>
          Named <code>key → value</code> tables used by the dummy EPG template engine's
          {' '}<code>{'{key|lookup:<name>}'}</code> pipe.
        </p>
      </div>

      <div className="settings-section">
        <div className="settings-section-header">
          <span className="material-icons">table_view</span>
          <h3>Tables</h3>
          <button className="btn-primary btn-small" onClick={() => setCreating(true)}>
            <span className="material-icons">add</span> New Table
          </button>
        </div>

        {loading ? (
          <p className="lookup-tables-empty">Loading...</p>
        ) : sortedTables.length === 0 ? (
          <p className="lookup-tables-empty">
            No lookup tables yet. Create one to map channel callsigns to URLs,
            country codes to names, or any other key/value substitution your
            templates need.
          </p>
        ) : (
          <table className="lookup-tables-list" role="grid">
            <thead>
              <tr>
                <th>Name</th>
                <th>Description</th>
                <th className="num">Entries</th>
                <th className="actions" aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {sortedTables.map((table) => (
                <tr key={table.id}>
                  <td className="lookup-tables-name">{table.name}</td>
                  <td className="lookup-tables-desc">{table.description || '—'}</td>
                  <td className="num">{table.entry_count}</td>
                  <td className="actions">
                    <button
                      className="btn-icon"
                      onClick={() => openEditor(table.id)}
                      aria-label={`Edit ${table.name}`}
                      title="Edit"
                    >
                      <span className="material-icons">edit</span>
                    </button>
                    <button
                      className="btn-icon btn-icon-danger"
                      onClick={() => handleDelete(table)}
                      aria-label={`Delete ${table.name}`}
                      title="Delete"
                    >
                      <span className="material-icons">delete</span>
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {creating && (
        <LookupTableEditorModal
          initial={null}
          onClose={() => setCreating(false)}
          onSaved={load}
        />
      )}
      {editingTable && (
        <LookupTableEditorModal
          initial={editingTable}
          onClose={() => setEditingTable(null)}
          onSaved={load}
        />
      )}
    </div>
  );
}
