import { useState, useEffect, useCallback } from 'react';
import {
  DndContext,
  closestCenter,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core';
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import type { EPGSource, EPGSourceType } from '../../types';
import * as api from '../../services/api';
import './EPGManagerTab.css';

interface SortableEPGSourceRowProps {
  source: EPGSource;
  onEdit: (source: EPGSource) => void;
  onDelete: (source: EPGSource) => void;
  onRefresh: (source: EPGSource) => void;
  onToggleActive: (source: EPGSource) => void;
}

function SortableEPGSourceRow({ source, onEdit, onDelete, onRefresh, onToggleActive }: SortableEPGSourceRowProps) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: source.id });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
    opacity: isDragging ? 0.5 : 1,
  };

  const getStatusIcon = (status: EPGSource['status']) => {
    switch (status) {
      case 'success': return 'check_circle';
      case 'error': return 'error';
      case 'fetching': return 'sync';
      case 'parsing': return 'hourglass_empty';
      case 'disabled': return 'block';
      default: return 'schedule';
    }
  };

  const getStatusClass = (status: EPGSource['status']) => {
    switch (status) {
      case 'success': return 'status-success';
      case 'error': return 'status-error';
      case 'fetching':
      case 'parsing': return 'status-pending';
      case 'disabled': return 'status-disabled';
      default: return 'status-idle';
    }
  };

  const getSourceTypeLabel = (type: EPGSourceType) => {
    switch (type) {
      case 'xmltv': return 'XMLTV';
      case 'schedules_direct': return 'Schedules Direct';
      case 'dummy': return 'Dummy';
    }
  };

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return 'Never';
    const date = new Date(dateStr);
    return date.toLocaleString();
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={`epg-source-row ${!source.is_active ? 'inactive' : ''}`}
    >
      <div className="drag-handle" {...attributes} {...listeners}>
        <span className="material-icons">drag_indicator</span>
      </div>

      <div className="source-priority">
        {source.priority}
      </div>

      <div className={`source-status ${getStatusClass(source.status)}`}>
        <span className="material-icons">{getStatusIcon(source.status)}</span>
      </div>

      <div className="source-info">
        <div className="source-name">{source.name}</div>
        <div className="source-details">
          <span className="source-type">{getSourceTypeLabel(source.source_type)}</span>
          {source.url && <span className="source-url" title={source.url}>{source.url}</span>}
        </div>
      </div>

      <div className="source-stats">
        <span className="epg-count">{source.epg_data_count} channels</span>
        <span className="refresh-interval">{source.refresh_interval}h refresh</span>
      </div>

      <div className="source-updated">
        <span className="updated-label">Updated:</span>
        <span className="updated-time">{formatDate(source.updated_at)}</span>
      </div>

      <div className="source-actions">
        <button
          className="action-btn"
          onClick={() => onToggleActive(source)}
          title={source.is_active ? 'Disable' : 'Enable'}
        >
          <span className="material-icons">
            {source.is_active ? 'toggle_on' : 'toggle_off'}
          </span>
        </button>
        <button
          className="action-btn"
          onClick={() => onRefresh(source)}
          title="Refresh"
          disabled={!source.is_active || source.status === 'fetching' || source.status === 'parsing'}
        >
          <span className="material-icons">refresh</span>
        </button>
        <button
          className="action-btn"
          onClick={() => onEdit(source)}
          title="Edit"
        >
          <span className="material-icons">edit</span>
        </button>
        <button
          className="action-btn delete"
          onClick={() => onDelete(source)}
          title="Delete"
        >
          <span className="material-icons">delete</span>
        </button>
      </div>
    </div>
  );
}

interface EPGSourceModalProps {
  isOpen: boolean;
  source: EPGSource | null;
  onClose: () => void;
  onSave: (data: api.CreateEPGSourceRequest) => Promise<void>;
}

