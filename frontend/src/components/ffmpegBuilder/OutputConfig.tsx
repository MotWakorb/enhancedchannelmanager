import { useState, useEffect, useCallback, useRef } from 'react';
import type { OutputConfig as OutputConfigType, ContainerFormat } from '../../types/ffmpegBuilder';

// ---------------------------------------------------------------------------
// Format metadata
// ---------------------------------------------------------------------------

interface FormatOption {
  value: ContainerFormat;
  label: string;
  description: string;
  extensions: string[];
}

const FORMAT_OPTIONS: FormatOption[] = [
  { value: 'ts', label: 'MPEG-TS', description: 'Transport stream for broadcasting and live IPTV streaming', extensions: ['.ts', '.mts'] },
  { value: 'hls', label: 'HLS', description: 'HTTP Live Streaming — adaptive chunked delivery', extensions: ['.m3u8'] },
  { value: 'dash', label: 'DASH', description: 'Dynamic Adaptive Streaming over HTTP', extensions: ['.mpd'] },
];

// ---------------------------------------------------------------------------
// Tooltips
// ---------------------------------------------------------------------------

const FIELD_TOOLTIPS: Record<string, string> = {
  outputPath: 'The output file path. The extension should match the selected format.',
  format: 'Container format determines how video, audio, and metadata streams are packaged together. Different formats have different codec compatibility and feature support.',
  segmentDuration: 'Duration of each HLS segment in seconds. Shorter segments allow faster quality switching but increase overhead.',
  playlistType: 'HLS playlist type. VOD for complete files, EVENT for growing playlists, or LIVE for sliding window.',
  segmentFilename: 'Pattern for segment filenames. Use %03d for zero-padded sequence numbers.',
};

const FIELD_HINTS: Record<string, string> = {
  outputPath: 'Use "pipe:1" for IPTV proxying (streams to stdout), or a path for HLS/DASH segments. Extension should match the format below.',
  format: 'How video, audio, and metadata are packaged together. MPEG-TS for live streaming/IPTV, HLS for adaptive web delivery, DASH for cross-platform streaming.',
  segmentDuration: 'Seconds per HLS chunk. Shorter (2\u20134s) means faster quality switching but more files. Longer (6\u201310s) means fewer files and less overhead.',
  playlistType: 'VOD creates a complete playlist when encoding finishes. Event keeps adding segments as they\u2019re created. Use VOD for recordings, Event for live.',
  segmentFilename: 'Pattern for naming segment files. %03d becomes 001, 002, 003, etc. Make sure the directory exists before encoding.',
};

// ---------------------------------------------------------------------------
// InfoIcon
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
// Dropdown
// ---------------------------------------------------------------------------

interface DropdownProps {
  label: string;
  options: { value: string; label: string }[];
  value: string;
  onChange: (value: string) => void;
  tooltip?: string;
  hint?: string;
}

