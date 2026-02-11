import { useState, useEffect, useCallback, useRef } from 'react';
import type { InputSource, InputSourceType, HWAccelAPI, HWAccelConfig } from '../../types/ffmpegBuilder';

// ---------------------------------------------------------------------------
// Metadata
// ---------------------------------------------------------------------------

const INPUT_TYPES: { value: InputSourceType; label: string }[] = [
  { value: 'url', label: 'URL' },
  { value: 'pipe', label: 'Pipe' },
];

const HWACCEL_OPTIONS: { value: HWAccelAPI; label: string }[] = [
  { value: 'none', label: 'None' },
  { value: 'cuda', label: 'CUDA' },
  { value: 'qsv', label: 'QSV' },
  { value: 'vaapi', label: 'VAAPI' },
];

const FORMAT_OPTIONS = [
  { value: '', label: 'Auto-detect' },
  { value: 'mpegts', label: 'MPEGTS' },
  { value: 'hls', label: 'HLS' },
  { value: 'mp4', label: 'MP4' },
  { value: 'matroska', label: 'Matroska' },
  { value: 'flv', label: 'FLV' },
];

// ---------------------------------------------------------------------------
// Tooltips
// ---------------------------------------------------------------------------

const FIELD_TOOLTIPS: Record<string, string> = {
  inputType: 'Choose the input source type: network URL for streams, or pipe from another process.',
  path: 'The URL of the input stream. Use the complete URL including the protocol.',
  format: 'Force the input demuxer format. Use auto-detect unless you know the format differs from what ffmpeg would guess from the file extension.',
  hwaccel: 'Hardware acceleration offloads decoding to your GPU, dramatically speeding up the pipeline. Requires compatible hardware and drivers.',
  device: 'The hardware device path for VAAPI acceleration, e.g., /dev/dri/renderD128. Required for VAAPI decoding.',
  outputFormat: 'The output format for hardware-accelerated decoding. Keeps decoded frames in GPU memory for faster pipeline.',
};

const FIELD_HINTS: Record<string, string> = {
  inputType: 'Where FFmpeg reads from. "URL" for network streams (HTTP, RTMP, UDP), "Pipe" for input from another program.',
  path: 'For IPTV, this is typically the stream URL from your M3U playlist. Use {streamUrl} as a placeholder for Dispatcharr to substitute at runtime.',
  format: 'Usually "Auto-detect" works fine. Only override if FFmpeg misidentifies the stream format, which is rare.',
  hwaccel: 'Offloads video decoding to your GPU, freeing up CPU. CUDA for NVIDIA GPUs, QSV for Intel, VAAPI for AMD or Intel (if QSV unavailable). Requires compatible hardware and drivers.',
  device: '/dev/dri/renderD128 is the default on most Linux systems. Check with ls /dev/dri/ if unsure.',
  outputFormat: 'Keeps decoded frames in GPU memory instead of copying back to system RAM. Essential for GPU-accelerated encoding pipelines.',
};

function getPathLabel(type: InputSourceType): string {
  switch (type) {
    case 'url': return 'URL';
    case 'pipe': return 'Pipe Input';
    default: return 'Path';
  }
}

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
  const selectedLabel = options.find(o => o.value === value)?.label || value || 'Select...';

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
        aria-valuetext={selectedLabel}
        className="dropdown-trigger"
        title={selectedLabel}
        onClick={() => setOpen(!open)}
      >
        {selectedLabel}
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

function validateInput(input: InputSource): string | null {
  if (input.type === 'url' && input.path && !input.path.match(/^(https?:\/\/.+|\{.+\})/i)) {
    return 'Please enter a valid URL (must start with http:// or https://)';
  }
  return null;
}

// ---------------------------------------------------------------------------
// InputSourceConfig
// ---------------------------------------------------------------------------

interface InputSourceConfigProps {
  value: InputSource;
  onChange: (value: InputSource) => void;
}

