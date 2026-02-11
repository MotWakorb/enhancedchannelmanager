import { useState, useEffect, useCallback } from 'react';
import type { VideoFilter, VideoFilterType } from '../../types/ffmpegBuilder';

// ---------------------------------------------------------------------------
// Filter metadata
// ---------------------------------------------------------------------------

interface FilterOption {
  type: VideoFilterType;
  label: string;
  tooltip: string;
  defaultParams: Record<string, string | number | boolean>;
}

const FILTER_OPTIONS: FilterOption[] = [
  { type: 'scale', label: 'Scale', tooltip: 'Resize video resolution. Changes the output width and height.', defaultParams: { width: 1920, height: 1080 } },
  { type: 'fps', label: 'FPS', tooltip: 'Change the video frame rate.', defaultParams: { fps: 30 } },
  { type: 'deinterlace', label: 'Deinterlace', tooltip: 'Remove interlacing artifacts from video captured with interlaced scanning.', defaultParams: {} },
  { type: 'custom', label: 'Custom', tooltip: 'Enter a custom FFmpeg filter string.', defaultParams: { filterString: '' } },
];

function getFilterTooltip(type: VideoFilterType): string {
  return FILTER_OPTIONS.find(f => f.type === type)?.tooltip || '';
}

function getFilterLabel(type: VideoFilterType): string {
  return FILTER_OPTIONS.find(f => f.type === type)?.label || type;
}

// ---------------------------------------------------------------------------
// Scale presets
// ---------------------------------------------------------------------------

const SCALE_PRESETS = [
  { label: '1080p', width: 1920, height: 1080 },
  { label: '720p', width: 1280, height: 720 },
  { label: '480p', width: 854, height: 480 },
];

const FPS_PRESETS = [24, 25, 30, 60];

// ---------------------------------------------------------------------------
// InfoIcon component
// ---------------------------------------------------------------------------

