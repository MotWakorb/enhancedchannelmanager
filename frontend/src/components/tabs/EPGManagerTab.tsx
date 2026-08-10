import { logger } from '../../utils/logger';
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
import type { EPGSource, EPGSourceType, SDCustomProperties, SDLineup } from '../../types';
import * as api from '../../services/api';
import { DummyEPGSourceModal } from '../DummyEPGSourceModal';
import { DummyEPGManagerSection } from '../DummyEPGManagerSection';
import { CustomSelect } from '../CustomSelect';
import { ModalOverlay } from '../ModalOverlay';
import { PageHeader } from '../PageHeader';
import { RouteHeaderSlot } from '../RouteHeaderSlots';
import { DenseToolbar } from '../DenseToolbar';
import { OverflowMenu } from '../OverflowMenu';
import type { OverflowMenuItem } from '../OverflowMenu';
import { SourceLoadStatus } from '../SourceLoadStatus';
import { classifySourceLoadError, type SourceLoadState } from '../sourceLoadState';
import {
  guideDownloadWatchCount,
  hasGuideDownloadWatch,
  isDownloadingSource,
  noteGuideDownloadRows,
  watchGuideDownload,
} from '../../services/epgGuideWatch';
import { GuideMigrationModal } from '../GuideMigrationModal';
import { useNotifications } from '../../contexts/NotificationContext';
import { formatDateTime } from '../../utils/formatting';
import '../ModalBase.css';
import './EPGManagerTab.css';

// Priorities shared by two or more sources resolve ties by internal ID
// (bead 09x38.15 item 1) — the header tooltip explains this, but the tie
// itself was otherwise invisible. Returns the set of priority values that
// occur more than once among the given sources.
// eslint-disable-next-line react-refresh/only-export-components -- pure helper co-located with the only component that uses it; splitting is mechanical churn
export function getTiedPriorities(sources: EPGSource[]): Set<number> {
  const counts = new Map<number, number>();
  for (const s of sources) {
    counts.set(s.priority, (counts.get(s.priority) || 0) + 1);
  }
  const tied = new Set<number>();
  for (const [priority, count] of counts) {
    if (count > 1) tied.add(priority);
  }
  return tied;
}

interface SortableEPGSourceRowProps {
  source: EPGSource;
  onEdit: (source: EPGSource) => void;
  onDelete: (source: EPGSource) => void;
  onRefresh: (source: EPGSource) => void;
  onToggleActive: (source: EPGSource) => void;
  hideEpgUrls?: boolean;
  isTied?: boolean;
}