export function InputSourceConfig({ value, onChange }: InputSourceConfigProps) {
  const [input, setInput] = useState<InputSource>(value);

  useEffect(() => {
    setInput(value);
  }, [value]);

  const emit = useCallback((next: InputSource) => {
    setInput(next);
    onChange(next);
  }, [onChange]);

  const handleTypeChange = (type: string) => {
    emit({
      type: type as InputSourceType,
      path: '',
      startTime: undefined,
      duration: undefined,
      streamLoop: undefined,
    });
  };

  const handlePathChange = (path: string) => {
    emit({ ...input, path });
  };

  const handleFormatChange = (format: string) => {
    emit({ ...input, format: format || undefined });
  };

  const handleHWAccelChange = (api: string) => {
    if (api === 'none') {
      const { hwaccel, ...rest } = input;
      emit(rest as InputSource);
    } else {
      const hwaccel: HWAccelConfig = { api: api as HWAccelAPI };
      if (api === 'vaapi') hwaccel.device = '/dev/dri/renderD128';
      if (api === 'cuda') hwaccel.outputFormat = 'cuda';
      emit({ ...input, hwaccel });
    }
  };

  const handleDeviceChange = (device: string) => {
    emit({ ...input, hwaccel: { ...input.hwaccel!, device } });
  };

  const handleOutputFormatChange = (outputFormat: string) => {
    emit({ ...input, hwaccel: { ...input.hwaccel!, outputFormat } });
  };

  const validationError = validateInput(input);
  const currentHWAccel = input.hwaccel?.api || 'none';
  const pathLabel = getPathLabel(input.type);

  return (
    <div className="input-source-config">
      {/* Input Type */}
      <Dropdown
        label="Input Type"
        options={INPUT_TYPES.map(t => ({ value: t.value, label: t.label }))}
        value={input.type}
        onChange={handleTypeChange}
        tooltip={FIELD_TOOLTIPS.inputType}
        hint={FIELD_HINTS.inputType}
      />

      {/* Path */}
      <div className="form-group">
        <label>
          {pathLabel}
          <InfoIcon tooltip={FIELD_TOOLTIPS.path} />
        </label>
        {input.type === 'pipe' ? (
          <div data-testid="input-path" aria-label="Pipe Input">pipe:0</div>
        ) : (
          <input
            data-testid="input-path"
            name="inputPath"
            type="text"
            aria-label={pathLabel}
            value={input.path}
            onChange={e => handlePathChange(e.target.value)}
          />
        )}
        {validationError && <div className="validation-error">{validationError}</div>}
        <span className="field-hint">{FIELD_HINTS.path}</span>
      </div>

      {/* Format */}
      <Dropdown
        label="Format"
        options={FORMAT_OPTIONS}
        value={input.format || ''}
        onChange={handleFormatChange}
        tooltip={FIELD_TOOLTIPS.format}
        hint={FIELD_HINTS.format}
      />

      {/* Hardware Acceleration */}
      <Dropdown
        label="Hardware Acceleration"
        options={HWACCEL_OPTIONS.map(h => ({ value: h.value, label: h.label }))}
        value={currentHWAccel}
        onChange={handleHWAccelChange}
        tooltip={FIELD_TOOLTIPS.hwaccel}
        hint={FIELD_HINTS.hwaccel}
      />

      {/* VAAPI device */}
      {input.hwaccel?.api === 'vaapi' && (
        <div className="form-group">
          <label htmlFor="hwaccel-device">
            Device
            <InfoIcon tooltip={FIELD_TOOLTIPS.device} />
          </label>
          <input
            id="hwaccel-device"
            type="text"
            aria-label="Device"
            value={input.hwaccel.device || ''}
            onChange={e => handleDeviceChange(e.target.value)}
          />
          <span className="field-hint">{FIELD_HINTS.device}</span>
        </div>
      )}

      {/* CUDA output format */}
      {input.hwaccel?.api === 'cuda' && (
        <div className="form-group">
          <label htmlFor="hwaccel-output-format">
            Output Format
            <InfoIcon tooltip={FIELD_TOOLTIPS.outputFormat} />
          </label>
          <input
            id="hwaccel-output-format"
            type="text"
            aria-label="Output Format"
            value={input.hwaccel.outputFormat || ''}
            onChange={e => handleOutputFormatChange(e.target.value)}
          />
          <span className="field-hint">{FIELD_HINTS.outputFormat}</span>
        </div>
      )}

    </div>
  );
}