function InfoIcon({ tooltip }: { tooltip: string }) {
  const [show, setShow] = useState(false);
  return (
    <span
      data-testid="info-icon"
      className="info-icon"
      onMouseEnter={() => setShow(true)}
      onMouseLeave={() => setShow(false)}
    >
      {'\u24D8'}
      {show && <div role="tooltip" className="tooltip">{tooltip}</div>}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Filter chain preview builder
// ---------------------------------------------------------------------------

function buildFilterChainPreview(filters: VideoFilter[], hwAccel?: string): string {
  const parts: string[] = [];

  const enabled = filters.filter(f => f.enabled).sort((a, b) => a.order - b.order);

  for (const f of enabled) {
    switch (f.type) {
      case 'scale':
        parts.push(`scale=${f.params.width ?? -1}:${f.params.height ?? -1}`);
        break;
      case 'fps':
        parts.push(`fps=${f.params.fps}`);
        break;
      case 'deinterlace':
        parts.push('yadif');
        break;
      case 'custom':
        if (f.params.filterString) parts.push(String(f.params.filterString));
        break;
      default:
        parts.push(f.type);
    }
  }

  // VAAPI hw filters
  if (hwAccel === 'vaapi' && enabled.length > 0) {
    parts.push('format=nv12');
    parts.push('hwupload_vaapi');
  }

  return parts.join(',');
}

// ---------------------------------------------------------------------------
// Filter parameter editors
// ---------------------------------------------------------------------------

function ScaleParams({
  filter,
  onUpdate,
}: {
  filter: VideoFilter;
  onUpdate: (params: Record<string, string | number | boolean>) => void;
}) {
  const width = Number(filter.params.width) || 0;
  const height = Number(filter.params.height) || 0;

  return (
    <div className="filter-params">
      <div className="setting-row">
        <label>
          {'Width'}
          <input
            type="number"
            aria-label="Width"
            value={width}
            onChange={e => onUpdate({ ...filter.params, width: Number(e.target.value) })}
          />
        </label>
        <label>
          {'Height'}
          <input
            type="number"
            aria-label="Height"
            value={height}
            onChange={e => onUpdate({ ...filter.params, height: Number(e.target.value) })}
          />
        </label>
      </div>
      <div className="filter-presets">
        {SCALE_PRESETS.map(p => (
          <button
            key={p.label}
            type="button"
            className="preset-btn"
            onClick={() => onUpdate({ ...filter.params, width: p.width, height: p.height })}
          >
            {p.label}
          </button>
        ))}
      </div>
      <label>
        <input
          type="checkbox"
          aria-label="Maintain aspect ratio"
          checked={!!filter.params.keepAspect}
          onChange={e => onUpdate({ ...filter.params, keepAspect: e.target.checked })}
        />
        {'Maintain aspect ratio'}
      </label>
      <div className="resolution-preview">{`${width}\u00d7${height}`}</div>
    </div>
  );
}

function FpsParams({
  filter,
  onUpdate,
}: {
  filter: VideoFilter;
  onUpdate: (params: Record<string, string | number | boolean>) => void;
}) {
  return (
    <div className="filter-params">
      <label>
        {'Frame Rate'}
        <input
          type="number"
          aria-label="FPS"
          value={Number(filter.params.fps) || 30}
          onChange={e => onUpdate({ ...filter.params, fps: Number(e.target.value) })}
        />
      </label>
      <div className="filter-presets">
        {FPS_PRESETS.map(fp => (
          <button
            key={fp}
            type="button"
            className="preset-btn"
            onClick={() => onUpdate({ ...filter.params, fps: fp })}
          >
            {String(fp)}
          </button>
        ))}
      </div>
    </div>
  );
}

function CustomParams({
  filter,
  onUpdate,
}: {
  filter: VideoFilter;
  onUpdate: (params: Record<string, string | number | boolean>) => void;
}) {
  const val = String(filter.params.filterString ?? '');
  return (
    <div className="filter-params">
      <label>
        {'Custom Filter'}
        <input
          type="text"
          aria-label="Custom Filter"
          value={val}
          onChange={e => onUpdate({ ...filter.params, filterString: e.target.value })}
        />
      </label>
      {!val && <div className="field-error">{'Filter string required'}</div>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// FilterItem component
// ---------------------------------------------------------------------------

function FilterItem({
  filter,
  index,
  total,
  onUpdate,
  onRemove,
  onMoveDown,
}: {
  filter: VideoFilter;
  index: number;
  total: number;
  onUpdate: (filter: VideoFilter) => void;
  onRemove: () => void;
  onMoveDown: () => void;
}) {
  const handleParamsUpdate = (params: Record<string, string | number | boolean>) => {
    onUpdate({ ...filter, params });
  };

  return (
    <div data-testid="filter-item" className={`filter-item${filter.enabled ? '' : ' disabled'}`}>
      <div className="filter-item-header">
        <input
          type="checkbox"
          role="checkbox"
          aria-label="Toggle filter"
          checked={filter.enabled}
          onChange={() => onUpdate({ ...filter, enabled: !filter.enabled })}
        />
        <span className="filter-type-label">{getFilterLabel(filter.type)}</span>
        <InfoIcon tooltip={getFilterTooltip(filter.type)} />
        <div className="filter-item-actions">
          {index < total - 1 && (
            <button type="button" aria-label="Move down" className="action-btn" onClick={onMoveDown}>{'↓'}</button>
          )}
          <button type="button" aria-label="Remove" className="action-btn danger" onClick={onRemove}>{'×'}</button>
        </div>
      </div>
      {filter.type === 'scale' && <ScaleParams filter={filter} onUpdate={handleParamsUpdate} />}
      {filter.type === 'fps' && <FpsParams filter={filter} onUpdate={handleParamsUpdate} />}
      {filter.type === 'custom' && <CustomParams filter={filter} onUpdate={handleParamsUpdate} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// VideoFilters component
// ---------------------------------------------------------------------------

interface VideoFiltersProps {
  value: VideoFilter[];
  onChange: (filters: VideoFilter[]) => void;
  hwAccel?: string;
}

export function VideoFilters({ value, onChange, hwAccel }: VideoFiltersProps) {
  const [filters, setFilters] = useState<VideoFilter[]>(value);
  const [showAddMenu, setShowAddMenu] = useState(false);

  useEffect(() => {
    setFilters(value);
  }, [value]);

  const emit = useCallback((next: VideoFilter[]) => {
    setFilters(next);
    onChange(next);
  }, [onChange]);

  const addFilter = (type: VideoFilterType) => {
    const opt = FILTER_OPTIONS.find(f => f.type === type);
    const newFilter: VideoFilter = {
      type,
      enabled: true,
      params: { ...(opt?.defaultParams || {}) },
      order: filters.length,
    };
    emit([...filters, newFilter]);
    setShowAddMenu(false);
  };

  const updateFilter = (index: number, updated: VideoFilter) => {
    const next = filters.map((f, i) => (i === index ? updated : f));
    emit(next);
  };

  const removeFilter = (index: number) => {
    const next = filters.filter((_, i) => i !== index).map((f, i) => ({ ...f, order: i }));
    emit(next);
  };

  const moveDown = (index: number) => {
    if (index >= filters.length - 1) return;
    const next = [...filters];
    const a = { ...next[index], order: index + 1 };
    const b = { ...next[index + 1], order: index };
    next[index] = b;
    next[index + 1] = a;
    emit(next);
  };

  const sorted = [...filters].sort((a, b) => a.order - b.order);
  const preview = buildFilterChainPreview(filters, hwAccel);

  return (
    <div className="video-filters">
      <div data-testid="video-filter-list" className="filter-list">
        {sorted.map((f, i) => (
          <FilterItem
            key={`${f.type}-${f.order}`}
            filter={f}
            index={i}
            total={sorted.length}
            onUpdate={updated => updateFilter(filters.indexOf(f), updated)}
            onRemove={() => removeFilter(filters.indexOf(f))}
            onMoveDown={() => moveDown(filters.indexOf(f))}
          />
        ))}
      </div>

      {/* Add filter dropdown */}
      <div className="add-filter-container">
        <button
          type="button"
          aria-label="Add filter"
          data-testid="add-video-filter"
          className="add-filter-btn"
          onClick={() => setShowAddMenu(!showAddMenu)}
        >
          {'+ Add Filter'}
        </button>
        {showAddMenu && (
          <div role="listbox" className="filter-type-dropdown">
            {FILTER_OPTIONS.map(opt => (
              <div
                key={opt.type}
                role="option"
                aria-selected={false}
                aria-label={opt.label}
                className="filter-type-option"
                onClick={() => addFilter(opt.type)}
              >
                {opt.label}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Filter chain preview */}
      <div data-testid="filter-chain-preview" className="filter-chain-preview">
        {preview || 'No filters configured'}
      </div>

      {/* VAAPI hw filter notice */}
      {hwAccel === 'vaapi' && filters.some(f => f.enabled) && (
        <div className="hw-filter-notice">
          {'Auto-inserted hardware upload filters for VAAPI pipeline'}
        </div>
      )}
    </div>
  );
}