function EPGSourceModal({ isOpen, source, onClose, onSave }: EPGSourceModalProps) {
  const [name, setName] = useState('');
  const [sourceType, setSourceType] = useState<EPGSourceType>('xmltv');
  const [url, setUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [refreshInterval, setRefreshInterval] = useState(24);
  const [priority, setPriority] = useState(0);
  const [isActive, setIsActive] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (source) {
      setName(source.name);
      setSourceType(source.source_type);
      setUrl(source.url || '');
      setApiKey(source.api_key || '');
      setRefreshInterval(source.refresh_interval);
      setPriority(source.priority);
      setIsActive(source.is_active);
    } else {
      setName('');
      setSourceType('xmltv');
      setUrl('');
      setApiKey('');
      setRefreshInterval(24);
      setPriority(0);
      setIsActive(true);
    }
    setError(null);
  }, [source, isOpen]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (!name.trim()) {
      setError('Name is required');
      return;
    }

    if (sourceType === 'xmltv' && !url.trim()) {
      setError('URL is required for XMLTV sources');
      return;
    }

    setSaving(true);
    try {
      await onSave({
        name: name.trim(),
        source_type: sourceType,
        url: sourceType === 'xmltv' ? url.trim() : null,
        api_key: sourceType === 'schedules_direct' ? apiKey.trim() : null,
        refresh_interval: refreshInterval,
        priority,
        is_active: isActive,
      });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save EPG source');
    } finally {
      setSaving(false);
    }
  };

  if (!isOpen) return null;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="epg-modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>{source ? 'Edit EPG Source' : 'Add Standard EPG'}</h2>
          <button className="close-btn" onClick={onClose}>&times;</button>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            <div className="form-group">
              <label htmlFor="name">Name</label>
              <input
                id="name"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="My EPG Source"
                autoFocus
              />
            </div>

            <div className="form-group">
              <label htmlFor="sourceType">Source Type</label>
              <select
                id="sourceType"
                value={sourceType}
                onChange={(e) => setSourceType(e.target.value as EPGSourceType)}
                disabled={!!source}
              >
                <option value="xmltv">XMLTV (URL)</option>
                <option value="schedules_direct">Schedules Direct</option>
              </select>
              {source && (
                <p className="form-hint">Source type cannot be changed after creation</p>
              )}
            </div>

            {sourceType === 'xmltv' && (
              <div className="form-group">
                <label htmlFor="url">XMLTV URL</label>
                <input
                  id="url"
                  type="url"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder="https://example.com/epg.xml"
                />
              </div>
            )}

            {sourceType === 'schedules_direct' && (
              <div className="form-group">
                <label htmlFor="apiKey">API Key</label>
                <input
                  id="apiKey"
                  type="password"
                  value={apiKey}
                  onChange={(e) => setApiKey(e.target.value)}
                  placeholder="Your Schedules Direct API key"
                />
              </div>
            )}

            <div className="form-row">
              <div className="form-group">
                <label htmlFor="refreshInterval">Refresh Interval (hours)</label>
                <input
                  id="refreshInterval"
                  type="number"
                  min="1"
                  max="168"
                  value={refreshInterval}
                  onChange={(e) => setRefreshInterval(parseInt(e.target.value) || 24)}
                />
              </div>

              <div className="form-group">
                <label htmlFor="priority">Priority</label>
                <input
                  id="priority"
                  type="number"
                  min="0"
                  value={priority}
                  onChange={(e) => setPriority(parseInt(e.target.value) || 0)}
                />
                <p className="form-hint">Higher = more important</p>
              </div>
            </div>

            <div className="form-group checkbox-group">
              <label className="checkbox-label">
                <input
                  type="checkbox"
                  checked={isActive}
                  onChange={(e) => setIsActive(e.target.checked)}
                />
                <span>Active</span>
              </label>
            </div>

            {error && <div className="error-message">{error}</div>}
          </div>

          <div className="modal-footer">
            <button type="button" className="btn-secondary" onClick={onClose} disabled={saving}>
              Cancel
            </button>
            <button type="submit" className="btn-primary" disabled={saving}>
              {saving ? 'Saving...' : source ? 'Save Changes' : 'Add EPG'}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

