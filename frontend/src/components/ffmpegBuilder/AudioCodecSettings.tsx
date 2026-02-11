import { useState, useEffect, useCallback, useRef } from 'react';
import type { AudioCodecSettings as AudioCodecSettingsType } from '../../types/ffmpegBuilder';

// ---------------------------------------------------------------------------
// Codec metadata
// ---------------------------------------------------------------------------

interface CodecOption {
  value: string;
  label: string;
  description: string;
}

const AUDIO_CODECS: CodecOption[] = [
  { value: 'aac', label: 'AAC', description: 'Advanced Audio Coding — widely supported lossy codec for IPTV' },
  { value: 'ac3', label: 'AC3', description: 'Dolby Digital AC-3 — surround sound codec for broadcast' },
  { value: 'eac3', label: 'EAC3', description: 'Enhanced AC-3 (Dolby Digital Plus) — improved surround sound for streaming' },
  { value: 'copy', label: 'Copy', description: 'Stream copy — no re-encoding' },
];

const BITRATE_PRESETS = ['128k', '192k', '256k', '320k'];

const SAMPLE_RATES = [
  { value: 22050, label: '22050 Hz' },
  { value: 44100, label: '44100 Hz' },
  { value: 48000, label: '48000 Hz' },
  { value: 96000, label: '96000 Hz' },
];

const CHANNEL_OPTIONS = [
  { value: 1, label: 'Mono', layout: 'mono' },
  { value: 2, label: 'Stereo', layout: 'stereo' },
  { value: 6, label: '5.1 Surround', layout: '5.1' },
  { value: 8, label: '7.1 Surround', layout: '7.1' },
];

const BITRATE_REGEX = /^\d+[kKmM]?$/;

// ---------------------------------------------------------------------------
// Tooltip descriptions
// ---------------------------------------------------------------------------

function getCodecTooltip(codec: string): string {
  const c = AUDIO_CODECS.find(o => o.value === codec);
  return c?.description || 'Select an audio codec for encoding.';
}

const FIELD_HINTS: Record<string, string> = {
  codec: 'AAC is the most compatible choice for IPTV. AC3/EAC3 for surround sound broadcast content. "Copy" passes audio through unchanged (fastest, no quality loss).',
  bitrate: 'Audio quality in kilobits per second. 128k = good for speech/talk shows, 192k = standard for music and IPTV, 256k+ = high quality. Higher values = better audio but larger files.',
  sampleRate: '48000 Hz is standard for video content and IPTV. 44100 Hz is CD quality (fine for music-only). Higher rates preserve more detail but increase file size.',
  channels: 'Stereo (2 channels) is standard for most IPTV content. 5.1 Surround for movies and premium content. Mono for voice-only streams.',
};

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
// SettingSelect — inline dropdown with role="option" items
// ---------------------------------------------------------------------------

interface SelectOption { value: string; label: string; disabled?: boolean }

