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
import { DummyEPGSourceModal } from '../DummyEPGSourceModal';
import './EPGManagerTab.css';

interface SortableEPGSourceRowProps {
  source: EPGSource;
  onEdit: (source: EPGSource) => void;
  onDelete: (source: EPGSource) => void;
  onRefresh: (source: EPGSource) => void;
  onToggleActive: (source: EPGSource) => void;
  hideEpgUrls?: boolean;
}

function SortableEPGSourceRow({ source, onEdit, onDelete, onRefresh, onToggleActive, hideEpgUrls = false }: SortableEPGSourceRowProps) {
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
      case 'fetching': return 'cloud_download';
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

  const getStatusLabel = (status: EPGSource['status']) => {
    switch (status) {
      case 'success': return 'Ready';
      case 'error': return 'Error';
      case 'fetching': return 'Downloading...';
      case 'parsing': return 'Processing...';
      case 'disabled': return 'Disabled';
      default: return 'Idle';
    }
  };

  // Check if source is actively being refreshed
  const isRefreshing = source.status === 'fetching' || source.status === 'parsing';

  // Parse program count from last_message (e.g., "Parsed 96,514 programs for 278 channels")
  const getProgramCount = (message: string | null): string | null => {
    if (!message) return null;
    const match = message.match(/Parsed ([\d,]+) programs/i);
    return match ? match[1] : null;
  };

  // Format number with commas (e.g., 51589 -> "51,589")
  const formatNumber = (value: string | number): string => {
    const num = typeof value === 'string' ? parseInt(value.replace(/,/g, ''), 10) : value;
    return isNaN(num) ? String(value) : num.toLocaleString();
  };

  const programCount = getProgramCount(source.last_message);

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

      <div className={`source-status ${getStatusClass(source.status)}`} title={source.last_message || ''}>
        <span className={`material-icons ${isRefreshing ? 'spinning' : ''}`}>
          {getStatusIcon(source.status)}
        </span>
        <span className="status-label">{getStatusLabel(source.status)}</span>
        {isRefreshing && (
          <span className="status-hint">See Dispatcharr for progress</span>
        )}
      </div>

      <div className="source-info">
        <div className="source-name">{source.name}</div>
        <div className="source-details">
          <span className="source-type">{getSourceTypeLabel(source.source_type)}</span>
          {source.url && !hideEpgUrls && <span className="source-url" title={source.url}>{source.url}</span>}
        </div>
        {source.last_message && source.status === 'error' && (
          <div className="source-message" title={source.last_message}>
            {source.last_message}
          </div>
        )}
      </div>

      <div className="source-stats">
        <span className="epg-count">{formatNumber(source.epg_data_count)} channels</span>
        {programCount && <span className="program-count">{programCount} programs</span>}
        {!programCount && <span className="refresh-interval">{source.refresh_interval}h refresh</span>}
      </div>

      <div className="source-updated">
        <span className="updated-label">Updated:</span>
        <span className="updated-time">{formatDate(source.updated_at)}</span>
      </div>

      <div className="source-actions">
        {source.url && !hideEpgUrls && (
          <button
            className="action-btn"
            onClick={() => {
              navigator.clipboard.writeText(source.url!);
            }}
            title="Copy URL"
          >
            <span className="material-icons">content_copy</span>
          </button>
        )}
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
    <div className="modal-overlay">
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

interface EPGManagerTabProps {
  onSourcesChange?: () => void;
  hideEpgUrls?: boolean;
}

export function EPGManagerTab({ onSourcesChange, hideEpgUrls = false }: EPGManagerTabProps) {
  const [sources, setSources] = useState<EPGSource[]>([]);
  const [dummySources, setDummySources] = useState<EPGSource[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingSource, setEditingSource] = useState<EPGSource | null>(null);
  const [refreshingAll, setRefreshingAll] = useState(false);
  // Dummy EPG modal state
  const [dummyModalOpen, setDummyModalOpen] = useState(false);
  const [editingDummySource, setEditingDummySource] = useState<EPGSource | null>(null);

  const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  );

  const loadSources = useCallback(async () => {
    try {
      const data = await api.getEPGSources();
      // Separate standard and dummy EPG sources, sort by priority (descending)
      const standardSources = data
        .filter(s => s.source_type !== 'dummy')
        .sort((a, b) => b.priority - a.priority);
      const dummyEpgSources = data
        .filter(s => s.source_type === 'dummy')
        .sort((a, b) => a.name.localeCompare(b.name));
      setSources(standardSources);
      setDummySources(dummyEpgSources);
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

  const handleEditSource = async (source: EPGSource) => {
    try {
      // Fetch full source details to ensure we have all properties
      const fullSource = await api.getEPGSource(source.id);
      setEditingSource(fullSource);
      setModalOpen(true);
    } catch (err) {
      setError('Failed to load EPG source details');
    }
  };

  const handleDeleteSource = async (source: EPGSource) => {
    if (!confirm(`Are you sure you want to delete "${source.name}"?`)) {
      return;
    }

    try {
      await api.deleteEPGSource(source.id);
      await loadSources();
      onSourcesChange?.();
    } catch (err) {
      setError('Failed to delete EPG source');
    }
  };

  const handleRefreshSource = async (source: EPGSource) => {
    try {
      await api.refreshEPGSource(source.id);
      // Immediately show we're refreshing by updating local state
      setSources(prev => prev.map(s =>
        s.id === source.id ? { ...s, status: 'fetching' } : s
      ));
      // Start polling for status updates every 2 seconds
      const pollInterval = setInterval(async () => {
        const updatedSources = await api.getEPGSources();
        const updatedSource = updatedSources.find(s => s.id === source.id);
        setSources(updatedSources.filter(s => s.source_type !== 'dummy').sort((a, b) => b.priority - a.priority));
        // Stop polling when refresh completes
        if (updatedSource && (updatedSource.status === 'success' || updatedSource.status === 'error')) {
          clearInterval(pollInterval);
        }
      }, 2000);
      // Stop polling after 5 minutes max
      setTimeout(() => clearInterval(pollInterval), 300000);
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
    onSourcesChange?.();
  };

  // Dummy EPG handlers
  const handleAddDummySource = () => {
    setEditingDummySource(null);
    setDummyModalOpen(true);
  };

  const handleEditDummySource = async (source: EPGSource) => {
    try {
      // Fetch full source details to get custom_properties
      const fullSource = await api.getEPGSource(source.id);
      setEditingDummySource(fullSource);
      setDummyModalOpen(true);
    } catch (err) {
      setError('Failed to load EPG source details');
    }
  };

  const handleDeleteDummySource = async (source: EPGSource) => {
    if (!confirm(`Are you sure you want to delete "${source.name}"?`)) {
      return;
    }

    try {
      await api.deleteEPGSource(source.id);
      await loadSources();
      onSourcesChange?.();
    } catch (err) {
      setError('Failed to delete dummy EPG source');
    }
  };

  const handleToggleDummyActive = async (source: EPGSource) => {
    try {
      await api.updateEPGSource(source.id, { is_active: !source.is_active });
      await loadSources();
    } catch (err) {
      setError('Failed to update dummy EPG source');
    }
  };

  const handleSaveDummySource = async (data: api.CreateEPGSourceRequest) => {
    if (editingDummySource) {
      await api.updateEPGSource(editingDummySource.id, data);
    } else {
      await api.createEPGSource(data);
    }
    await loadSources();
    onSourcesChange?.();
  };

  const handleRefreshAll = async () => {
    console.log('[EPGManagerTab] handleRefreshAll called!');
    setRefreshingAll(true);
    try {
      console.log('[EPGManagerTab] Triggering EPG import...');
      await api.triggerEPGImport();
      console.log('[EPGManagerTab] EPG import triggered successfully');
      // Mark all active non-dummy sources as fetching
      setSources(prev => prev.map(s =>
        s.is_active && s.source_type !== 'dummy' ? { ...s, status: 'fetching' } : s
      ));
      // Start polling for status updates every 2 seconds
      const pollInterval = setInterval(async () => {
        const updatedSources = await api.getEPGSources();
        const standardSources = updatedSources.filter(s => s.source_type !== 'dummy').sort((a, b) => b.priority - a.priority);
        setSources(standardSources);
        // Stop polling when all sources are done (no fetching/parsing)
        const stillRefreshing = standardSources.some(s => s.status === 'fetching' || s.status === 'parsing');
        if (!stillRefreshing) {
          clearInterval(pollInterval);
          setRefreshingAll(false);
        }
      }, 2000);
      // Stop polling after 10 minutes max
      setTimeout(() => {
        clearInterval(pollInterval);
        setRefreshingAll(false);
      }, 600000);
    } catch (err) {
      console.error('[EPGManagerTab] Failed to trigger EPG import:', err);
      setError('Failed to trigger EPG import');
      setRefreshingAll(false);
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
          <button className="btn-secondary" onClick={handleRefreshAll} disabled={refreshingAll}>
            <span className={`material-icons ${refreshingAll ? 'spinning' : ''}`}>sync</span>
            {refreshingAll ? 'Refreshing...' : 'Refresh All'}
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
            <span className="col-priority" title="Higher priority number = matches first for EPG channel matching">Priority</span>
            <span className="col-status" title="Current refresh status. Idle and Ready are normal states.">Status</span>
            <span className="col-info" title="EPG source name, type, and URL">Source</span>
            <span className="col-stats" title="Number of channels matched to this EPG and total programs parsed">Stats</span>
            <span className="col-updated" title="Last time this EPG source was refreshed">Last Updated</span>
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
                  hideEpgUrls={hideEpgUrls}
                />
              ))}
            </SortableContext>
          </DndContext>
        </div>
      )}

      {/* Dummy EPG Sources Section */}
      <div className="dummy-epg-section">
        <div className="epg-header">
          <div className="header-title">
            <h2>Dummy EPG Sources</h2>
            <p className="header-description">
              Pattern-based EPG sources that generate programs from channel/stream names.
            </p>
          </div>
          <div className="header-actions">
            <button className="btn-primary" onClick={handleAddDummySource}>
              <span className="material-icons">add</span>
              Add Dummy EPG
            </button>
          </div>
        </div>

        {dummySources.length === 0 ? (
          <div className="dummy-empty-state">
            <span className="material-icons">auto_fix_high</span>
            <p>No dummy EPG sources. Create one to generate EPG data from channel names using regex patterns.</p>
          </div>
        ) : (
          <div className="dummy-sources-list">
            {dummySources.map((source) => (
              <div key={source.id} className={`dummy-source-row ${!source.is_active ? 'inactive' : ''}`}>
                <div className={`dummy-status ${source.is_active ? 'active' : 'disabled'}`}>
                  <span className="material-icons">
                    {source.is_active ? 'check_circle' : 'block'}
                  </span>
                </div>
                <div className="dummy-info">
                  <div className="dummy-name">{source.name}</div>
                  <div className="dummy-details">
                    <span className="dummy-type">Dummy</span>
                    <span className="dummy-pattern">Pattern-based generation</span>
                  </div>
                </div>
                <div className="dummy-actions">
                  <button
                    className="action-btn"
                    onClick={() => handleToggleDummyActive(source)}
                    title={source.is_active ? 'Disable' : 'Enable'}
                  >
                    <span className="material-icons">
                      {source.is_active ? 'toggle_on' : 'toggle_off'}
                    </span>
                  </button>
                  <button
                    className="action-btn"
                    onClick={() => handleEditDummySource(source)}
                    title="Edit"
                  >
                    <span className="material-icons">edit</span>
                  </button>
                  <button
                    className="action-btn delete"
                    onClick={() => handleDeleteDummySource(source)}
                    title="Delete"
                  >
                    <span className="material-icons">delete</span>
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <EPGSourceModal
        isOpen={modalOpen}
        source={editingSource}
        onClose={() => setModalOpen(false)}
        onSave={handleSaveSource}
      />

      <DummyEPGSourceModal
        isOpen={dummyModalOpen}
        source={editingDummySource}
        onClose={() => setDummyModalOpen(false)}
        onSave={handleSaveDummySource}
      />
    </div>
  );
}