export function EPGManagerTab() {
  const [sources, setSources] = useState<EPGSource[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingSource, setEditingSource] = useState<EPGSource | null>(null);

  const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  );

  const loadSources = useCallback(async () => {
    try {
      const data = await api.getEPGSources();
      // Filter out dummy EPG sources and sort by priority (descending)
      const standardSources = data
        .filter(s => s.source_type !== 'dummy')
        .sort((a, b) => b.priority - a.priority);
      setSources(standardSources);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load EPG sources');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadSources();
  }, [loadSources]);

  const handleDragEnd = async (event: DragEndEvent) => {
    const { active, over } = event;

    if (over && active.id !== over.id) {
      const oldIndex = sources.findIndex((s) => s.id === active.id);
      const newIndex = sources.findIndex((s) => s.id === over.id);

      const newOrder = arrayMove(sources, oldIndex, newIndex);
      setSources(newOrder);

      // Update priorities based on new order (higher index = higher priority)
      // Reverse the order so first item has highest priority
      const updates = newOrder.map((source, index) => ({
        id: source.id,
        priority: newOrder.length - index,
      }));

      try {
        await Promise.all(
          updates.map(({ id, priority }) => api.updateEPGSource(id, { priority }))
        );
        await loadSources();
      } catch (err) {
        setError('Failed to update priorities');
        await loadSources();
      }
    }
  };

  const handleAddSource = () => {
    setEditingSource(null);
    setModalOpen(true);
  };

  const handleEditSource = (source: EPGSource) => {
    setEditingSource(source);
    setModalOpen(true);
  };

  const handleDeleteSource = async (source: EPGSource) => {
    if (!confirm(`Are you sure you want to delete "${source.name}"?`)) {
      return;
    }

    try {
      await api.deleteEPGSource(source.id);
      await loadSources();
    } catch (err) {
      setError('Failed to delete EPG source');
    }
  };

  const handleRefreshSource = async (source: EPGSource) => {
    try {
      await api.refreshEPGSource(source.id);
      // Start polling for status updates
      setTimeout(loadSources, 2000);
    } catch (err) {
      setError('Failed to refresh EPG source');
    }
  };

  const handleToggleActive = async (source: EPGSource) => {
    try {
      await api.updateEPGSource(source.id, { is_active: !source.is_active });
      await loadSources();
    } catch (err) {
      setError('Failed to update EPG source');
    }
  };

  const handleSaveSource = async (data: api.CreateEPGSourceRequest) => {
    if (editingSource) {
      await api.updateEPGSource(editingSource.id, data);
    } else {
      await api.createEPGSource(data);
    }
    await loadSources();
  };

  const handleRefreshAll = async () => {
    try {
      await api.triggerEPGImport();
      setTimeout(loadSources, 2000);
    } catch (err) {
      setError('Failed to trigger EPG import');
    }
  };

  if (loading) {
    return (
      <div className="epg-manager-tab">
        <div className="loading-state">
          <span className="material-icons spinning">sync</span>
          <p>Loading EPG sources...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="epg-manager-tab">
      <div className="epg-header">
        <div className="header-title">
          <h2>EPG Sources</h2>
          <p className="header-description">
            Manage your Electronic Program Guide sources. Drag to reorder priority.
          </p>
        </div>
        <div className="header-actions">
          <button className="btn-secondary" onClick={handleRefreshAll}>
            <span className="material-icons">sync</span>
            Refresh All
          </button>
          <button className="btn-primary" onClick={handleAddSource}>
            <span className="material-icons">add</span>
            Add Standard EPG
          </button>
        </div>
      </div>

      {error && (
        <div className="error-banner">
          <span className="material-icons">error</span>
          {error}
          <button onClick={() => setError(null)}>&times;</button>
        </div>
      )}

      {sources.length === 0 ? (
        <div className="empty-state">
          <span className="material-icons">schedule</span>
          <h3>No EPG Sources</h3>
          <p>Add an EPG source to get program guide data for your channels.</p>
          <button className="btn-primary" onClick={handleAddSource}>
            <span className="material-icons">add</span>
            Add Standard EPG
          </button>
        </div>
      ) : (
        <div className="epg-sources-list">
          <div className="list-header">
            <span className="col-drag"></span>
            <span className="col-priority">Priority</span>
            <span className="col-status">Status</span>
            <span className="col-info">Source</span>
            <span className="col-stats">Stats</span>
            <span className="col-updated">Last Updated</span>
            <span className="col-actions">Actions</span>
          </div>

          <DndContext
            sensors={sensors}
            collisionDetection={closestCenter}
            onDragEnd={handleDragEnd}
          >
            <SortableContext
              items={sources.map((s) => s.id)}
              strategy={verticalListSortingStrategy}
            >
              {sources.map((source) => (
                <SortableEPGSourceRow
                  key={source.id}
                  source={source}
                  onEdit={handleEditSource}
                  onDelete={handleDeleteSource}
                  onRefresh={handleRefreshSource}
                  onToggleActive={handleToggleActive}
                />
              ))}
            </SortableContext>
          </DndContext>
        </div>
      )}

      <EPGSourceModal
        isOpen={modalOpen}
        source={editingSource}
        onClose={() => setModalOpen(false)}
        onSave={handleSaveSource}
      />
    </div>
  );
}