function SettingSelect({
  label,
  value,
  options,
  onChange,
  disabled,
}: {
  label: string;
  value: string;
  options: SelectOption[];
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const selected = options.find(o => o.value === value);

  useEffect(() => {
    if (!isOpen) return;
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setIsOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [isOpen]);

  return (
    <div ref={ref} style={{ display: 'contents' }}>
      <button
        type="button"
        aria-label={label}
        aria-expanded={isOpen}
        disabled={disabled}
        onClick={() => { if (!disabled) setIsOpen(!isOpen); }}
        className="setting-select-trigger"
      >
        {selected?.label || value || 'Select…'}
      </button>
      {isOpen && (
        <div role="listbox" className="setting-select-dropdown">
          {options.map(opt => (
            <div
              key={opt.value}
              role="option"
              aria-selected={opt.value === value}
              aria-disabled={opt.disabled ? 'true' : undefined}
              className={`setting-option${opt.disabled ? ' disabled' : ''}`}
              onClick={() => {
                if (!opt.disabled) {
                  onChange(opt.value);
                  setIsOpen(false);
                }
              }}
            >
              {opt.label}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// AudioCodecSettings component
// ---------------------------------------------------------------------------

interface AudioCodecSettingsProps {
  value: AudioCodecSettingsType;
  onChange: (settings: AudioCodecSettingsType) => void;
}

export function AudioCodecSettings({ value, onChange }: AudioCodecSettingsProps) {
  const [settings, setSettings] = useState<AudioCodecSettingsType>(value);
  const [bitrateError, setBitrateError] = useState<string>('');

  useEffect(() => {
    setSettings(value);
  }, [value]);

  const update = useCallback((patch: Partial<AudioCodecSettingsType>) => {
    const next = { ...settings, ...patch };
    setSettings(next);
    onChange(next);
  }, [settings, onChange]);

  const codec = settings.codec;
  const copyMode = codec === 'copy';
  const showBitrate = !copyMode;
  const codecMeta = AUDIO_CODECS.find(c => c.value === codec);

  const codecOptions: SelectOption[] = AUDIO_CODECS.map(c => ({
    value: c.value,
    label: c.label,
  }));

  const sampleRateOptions: SelectOption[] = SAMPLE_RATES.map(s => ({
    value: String(s.value),
    label: String(s.value),
  }));

  const channelOptions: SelectOption[] = CHANNEL_OPTIONS.map(c => ({
    value: String(c.value),
    label: `${c.label} (${c.layout})`,
  }));

  const channelMeta = CHANNEL_OPTIONS.find(c => c.value === settings.channels);

  return (
    <div className="audio-codec-settings">
      {/* Codec selector */}
      <div className="setting-row">
        <label>Audio Codec <InfoIcon tooltip={getCodecTooltip(codec)} /></label>
        <SettingSelect
          label="Audio Codec"
          value={codec}
          options={codecOptions}
          onChange={v => update({ codec: v as AudioCodecSettingsType['codec'] })}
        />
        <span className="field-hint">{FIELD_HINTS.codec}</span>
      </div>

      {/* Copy mode notice */}
      {copyMode && (
        <div className="copy-notice">{'Stream copy — no re-encoding'}</div>
      )}

      {/* Bitrate (lossy codecs only) */}
      {showBitrate && (
        <div className="setting-row">
          <label>Bitrate <InfoIcon tooltip="Higher bitrate produces better quality audio but larger file size. 192k is a good default for AAC." /></label>
          <input
            type="text"
            aria-label="Bitrate"
            value={settings.bitrate ?? ''}
            onChange={e => {
              const val = e.target.value;
              update({ bitrate: val });
              if (val && !BITRATE_REGEX.test(val)) {
                setBitrateError('Please enter a valid bitrate format (e.g., 192k)');
              } else {
                setBitrateError('');
              }
            }}
          />
          {bitrateError && <div className="field-error">{bitrateError}</div>}
          <div className="bitrate-presets">
            {BITRATE_PRESETS.map(bp => (
              <button
                key={bp}
                type="button"
                className="bitrate-preset-btn"
                onClick={() => {
                  update({ bitrate: bp });
                  setBitrateError('');
                }}
              >
                {bp}
              </button>
            ))}
          </div>
          <span className="field-hint">{FIELD_HINTS.bitrate}</span>
        </div>
      )}

      {/* Sample Rate — hidden in copy mode */}
      {!copyMode && (
        <div className="setting-row">
          <label>Sample Rate <InfoIcon tooltip="Audio sample rate in Hz. 48000 Hz is standard for video, 44100 Hz for music." /></label>
          <SettingSelect
            label="Sample Rate"
            value={String(settings.sampleRate ?? 48000)}
            options={sampleRateOptions}
            onChange={v => update({ sampleRate: Number(v) })}
          />
          <span className="field-hint">{FIELD_HINTS.sampleRate}</span>
        </div>
      )}

      {/* Channels — hidden in copy mode */}
      {!copyMode && (
        <div className="setting-row">
          <label>Channels <InfoIcon tooltip="Number of audio channels. Stereo (2) is most common; 5.1 for surround sound." /></label>
          <SettingSelect
            label="Channels"
            value={String(settings.channels ?? 2)}
            options={channelOptions}
            onChange={v => {
              const ch = Number(v);
              const meta = CHANNEL_OPTIONS.find(c => c.value === ch);
              update({ channels: ch, channelLayout: meta?.layout });
            }}
          />
          <span className="field-hint">{FIELD_HINTS.channels}</span>
        </div>
      )}
    </div>
  );
}
