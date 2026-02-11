import { useState, useEffect, useCallback } from 'react';
import type { AudioFilter, AudioFilterType } from '../../types/ffmpegBuilder';

// ---------------------------------------------------------------------------
// Filter metadata
// ---------------------------------------------------------------------------

interface FilterOption {
  type: AudioFilterType;
  label: string;
  tooltip: string;
  defaultParams: Record<string, string | number | boolean>;
}

const FILTER_OPTIONS: FilterOption[] = [
  { type: 'volume', label: 'Volume', tooltip: 'Adjust audio volume level in dB. 1.0 = original, >1 = louder, <1 = quieter.', defaultParams: { volume: 1.0 } },
  { type: 'loudnorm', label: 'Loudnorm', tooltip: 'EBU R128 loudness normalization. Ensures consistent loudness across content.', defaultParams: { I: -24, LRA: 7, TP: -2 } },
  { type: 'aresample', label: 'Aresample', tooltip: 'Resample audio to a different sample rate.', defaultParams: { sampleRate: 48000 } },
  { type: 'custom', label: 'Custom', tooltip: 'Enter a custom FFmpeg audio filter string.', defaultParams: { filterString: '' } },
];

function getFilterTooltip(type: AudioFilterType): string {
  return FILTER_OPTIONS.find(f => f.type === type)?.tooltip || '';
}

function getFilterLabel(type: AudioFilterType): string {
  return FILTER_OPTIONS.find(f => f.type === type)?.label || type;
}

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
// Volume dB helper
// ---------------------------------------------------------------------------

function volumeToDb(volume: number): string {
  if (volume <= 0) return '-\u221EdB';
  const db = 20 * Math.log10(volume);
  return `${db >= 0 ? '+' : ''}${db.toFixed(1)}dB`;
}

// ---------------------------------------------------------------------------
// Filter parameter editors
// ---------------------------------------------------------------------------

function VolumeParams({
  filter,
  onUpdate,
}: {
  filter: AudioFilter;
  onUpdate: (params: Record<string, string | number | boolean>) => void;
}) {
  const vol = Number(filter.params.volume) || 0;
  return (
    <div className="filter-params">
      <label>
        <input
          type="range"
          aria-label="Volume"
          min="0"
          max="3"
          step="0.1"
          value={vol}
          onChange={e => onUpdate({ ...filter.params, volume: Number(e.target.value) })}
        />
      </label>
      <span className="db-label">{volumeToDb(vol)}</span>
    </div>
  );
}

function LoudnormParams({
  filter,
  onUpdate,
}: {
  filter: AudioFilter;
  onUpdate: (params: Record<string, string | number | boolean>) => void;
}) {
  return (
    <div className="filter-params">
      <label>
        {'Target Loudness (I)'}
        <input
          type="number"
          aria-label="Target Loudness"
          value={Number(filter.params.I) || -24}
          onChange={e => onUpdate({ ...filter.params, I: Number(e.target.value) })}
        />
      </label>
      <label>
        {'LRA'}
        <input
          type="number"
          aria-label="LRA"
          value={Number(filter.params.LRA) || 7}
          onChange={e => onUpdate({ ...filter.params, LRA: Number(e.target.value) })}
        />
      </label>
      <label>
        {'True Peak (TP)'}
        <input
          type="number"
          aria-label="True Peak"
          value={Number(filter.params.TP) || -2}
          onChange={e => onUpdate({ ...filter.params, TP: Number(e.target.value) })}
        />
      </label>
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
  filter: AudioFilter;
  index: number;
  total: number;
  onUpdate: (filter: AudioFilter) => void;
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
      {filter.type === 'volume' && <VolumeParams filter={filter} onUpdate={handleParamsUpdate} />}
      {filter.type === 'loudnorm' && <LoudnormParams filter={filter} onUpdate={handleParamsUpdate} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// AudioFilters component
// ---------------------------------------------------------------------------

interface AudioFiltersProps {
  value: AudioFilter[];
  onChange: (filters: AudioFilter[]) => void;
}

export function AudioFilters({ value, onChange }: AudioFiltersProps) {
  const [filters, setFilters] = useState<AudioFilter[]>(value);
  const [showAddMenu, setShowAddMenu] = useState(false);

  useEffect(() => {
    setFilters(value);
  }, [value]);

  const emit = useCallback((next: AudioFilter[]) => {
    setFilters(next);
    onChange(next);
  }, [onChange]);

  const addFilter = (type: AudioFilterType) => {
    const opt = FILTER_OPTIONS.find(f => f.type === type);
    const newFilter: AudioFilter = {
      type,
      enabled: true,
      params: { ...(opt?.defaultParams || {}) },
      order: filters.length,
    };
    emit([...filters, newFilter]);
    setShowAddMenu(false);
  };

  const updateFilter = (index: number, updated: AudioFilter) => {
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

  return (
    <div className="audio-filters">
      <div data-testid="audio-filter-list" className="filter-list">
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
          data-testid="add-audio-filter"
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
    </div>
  );
}