function Dropdown({ label, options, value, onChange, tooltip, hint }: DropdownProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [open]);

  return (
    <div className="form-group" ref={ref}>
      <label>
        {label}
        {tooltip && <InfoIcon tooltip={tooltip} />}
      </label>
      <div
        role="combobox"
        aria-label={label}
        aria-expanded={open}
        className="dropdown-trigger"
        onClick={() => setOpen(!open)}
      >
        {options.find(o => o.value === value)?.label || value || 'Select...'}
      </div>
      {open && (
        <div role="listbox" className="dropdown-list">
          {options.map(opt => (
            <div
              key={opt.value}
              role="option"
              aria-label={opt.label}
              aria-selected={opt.value === value}
              className={`dropdown-option${opt.value === value ? ' selected' : ''}`}
              onClick={() => { onChange(opt.value); setOpen(false); }}
            >
              {opt.label}
            </div>
          ))}
        </div>
      )}
      {hint && <span className="field-hint">{hint}</span>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

function getFormatForExtension(path: string): string | null {
  const ext = path.split('.').pop()?.toLowerCase();
  for (const fmt of FORMAT_OPTIONS) {
    if (fmt.extensions.some(e => e === `.${ext}`)) return fmt.value;
  }
  return null;
}

function getValidation(output: OutputConfigType): { error: string | null; warning: string | null } {
  if (!output.path) {
    return { error: 'Output path is required', warning: null };
  }

  const detectedFormat = getFormatForExtension(output.path);
  if (detectedFormat && output.format && detectedFormat !== output.format) {
    return {
      error: null,
      warning: `File extension does not match the selected format (${output.format})`,
    };
  }

  return { error: null, warning: null };
}

// ---------------------------------------------------------------------------
// OutputConfig component
// ---------------------------------------------------------------------------

interface OutputConfigProps {
  value: OutputConfigType;
  onChange: (value: OutputConfigType) => void;
}

export function OutputConfig({ value, onChange }: OutputConfigProps) {
  const [output, setOutput] = useState<OutputConfigType>(value);

  useEffect(() => {
    setOutput(value);
  }, [value]);

  const emit = useCallback((next: OutputConfigType) => {
    setOutput(next);
    onChange(next);
  }, [onChange]);

  const handlePathChange = (path: string) => {
    emit({ ...output, path });
  };

  const handleFormatChange = (format: string) => {
    emit({ ...output, format: format as ContainerFormat });
  };

  const handleHLSOption = (key: string, val: string) => {
    emit({
      ...output,
      options: { ...(output.options || {}), [key]: val },
    });
  };

  const validation = getValidation(output);
  const currentFormat = output.format || 'ts';
  const formatInfo = FORMAT_OPTIONS.find(f => f.value === currentFormat);
  const isHLS = currentFormat === 'hls';

  return (
    <div className="output-config">
      {/* Output Path */}
      <div className="form-group">
        <label htmlFor="output-path">
          Output Path
          <InfoIcon tooltip={FIELD_TOOLTIPS.outputPath} />
        </label>
        <input
          id="output-path"
          type="text"
          aria-label="Output Path"
          value={output.path}
          onChange={e => handlePathChange(e.target.value)}
        />
        {validation.error && <div className="validation-error">{validation.error}</div>}
        {validation.warning && <div className="validation-warning">{validation.warning}</div>}
        <span className="field-hint">{FIELD_HINTS.outputPath}</span>
      </div>

      {/* Format */}
      <div className="output-config-field">
        <Dropdown
          label="Format"
          options={FORMAT_OPTIONS.map(f => ({ value: f.value, label: f.label }))}
          value={currentFormat}
          onChange={handleFormatChange}
          tooltip={FIELD_TOOLTIPS.format}
          hint={FIELD_HINTS.format}
        />
        {formatInfo && <div className="format-description">{formatInfo.description}</div>}
      </div>

      {/* HLS Options */}
      {isHLS && (
        <>
          <div className="form-group">
            <span className="field-label-text">
              Segment Duration
              <InfoIcon tooltip={FIELD_TOOLTIPS.segmentDuration} />
            </span>
            <input
              type="number"
              aria-label="Segment Duration"
              value={output.options?.hls_time || '6'}
              onChange={e => handleHLSOption('hls_time', e.target.value)}
            />
            <span className="field-hint">{FIELD_HINTS.segmentDuration}</span>
          </div>

          <div className="form-group">
            <Dropdown
              label="Playlist Type"
              options={[
                { value: 'vod', label: 'VOD' },
                { value: 'event', label: 'Event' },
              ]}
              value={output.options?.hls_playlist_type || 'vod'}
              onChange={v => handleHLSOption('hls_playlist_type', v)}
              hint={FIELD_HINTS.playlistType}
            />
          </div>

          <div className="form-group">
            <span className="field-label-text">
              Segment Filename
              <InfoIcon tooltip={FIELD_TOOLTIPS.segmentFilename} />
            </span>
            <input
              type="text"
              aria-label="Segment Filename Pattern"
              value={output.options?.hls_segment_filename || 'segment_%03d.ts'}
              onChange={e => handleHLSOption('hls_segment_filename', e.target.value)}
            />
            <span className="field-hint">{FIELD_HINTS.segmentFilename}</span>
          </div>
        </>
      )}
    </div>
  );
}