function SortableEPGSourceRow({ source, onEdit, onDelete, onRefresh, onToggleActive, hideEpgUrls = false, isTied = false }: SortableEPGSourceRowProps) {
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

  // Per-source actions collapsed into the row's kebab so the visible buttons
  // fit the actions column at their declared 32px box
  // (bead enhancedchannelmanager-xh33o).
  const rowMenuItems: OverflowMenuItem[] = [
    ...(source.url && !hideEpgUrls
      ? [{
          label: 'Copy URL',
          icon: 'content_copy',
          onClick: () => { navigator.clipboard.writeText(source.url!); },
        }]
      : []),
    {
      label: 'Delete',
      icon: 'delete',
      onClick: () => onDelete(source),
    },
  ];

  const getSourceTypeLabel = (type: EPGSourceType) => {
    switch (type) {
      case 'xmltv': return 'XMLTV';
      case 'schedules_direct': return 'Schedules Direct';
      case 'dummy': return 'Dummy';
    }
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
        {isTied && (
          <span
            className="badge badge-warning badge-sm priority-tie-badge"
            title="Another source shares this priority. Ties are broken by internal ID (lower wins) — drag in Reorder mode to assign a distinct priority."
          >
            tie
          </span>
        )}
      </div>

      <div className={`source-status ${getStatusClass(source.status)}`} title={source.last_message || ''}>
        <span className={`material-icons ${isRefreshing ? 'spinning' : ''}`}>
          {getStatusIcon(source.status)}
        </span>
        <span className="status-label micro-label">{getStatusLabel(source.status)}</span>
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
        <span className="updated-time">{formatDateTime(source.updated_at)}</span>
      </div>

      {/* Three primary actions plus the kebab, which is what the actions
          column holds at the declared 32px box (bead
          enhancedchannelmanager-xh33o). Copy URL is conditional on the source
          having a URL and on the hide-URLs setting; putting it in the kebab
          keeps the visible count fixed at three whatever those resolve to. */}
      <div className="source-actions">
        <button
          className={`action-btn toggle ${source.is_active ? 'active' : ''}`}
          onClick={() => onToggleActive(source)}
          title={source.is_active ? 'Disable' : 'Enable'}
          aria-label={source.is_active ? 'Disable EPG source' : 'Enable EPG source'}
          aria-pressed={source.is_active}
        >
          <span className="material-icons" aria-hidden="true">
            {source.is_active ? 'toggle_on' : 'toggle_off'}
          </span>
        </button>
        <button
          className="action-btn"
          onClick={() => onRefresh(source)}
          title="Refresh"
          disabled={!source.is_active || source.status === 'fetching' || source.status === 'parsing'}
          aria-label="Refresh EPG source"
        >
          <span className="material-icons" aria-hidden="true">refresh</span>
        </button>
        <button
          className="action-btn"
          onClick={() => onEdit(source)}
          title="Edit"
          aria-label="Edit EPG source"
        >
          <span className="material-icons" aria-hidden="true">edit</span>
        </button>
        <OverflowMenu items={rowMenuItems} label={`More actions for ${source.name}`} />
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

// Schedules Direct enforces a ~200-request/2h limit; Dispatcharr refuses
// refreshes more often than every 2 hours. Surface that as the SD minimum.
const SD_MIN_REFRESH_HOURS = 2;

// Logo-style previews. Each shows SD's ESPN HD station (s32645) rendered in that
// style, straight from SD's public logo bucket — same images Dispatcharr uses so
// operators can eyeball the variants before choosing.
// Base ends at the station id; each style is a filename suffix (no extra path
// segment) — matches Dispatcharr exactly: .../stationLogos/s32645_dark_360w_270h.png
const SD_LOGO_PREVIEW_BASE =
  'https://schedulesdirect-api20141201-logos.s3.dualstack.us-east-1.amazonaws.com/stationLogos/s32645';
const SD_LOGO_STYLES: { value: NonNullable<SDCustomProperties['logo_style']>; label: string; url: string }[] = [
  { value: 'dark', label: 'Dark', url: `${SD_LOGO_PREVIEW_BASE}_dark_360w_270h.png` },
  { value: 'white', label: 'White', url: `${SD_LOGO_PREVIEW_BASE}_white_360w_270h.png` },
  { value: 'gray', label: 'Gray', url: `${SD_LOGO_PREVIEW_BASE}_gray_360w_270h.png` },
  { value: 'light', label: 'Light', url: `${SD_LOGO_PREVIEW_BASE}_light_360w_270h.png` },
];

function EPGSourceModal({ isOpen, source, onClose, onSave }: EPGSourceModalProps) {
  const [name, setName] = useState('');
  const [sourceType, setSourceType] = useState<EPGSourceType>('xmltv');
  const [url, setUrl] = useState('');
  // Schedules Direct credentials. Password is write-only: blank means "keep
  // existing" on edit (preserve-on-omit), mirroring smtp_password / emby_api_key.
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  // Schedules Direct settings (persisted into custom_properties).
  const [logoStyle, setLogoStyle] = useState<NonNullable<SDCustomProperties['logo_style']>>('dark');
  const [autoApplyLogos, setAutoApplyLogos] = useState(false);
  const [fetchPosters, setFetchPosters] = useState(false);
  const [posterStyle, setPosterStyle] = useState('sd_recommended');
  const [refreshInterval, setRefreshInterval] = useState(24);
  const [priority, setPriority] = useState(0);
  const [isActive, setIsActive] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isSD = sourceType === 'schedules_direct';

  useEffect(() => {
    if (source) {
      setName(source.name);
      setSourceType(source.source_type);
      setUrl(source.url || '');
      setUsername(source.username || '');
      setPassword('');
      const cp = (source.custom_properties || {}) as SDCustomProperties;
      setLogoStyle(cp.logo_style || 'dark');
      setAutoApplyLogos(!!cp.auto_apply_epg_logos);
      setFetchPosters(!!cp.fetch_posters);
      setPosterStyle(cp.poster_style || 'sd_recommended');
      setRefreshInterval(source.refresh_interval);
      setPriority(source.priority);
      setIsActive(source.is_active);
    } else {
      setName('');
      setSourceType('xmltv');
      setUrl('');
      setUsername('');
      setPassword('');
      setLogoStyle('dark');
      setAutoApplyLogos(false);
      setFetchPosters(false);
      setPosterStyle('sd_recommended');
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

    if (isSD) {
      if (!username.trim()) {
        setError('Username is required for Schedules Direct sources');
        return;
      }
      // Password required on create; on edit a blank field keeps the stored one.
      if (!source && !password.trim()) {
        setError('Password is required for Schedules Direct sources');
        return;
      }
      if (refreshInterval > 0 && refreshInterval < SD_MIN_REFRESH_HOURS) {
        setError(`Schedules Direct refresh interval must be at least ${SD_MIN_REFRESH_HOURS} hours`);
        return;
      }
    }

    setSaving(true);
    try {
      const payload: api.CreateEPGSourceRequest = {
        name: name.trim(),
        source_type: sourceType,
        url: sourceType === 'xmltv' ? url.trim() : null,
        refresh_interval: refreshInterval,
        priority,
        is_active: isActive,
      };
      if (isSD) {
        payload.username = username.trim();
        // Preserve-on-omit: only send password when the user typed one.
        if (password.trim()) {
          payload.password = password.trim();
        }
        payload.custom_properties = {
          logo_style: logoStyle,
          auto_apply_epg_logos: autoApplyLogos,
          fetch_posters: fetchPosters,
          poster_style: posterStyle,
        } satisfies SDCustomProperties;
      }
      await onSave(payload);
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save EPG source');
    } finally {
      setSaving(false);
    }
  };

  if (!isOpen) return null;

  return (
    <ModalOverlay onClose={onClose}>
      <div className="modal-container modal-lg epg-source-modal">
        <div className="modal-header">
          <h2>{source ? 'Edit EPG Source' : 'Add Standard EPG'}</h2>
          <button className="modal-close-btn" onClick={onClose} aria-label="Close" title="Close">
            <span className="material-icons" aria-hidden="true">close</span>
          </button>
        </div>

        <form onSubmit={handleSubmit}>
          <div className="modal-body">
            <div className="modal-form-group">
              <label htmlFor="name">Name <span className="modal-required">*</span></label>
              <input
                id="name"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="My EPG Source"
                autoFocus
              />
            </div>

            <div className="modal-form-group">
              <label htmlFor="sourceType">Source Type</label>
              <CustomSelect
                value={sourceType}
                onChange={(val) => setSourceType(val as EPGSourceType)}
                disabled={!!source}
                options={[
                  { value: 'xmltv', label: 'XMLTV (URL)' },
                  { value: 'schedules_direct', label: 'Schedules Direct' },
                ]}
              />
              {source && (
                <p className="form-hint">Source type cannot be changed after creation</p>
              )}
            </div>

            {sourceType === 'xmltv' && (
              <div className="modal-form-group">
                <label htmlFor="url">XMLTV URL <span className="modal-required">*</span></label>
                <input
                  id="url"
                  type="url"
                  value={url}
                  onChange={(e) => setUrl(e.target.value)}
                  placeholder="https://example.com/epg.xml"
                />
              </div>
            )}

            {isSD && (
              <>
                <p className="form-hint sd-rate-note">
                  Schedules Direct is a paid account with strict rate limits
                  (lineup changes ~6/24h, refresh no more than every {SD_MIN_REFRESH_HOURS} hours).
                </p>
                <div className="modal-form-group">
                  <label htmlFor="sdUsername">Username <span className="modal-required">*</span></label>
                  <input
                    id="sdUsername"
                    type="text"
                    autoComplete="off"
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    placeholder="Your Schedules Direct username"
                  />
                </div>
                <div className="modal-form-group">
                  <label htmlFor="sdPassword">
                    Password {!source && <span className="modal-required">*</span>}
                  </label>
                  <input
                    id="sdPassword"
                    type="password"
                    autoComplete="new-password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder={source ? 'Leave blank to keep current password' : 'Your Schedules Direct password'}
                  />
                </div>
                <div className="modal-form-group">
                  <label>Station Logo Style</label>
                  <div className="sd-logo-style-row" role="radiogroup" aria-label="Station logo style">
                    {SD_LOGO_STYLES.map((style) => (
                      <button
                        key={style.value}
                        type="button"
                        role="radio"
                        aria-checked={logoStyle === style.value}
                        className={`sd-logo-style-option${logoStyle === style.value ? ' selected' : ''}`}
                        onClick={() => setLogoStyle(style.value)}
                      >
                        <img
                          src={style.url}
                          alt={`${style.label} logo example`}
                          loading="lazy"
                          onError={(e) => { (e.target as HTMLImageElement).style.visibility = 'hidden'; }}
                        />
                        <span>{style.label}</span>
                      </button>
                    ))}
                  </div>
                </div>
                <div className="modal-form-group">
                  <label className="modal-checkbox-label">
                    <input
                      type="checkbox"
                      checked={autoApplyLogos}
                      onChange={(e) => setAutoApplyLogos(e.target.checked)}
                    />
                    <span>Auto-apply EPG logos to channels</span>
                  </label>
                </div>
                <div className="modal-form-group">
                  <label className="modal-checkbox-label">
                    <input
                      type="checkbox"
                      checked={fetchPosters}
                      onChange={(e) => setFetchPosters(e.target.checked)}
                    />
                    <span>Fetch program posters (uses extra SD API requests)</span>
                  </label>
                </div>
                {fetchPosters && (
                  <div className="modal-form-group">
                    <label htmlFor="sdPosterStyle">Poster Style</label>
                    <CustomSelect
                      value={posterStyle}
                      onChange={(val) => setPosterStyle(val)}
                      options={[
                        { value: 'sd_recommended', label: 'SD Recommended' },
                        { value: 'poster', label: 'Poster (2x3)' },
                        { value: 'banner', label: 'Banner' },
                        { value: 'banner-l1', label: 'Banner L1' },
                      ]}
                    />
                  </div>
                )}
              </>
            )}

            <div className="modal-form-row">
              <div className="modal-form-group">
                <label htmlFor="refreshInterval">Refresh Interval (hours)</label>
                <input
                  id="refreshInterval"
                  type="number"
                  min={isSD ? SD_MIN_REFRESH_HOURS : 0}
                  max="168"
                  value={refreshInterval}
                  onChange={(e) => setRefreshInterval(parseInt(e.target.value))}
                />
                <span className="form-hint">
                  {isSD ? `0 = manual; min ${SD_MIN_REFRESH_HOURS}h, 24h recommended` : '0 = manual refresh only'}
                </span>
              </div>

              <div className="modal-form-group">
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

            <div className="modal-form-group">
              <label className="modal-checkbox-label">
                <input
                  type="checkbox"
                  checked={isActive}
                  onChange={(e) => setIsActive(e.target.checked)}
                />
                <span>Active</span>
              </label>
            </div>

            {error && <div className="modal-error-banner">{error}</div>}

            {source && isSD && <SDLineupManager sourceId={source.id} />}
          </div>

          <div className="modal-footer">
            <button type="button" className="modal-btn modal-btn-secondary" onClick={onClose} disabled={saving}>
              Cancel
            </button>
            <button type="submit" className="modal-btn modal-btn-primary" disabled={saving}>
              {saving ? 'Saving...' : source ? 'Save Changes' : 'Add EPG'}
            </button>
          </div>
        </form>
      </div>
    </ModalOverlay>
  );
}

// ISO-3166 alpha-3 countries Schedules Direct supports lineup search for.
// Mirrors Dispatcharr's hardcoded fallback list (SD has no client-facing
// country-list proxy endpoint, so ECM ships the list rather than add a hop).
const SD_COUNTRIES: { value: string; label: string }[] = [
  { value: 'USA', label: 'United States' },
  { value: 'CAN', label: 'Canada' },
  { value: 'GBR', label: 'United Kingdom' },
  { value: 'IRL', label: 'Ireland' },
  { value: 'AUS', label: 'Australia' },
  { value: 'NZL', label: 'New Zealand' },
  { value: 'MEX', label: 'Mexico' },
  { value: 'BRA', label: 'Brazil' },
  { value: 'DEU', label: 'Germany' },
  { value: 'NLD', label: 'Netherlands' },
];

interface SDLineupManagerProps {
  sourceId: number;
}

// Live Schedules Direct lineup manager. Lineups live on the SD account, not in
// Dispatcharr's DB, so every action here authenticates to SD through Dispatcharr
// and counts against SD's rate limits — no auto-refresh/polling loops.
function SDLineupManager({ sourceId }: SDLineupManagerProps) {
  const notifications = useNotifications();
  const [lineups, setLineups] = useState<SDLineup[]>([]);
  const [maxLineups, setMaxLineups] = useState<number | null>(null);
  const [changesRemaining, setChangesRemaining] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyLineup, setBusyLineup] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [country, setCountry] = useState('USA');
  const [postalcode, setPostalcode] = useState('');
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<SDLineup[]>([]);
  const [searched, setSearched] = useState(false);

  const loadLineups = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.getSDLineups(sourceId);
      setLineups(data.lineups || []);
      setMaxLineups(data.max_lineups ?? null);
      setChangesRemaining(data.changes_remaining ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load lineups');
    } finally {
      setLoading(false);
    }
  }, [sourceId]);

  useEffect(() => {
    loadLineups();
  }, [loadLineups]);

  const handleSearch = async () => {
    if (!postalcode.trim()) {
      setError('Postal code is required to search');
      return;
    }
    setSearching(true);
    setError(null);
    setSearched(true);
    try {
      const data = await api.searchSDLineups(sourceId, country, postalcode.trim());
      setResults(data.lineups || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Lineup search failed');
      setResults([]);
    } finally {
      setSearching(false);
    }
  };

  const activeIds = new Set(lineups.map((l) => l.lineup));

  const handleAdd = async (lineup: string) => {
    setBusyLineup(lineup);
    setError(null);
    try {
      await api.addSDLineup(sourceId, lineup);
      notifications.success(`Added lineup ${lineup}`, 'Schedules Direct');
      await loadLineups();
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to add lineup';
      setError(msg);
      notifications.error(msg, 'Schedules Direct');
    } finally {
      setBusyLineup(null);
    }
  };

  const handleRemove = async (lineup: string) => {
    setBusyLineup(lineup);
    setError(null);
    try {
      await api.deleteSDLineup(sourceId, lineup);
      notifications.success(`Removed lineup ${lineup}`, 'Schedules Direct');
      await loadLineups();
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Failed to remove lineup';
      setError(msg);
      notifications.error(msg, 'Schedules Direct');
    } finally {
      setBusyLineup(null);
    }
  };

  const atMax = maxLineups != null && lineups.length >= maxLineups;
  const noChangesLeft = changesRemaining != null && changesRemaining <= 0;

  return (
    <div className="sd-lineup-manager">
      <h3 className="modal-section-title">
        Lineups{maxLineups != null ? ` (${lineups.length}/${maxLineups})` : ''}
        {changesRemaining != null && (
          <span className="sd-changes-remaining"> · {changesRemaining} changes left today</span>
        )}
      </h3>

      {error && <div className="modal-error-banner">{error}</div>}
      {noChangesLeft && (
        <p className="form-hint sd-rate-note">
          Daily Schedules Direct lineup-change limit reached. Try again later.
        </p>
      )}

      {loading ? (
        <p className="form-hint">Loading lineups…</p>
      ) : lineups.length === 0 ? (
        <p className="form-hint">No lineups added yet. Search below to add one.</p>
      ) : (
        <ul className="sd-lineup-list">
          {lineups.map((l) => (
            <li key={l.lineup} className="sd-lineup-row">
              <span className="sd-lineup-name">
                {l.name || l.lineup}
                <span className="sd-lineup-id"> ({l.lineup})</span>
              </span>
              <button
                type="button"
                className="modal-btn modal-btn-secondary modal-btn-small"
                disabled={busyLineup === l.lineup || noChangesLeft}
                onClick={() => handleRemove(l.lineup)}
              >
                {busyLineup === l.lineup ? '…' : 'Remove'}
              </button>
            </li>
          ))}
        </ul>
      )}

      <div className="sd-lineup-search">
        <div className="modal-form-row sd-lineup-search-row">
          <div className="modal-form-group">
            <label htmlFor="sdCountry">Country</label>
            <CustomSelect value={country} onChange={setCountry} options={SD_COUNTRIES} />
          </div>
          <div className="modal-form-group">
            <label htmlFor="sdPostal">Postal Code</label>
            <input
              id="sdPostal"
              type="text"
              value={postalcode}
              onChange={(e) => setPostalcode(e.target.value)}
              placeholder="e.g. 90210"
            />
          </div>
          <div className="modal-form-group sd-search-btn-group">
            <button
              type="button"
              className="modal-btn modal-btn-secondary"
              onClick={handleSearch}
              disabled={searching}
            >
              {searching ? 'Searching…' : 'Search'}
            </button>
          </div>
        </div>

        {searched && !searching && results.length === 0 && (
          <p className="form-hint">No lineups found for that location.</p>
        )}

        {results.length > 0 && (
          <ul className="sd-lineup-list sd-search-results">
            {results.map((l) => (
              <li key={l.lineup} className="sd-lineup-row">
                <span className="sd-lineup-name">
                  {l.name || l.lineup}
                  {l.transport && <span className="sd-lineup-meta"> [{l.transport}]</span>}
                  {l.location && <span className="sd-lineup-meta"> {l.location}</span>}
                  <span className="sd-lineup-id"> ({l.lineup})</span>
                </span>
                {activeIds.has(l.lineup) ? (
                  <span className="sd-lineup-added">Added</span>
                ) : (
                  <button
                    type="button"
                    className="modal-btn modal-btn-primary modal-btn-small"
                    disabled={busyLineup === l.lineup || atMax || noChangesLeft}
                    onClick={() => handleAdd(l.lineup)}
                    title={atMax ? 'Maximum lineups reached' : undefined}
                  >
                    {busyLineup === l.lineup ? '…' : 'Add'}
                  </button>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

interface EPGManagerTabProps {
  onSourcesChange?: () => void;
  hideEpgUrls?: boolean;
}

export function EPGManagerTab({ onSourcesChange, hideEpgUrls = false }: EPGManagerTabProps) {
  const notifications = useNotifications();
  const [sources, setSources] = useState<EPGSource[]>([]);
  const [dummySources, setDummySources] = useState<EPGSource[]>([]);
  const [loading, setLoading] = useState(true);
  const [sourceLoadState, setSourceLoadState] = useState<SourceLoadState>('loading');
  const [hasLoadedSourceData, setHasLoadedSourceData] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [editingSource, setEditingSource] = useState<EPGSource | null>(null);
  const [refreshingAll, setRefreshingAll] = useState(false);
  const [migrationOpen, setMigrationOpen] = useState(false);
  // Dummy EPG modal state
  const [dummyModalOpen, setDummyModalOpen] = useState(false);
  const [editingDummySource, setEditingDummySource] = useState<EPGSource | null>(null);

  // Reorder mode gates the DndContext mount. @dnd-kit's useRect() attaches a
  // MutationObserver to document.body for every mounted DndContext; we only
  // mount when the user is actively reordering EPG source priorities (gh #207).
  const [isReorderMode, setIsReorderMode] = useState(false);

  const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    })
  );

  // The guide-download watch lives in `services/epgGuideWatch`, at module
  // scope, NOT in this component. An operator who adds a source and navigates
  // away while it is still parsing unmounts this tab, and a watch that died
  // with it never published the completion — leaving the Edit Channel guide
  // picker on its app-start snapshot until a full reload (bead
  // enhancedchannelmanager-1twap). Still no standing polling and no
  // refetch-on-focus; see that module's header.

  const loadSources = useCallback(async () => {
    setLoading(true);
    setSourceLoadState('loading');
    try {
      const data = await api.getEPGSources();
      noteGuideDownloadRows(data);
      // Separate standard and dummy EPG sources, sort by priority (descending)
      const standardSources = data
        .filter(s => s.source_type !== 'dummy')
        .sort((a, b) => b.priority - a.priority);
      const dummyEpgSources = data
        .filter(s => s.source_type === 'dummy')
        .sort((a, b) => a.name.localeCompare(b.name));
      setSources(standardSources);
      setDummySources(dummyEpgSources);
      setHasLoadedSourceData(true);
      setSourceLoadState('success');
    } catch (err) {
      setSourceLoadState(classifySourceLoadError(err));
      notifications.error(err instanceof Error ? err.message : 'Failed to load EPG sources', 'EPG Manager');
    } finally {
      setLoading(false);
    }
  }, [notifications]);

  useEffect(() => {
    loadSources();
  }, [loadSources]);

  // Poll while any source is in a transitional state (fetching/parsing)
  useEffect(() => {
    const hasTransitional = [...sources, ...dummySources].some(isDownloadingSource);
    if (!hasTransitional) return;
    const interval = setInterval(loadSources, 2000);
    const timeout = setTimeout(() => clearInterval(interval), 300000);
    return () => { clearInterval(interval); clearTimeout(timeout); };
  }, [sources, dummySources, loadSources]);

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
        logger.error('EPGManagerTab: failed to update priorities', err);
        notifications.error('Failed to update priorities', 'EPG Manager');
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
      logger.error('EPGManagerTab: failed to load EPG source details', err);
      notifications.error('Failed to load EPG source details', 'EPG Manager');
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
      logger.error('EPGManagerTab: failed to delete EPG source', err);
      notifications.error('Failed to delete EPG source', 'EPG Manager');
    }
  };

  const handleRefreshSource = async (source: EPGSource) => {
    try {
      await api.refreshEPGSource(source.id);
      // A re-download replaces this source's guide rows, so its completion is
      // the same `App.epgData` event a first download is (bead …-3vtim).
      watchGuideDownload(source);
      // Immediately show we're refreshing by updating local state
      setSources(prev => prev.map(s =>
        s.id === source.id ? { ...s, status: 'fetching' } : s
      ));
      // Start polling for status updates every 2 seconds
      const pollInterval = setInterval(async () => {
        const updatedSources = await api.getEPGSources();
        noteGuideDownloadRows(updatedSources);
        setSources(updatedSources.filter(s => s.source_type !== 'dummy').sort((a, b) => b.priority - a.priority));
        // Stop polling once the download we started has genuinely finished.
        //
        // This used to stop on `status === 'success'`, which is the reading
        // Dispatcharr still returns for the PREVIOUS run in the seconds before it
        // picks the job up — so the poller quit immediately and never saw the
        // refresh it was watching (live re-drive 2026-08-09: 65s refresh, zero
        // guide refetches). The watch is the authority: it clears only on a
        // result that is demonstrably newer than the one at click time.
        if (!hasGuideDownloadWatch(source.id)) {
          clearInterval(pollInterval);
        }
      }, 2000);
      // Stop polling after 5 minutes max
      setTimeout(() => clearInterval(pollInterval), 300000);
    } catch (err) {
      logger.error('EPGManagerTab: failed to refresh EPG source', err);
      notifications.error('Failed to refresh EPG source', 'EPG Manager');
    }
  };

  const handleToggleActive = async (source: EPGSource) => {
    try {
      await api.updateEPGSource(source.id, { is_active: !source.is_active });
      await loadSources();
    } catch (err) {
      logger.error('EPGManagerTab: failed to update EPG source active state', err);
      notifications.error('Failed to update EPG source', 'EPG Manager');
    }
  };

  const handleSaveSource = async (data: api.CreateEPGSourceRequest) => {
    if (editingSource) {
      await api.updateEPGSource(editingSource.id, data);
    } else {
      const created = await api.createEPGSource(data);
      // A brand-new source has guide rows coming that `App.epgData` has never
      // seen (bead enhancedchannelmanager-3vtim).
      watchGuideDownload(created);
    }
    await loadSources();
    onSourcesChange?.();
  };

  // Dummy EPG handlers. New-creation is intentionally absent: the legacy
  // Dispatcharr `source_type=dummy` path is deprecated in favor of the Dummy
  // EPG Profiles section (bead 09x38.4). Existing legacy sources stay editable.
  const handleEditDummySource = async (source: EPGSource) => {
    try {
      // Fetch full source details to get custom_properties
      const fullSource = await api.getEPGSource(source.id);
      setEditingDummySource(fullSource);
      setDummyModalOpen(true);
    } catch (err) {
      logger.error('EPGManagerTab: failed to load dummy EPG source details', err);
      notifications.error('Failed to load EPG source details', 'EPG Manager');
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
      logger.error('EPGManagerTab: failed to delete dummy EPG source', err);
      notifications.error('Failed to delete dummy EPG source', 'EPG Manager');
    }
  };

  const handleToggleDummyActive = async (source: EPGSource) => {
    try {
      await api.updateEPGSource(source.id, { is_active: !source.is_active });
      await loadSources();
    } catch (err) {
      logger.error('EPGManagerTab: failed to toggle dummy EPG source active state', err);
      notifications.error('Failed to update dummy EPG source', 'EPG Manager');
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
    logger.debug('[EPGManagerTab] handleRefreshAll called!');
    setRefreshingAll(true);
    try {
      logger.debug('[EPGManagerTab] Triggering EPG import...');
      await api.triggerEPGImport();
      logger.debug('[EPGManagerTab] EPG import triggered successfully');
      // Every source about to re-download can change `App.epgData` (bead …-3vtim).
      for (const s of sources) {
        if (s.is_active && s.source_type !== 'dummy') watchGuideDownload(s);
      }
      // Mark all active non-dummy sources as fetching
      setSources(prev => prev.map(s =>
        s.is_active && s.source_type !== 'dummy' ? { ...s, status: 'fetching' } : s
      ));
      // Start polling for status updates every 2 seconds
      const pollInterval = setInterval(async () => {
        const updatedSources = await api.getEPGSources();
        noteGuideDownloadRows(updatedSources);
        const standardSources = updatedSources.filter(s => s.source_type !== 'dummy').sort((a, b) => b.priority - a.priority);
        setSources(standardSources);
        // Stop polling when every source is done. Outstanding watches count as
        // "not done": a source whose status still reads `success` from the
        // previous run has not started yet, and stopping here would strand it.
        const stillRefreshing =
          standardSources.some(isDownloadingSource) ||
          guideDownloadWatchCount() > 0;
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
      logger.error('[EPGManagerTab] Failed to trigger EPG import:', err);
      notifications.error('Failed to trigger EPG import', 'EPG Manager');
      setRefreshingAll(false);
    }
  };

  if (loading && !hasLoadedSourceData) {
    return (
      <div className="epg-manager-tab">
        <RouteHeaderSlot name="controls">
          <DenseToolbar
            label="EPG source controls"
            secondaryActions={(
              <button className="btn-secondary" type="button" disabled>
                <span className="material-icons spinning">sync</span>
                Loading sources
              </button>
            )}
          />
        </RouteHeaderSlot>
        <RouteHeaderSlot name="status">
          <SourceLoadStatus
            state={sourceLoadState}
            sourceName="EPG sources"
            successText=""
          />
        </RouteHeaderSlot>
        <div className="tab-loading">
          <span className="material-icons spinning">sync</span>
          <p>Loading EPG sources...</p>
        </div>
      </div>
    );
  }
  if (sourceLoadState === 'permission' || (sourceLoadState === 'error' && !hasLoadedSourceData)) {
    return (
      <div className="epg-manager-tab">
        <RouteHeaderSlot name="status">
          <SourceLoadStatus
            state={sourceLoadState}
            sourceName="EPG sources"
            successText=""
            onRetry={loadSources}
          />
        </RouteHeaderSlot>
        <div className="tab-load-unavailable">
          <SourceLoadStatus
            state={sourceLoadState}
            sourceName="EPG sources"
            successText=""
            announce={false}
          />
        </div>
      </div>
    );
  }

  const tiedPriorities = getTiedPriorities(sources);

  return (
    <div className="epg-manager-tab">
      <RouteHeaderSlot name="primary-action">
        <button className="btn-primary" onClick={handleAddSource}>
          <span className="material-icons">add</span>
          Add Standard EPG
        </button>
      </RouteHeaderSlot>
      {/* A healthy, loaded EPG Manager says nothing here — see the matching
          comment in M3UManagerTab.tsx. "N EPG sources" restated the list
          directly beneath it (bead enhancedchannelmanager-tygwm); the refresh
          and stale-with-Retry states stay because the list cannot show them.
          EPG Manager has no related links, so this is also the route that
          leaves the header's meta row genuinely empty. */}
      {(refreshingAll || sourceLoadState !== 'success') && (
        <RouteHeaderSlot name="status">
          {refreshingAll
            ? <span>EPG refresh in progress</span>
            : (
              <SourceLoadStatus
                state={sourceLoadState}
                sourceName="EPG sources"
                successText=""
                stale={sourceLoadState === 'error'}
                onRetry={loadSources}
              />
            )}
        </RouteHeaderSlot>
      )}
      <RouteHeaderSlot name="controls">
        <DenseToolbar
          label="EPG source controls"
          sortView={sources.length > 1 ? <button
            className="btn-secondary"
            onClick={() => setIsReorderMode((v) => !v)}
            title={isReorderMode ? 'Exit reorder mode' : 'Reorder priority'}
          >
            <span className="material-icons">{isReorderMode ? 'check' : 'reorder'}</span>
            {isReorderMode ? 'Done' : 'Reorder'}
          </button> : undefined}
          secondaryActions={<>
            <button className="btn-secondary" onClick={handleRefreshAll} disabled={refreshingAll}>
              <span className={`material-icons ${refreshingAll ? 'spinning' : ''}`}>sync</span>
              {refreshingAll ? 'Refreshing...' : 'Refresh All'}
            </button>
            {sources.length > 1 && (
              <button className="btn-secondary" onClick={() => setMigrationOpen(true)}>
                <span className="material-icons">swap_horiz</span>
                Migrate Guides
              </button>
            )}
          </>}
        />
      </RouteHeaderSlot>

      {/* The pane used to open on an unlabelled table — route title,
          description, then straight into the list, while the section below it
          was labelled "Dummy EPG Profiles" (bead
          enhancedchannelmanager-7dxx0). Rendered through PageHeader rather
          than a local h2 so it takes the same section role
          (`.header-title h2`, 15px/600/1.3) as that heading, automatically.
          Unconditional: the heading names the section, not the rows, and
          keeping it over the empty state also stops the outline skipping from
          the route h1 to the empty state's h3. */}
      <PageHeader className="page-header-heading-only" title="EPG Sources" />

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
            <span className="col-priority" title="Higher priority number = matches first for EPG channel matching. Ties are broken by which source was added to ECM first (lower internal ID wins) — drag any row in Reorder mode to assign each source a distinct priority and resolve ties explicitly.">Priority</span>
            <span className="col-status" title="Current refresh status. Idle and Ready are normal states.">Status</span>
            <span className="col-info" title="EPG source name, type, and URL">Source</span>
            <span className="col-stats" title="Number of channels matched to this EPG and total programs parsed">Stats</span>
            <span className="col-updated" title="Last time this EPG source was refreshed">Last Updated</span>
            <span className="col-actions">Actions</span>
          </div>

          {isReorderMode ? (
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
                    isTied={tiedPriorities.has(source.priority)}
                  />
                ))}
              </SortableContext>
            </DndContext>
          ) : (
            sources.map((source) => (
              <SortableEPGSourceRow
                key={source.id}
                source={source}
                onEdit={handleEditSource}
                onDelete={handleDeleteSource}
                onRefresh={handleRefreshSource}
                onToggleActive={handleToggleActive}
                isTied={tiedPriorities.has(source.priority)}
                hideEpgUrls={hideEpgUrls}
              />
            ))
          )}
        </div>
      )}

      {/*
        Legacy "Dummy EPG Sources" section (Dispatcharr-native
        `source_type=dummy`). Deprecated in favor of Dummy EPG Profiles
        (bead 09x38.4). Renders ONLY when legacy sources already exist so it
        folds away entirely on instances with none; existing ones are
        grandfathered — editable, but with no new-creation affordance.
      */}
      {dummySources.length > 0 && (
      <div className="dummy-epg-section">
        <PageHeader
          className="epg-header"
          title="Dummy EPG Sources (Legacy)"
          description="Legacy Dispatcharr dummy EPG sources. Existing sources stay editable."
        />

        <div className="dummy-epg-deprecation-notice">
          <span className="material-icons" aria-hidden="true">info</span>
          <p>
            This legacy dummy EPG source type is deprecated. Use the{' '}
            <strong>Dummy EPG Profiles</strong> section below for new dummy EPG
            data — it offers live preview, richer templates, and Event Sync
            integration. Existing sources here remain editable but new ones can
            no longer be created.
          </p>
        </div>

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
                    className={`action-btn toggle ${source.is_active ? 'active' : ''}`}
                    onClick={() => handleToggleDummyActive(source)}
                    title={source.is_active ? 'Disable' : 'Enable'}
                    aria-label={source.is_active ? 'Disable EPG source' : 'Enable EPG source'}
                    aria-pressed={source.is_active}
                  >
                    <span className="material-icons" aria-hidden="true">
                      {source.is_active ? 'toggle_on' : 'toggle_off'}
                    </span>
                  </button>
                  <button
                    className="action-btn"
                    onClick={() => handleEditDummySource(source)}
                    title="Edit"
                    aria-label="Edit EPG source"
                  >
                    <span className="material-icons" aria-hidden="true">edit</span>
                  </button>
                  <button
                    className="action-btn delete"
                    onClick={() => handleDeleteDummySource(source)}
                    title="Delete"
                    aria-label="Delete EPG source"
                  >
                    <span className="material-icons" aria-hidden="true">delete</span>
                  </button>
                </div>
              </div>
            ))}
        </div>
      </div>
      )}

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

      <GuideMigrationModal
        isOpen={migrationOpen}
        sources={sources}
        onClose={() => setMigrationOpen(false)}
        onApplied={(result) => {
          if (result.failed > 0 || result.audit_failed > 0) {
            notifications.warning(
              `Migrated ${result.mutated} channel guides; ${result.skipped} skipped, ${result.failed} failed, and ${result.audit_failed} audit writes failed.`,
              'Guide Migration'
            );
          } else {
            notifications.success(
              `Migrated ${result.mutated} channel guides${result.skipped ? `; ${result.skipped} skipped` : ''}.`,
              'Guide Migration'
            );
          }
        }}
      />

      {/* Dummy EPG Profiles — the supported dummy EPG path (bead 09x38.4) */}
      <DummyEPGManagerSection onSourcesChanged={loadSources} />
    </div>
  );
}
