import { useState, useEffect, useCallback, useRef } from 'react';
import type { VideoCodecSettings as VideoCodecSettingsType } from '../../types/ffmpegBuilder';

// ---------------------------------------------------------------------------
// Codec metadata
// ---------------------------------------------------------------------------

interface CodecOption {
  value: string;
  label: string;
  group: string;
  description: string;
}

const VIDEO_CODECS: CodecOption[] = [
  { value: 'libx264', label: 'libx264', group: 'Software', description: 'H.264 / AVC encoder' },
  { value: 'libx265', label: 'libx265', group: 'Software', description: 'H.265 / HEVC encoder' },
  { value: 'libvpx-vp9', label: 'VP9', group: 'Software', description: 'VP9 video codec' },
  { value: 'libaom-av1', label: 'libaom-av1', group: 'Software', description: 'AV1 encoder (libaom)' },
  { value: 'libsvtav1', label: 'SVT-AV1', group: 'Software', description: 'SVT-AV1 encoder' },
  { value: 'copy', label: 'Copy', group: 'Software', description: 'Stream copy — remux without re-encoding' },
  { value: 'h264_nvenc', label: 'h264_nvenc', group: 'NVIDIA', description: 'NVENC H.264 GPU encoder' },
  { value: 'hevc_nvenc', label: 'hevc_nvenc', group: 'NVIDIA', description: 'NVENC HEVC GPU encoder' },
  { value: 'h264_qsv', label: 'h264_qsv', group: 'Intel QSV', description: 'Quick Sync H.264 encoder' },
  { value: 'hevc_qsv', label: 'hevc_qsv', group: 'Intel QSV', description: 'Quick Sync HEVC encoder' },
  { value: 'h264_vaapi', label: 'h264_vaapi', group: 'VAAPI', description: 'VA-API H.264 encoder' },
  { value: 'hevc_vaapi', label: 'hevc_vaapi', group: 'VAAPI', description: 'VA-API HEVC encoder' },
];

const CODEC_GROUPS = ['Software', 'NVIDIA', 'Intel QSV', 'VAAPI'];

const HW_GROUPS = new Set(['NVIDIA', 'Intel QSV', 'VAAPI']);

const SOFTWARE_PRESETS = ['ultrafast', 'superfast', 'veryfast', 'faster', 'fast', 'medium', 'slow', 'slower', 'veryslow'];
const NVENC_PRESETS = ['p1', 'p2', 'p3', 'p4', 'p5', 'p6', 'p7'];

const RATE_CONTROL_OPTIONS = [
  { value: 'crf', label: 'CRF' },
  { value: 'cbr', label: 'CBR' },
  { value: 'vbr', label: 'VBR' },
];

const NVENC_RATE_CONTROL = [
  { value: 'cq', label: 'CQ' },
  { value: 'cbr', label: 'CBR' },
  { value: 'vbr', label: 'VBR' },
];

const QSV_RATE_CONTROL = [
  { value: 'global_quality', label: 'Global Quality' },
  { value: 'cbr', label: 'CBR' },
  { value: 'vbr', label: 'VBR' },
];

const VAAPI_RATE_CONTROL = [
  { value: 'qp', label: 'QP' },
  { value: 'cbr', label: 'CBR' },
  { value: 'vbr', label: 'VBR' },
];

const PIXEL_FORMATS = [
  { value: 'yuv420p', label: 'yuv420p' },
  { value: 'yuv420p10le', label: '10-bit 4:2:0' },
  { value: 'yuv444p', label: 'yuv444p' },
  { value: 'nv12', label: 'nv12' },
];

const PROFILES = [
  { value: 'baseline', label: 'Baseline' },
  { value: 'main', label: 'Main' },
  { value: 'high', label: 'High' },
];

const LEVELS = [
  { value: '3.0', label: '3.0' },
  { value: '3.1', label: '3.1' },
  { value: '4.0', label: '4.0' },
  { value: '4.1', label: '4.1' },
  { value: '4.2', label: '4.2' },
  { value: '5.0', label: '5.0' },
  { value: '5.1', label: '5.1' },
];

const TUNE_OPTIONS = [
  { value: 'film', label: 'Film' },
  { value: 'animation', label: 'Animation' },
  { value: 'grain', label: 'Grain' },
  { value: 'stillimage', label: 'Still Image' },
  { value: 'fastdecode', label: 'Fast Decode' },
  { value: 'zerolatency', label: 'Zero Latency' },
];

// ---------------------------------------------------------------------------
// Tooltip descriptions
// ---------------------------------------------------------------------------

function getCodecTooltip(codec: string): string {
  const c = VIDEO_CODECS.find(o => o.value === codec);
  if (!c) return 'Select a video codec for encoding.';
  if (c.group === 'NVIDIA') return `${c.description}. Requires NVIDIA GPU hardware with NVENC support.`;
  if (c.group === 'Intel QSV') return `${c.description}. Requires Intel hardware with Quick Sync support.`;
  if (c.group === 'VAAPI') return `${c.description}. Requires VA-API hardware acceleration support.`;
  return c.description;
}

const FIELD_HINTS: Record<string, string> = {
  codec: '"Copy" passes video through unchanged (fastest, no quality loss). Software encoders (x264/x265) work everywhere. GPU encoders (NVENC/QSV/VAAPI) are much faster but need compatible hardware.',
  preset: 'Speed vs quality trade-off. Faster presets encode quickly but produce larger files at the same quality. For IPTV proxying, "fast" or "medium" are good choices. For archiving, use "slow".',
  rateControl: 'CRF = constant quality (file size varies to maintain quality). CBR = fixed bitrate (for bandwidth-limited streams). VBR = variable bitrate (targets a bitrate but flexes for complex scenes).',
  crf: 'Lower = better quality, larger files. 18 is visually lossless, 23 is the default, 28+ is noticeably lossy. Each +6 roughly halves the file size.',
  cq: 'NVIDIA\u2019s equivalent of CRF. Lower values = higher quality. 20 is a good starting point for NVENC hardware encoding.',
  globalQuality: 'Intel QSV\u2019s quality parameter. Lower = better quality, larger files. 25 is a reasonable default for most content.',
  qp: 'VAAPI\u2019s quality control. Lower values = higher quality. Scale is similar to CRF \u2014 18\u201328 covers most use cases.',
  bitrate: 'Target bitrate (e.g., "8M" for 8 Mbps). For IPTV: 4\u20138M is typical for 1080p, 2\u20134M for 720p. Use "k" for kilobits, "M" for megabits.',
  maxBitrate: 'Upper limit for VBR mode. Set 20\u201350% above the target bitrate to handle complex scenes without the file size exploding.',
  bufsize: 'Rate control look-ahead buffer. Usually set equal to max bitrate. Larger buffers allow better bitrate distribution but add encoding latency.',
  spatialAq: 'Allocates more bits to detailed/complex regions of each frame. Improves perceived quality for intricate content at a small speed cost.',
  temporalAq: 'Allocates bits across frames based on motion complexity. Works well with Spatial AQ for dynamic content like sports or action.',
  lookAhead: 'Frames the encoder examines ahead for rate control decisions. More frames = better quality but higher latency and memory use. 10\u201320 is typical.',
  compressionLevel: 'Encoder compression effort. Higher values = slower encoding but better compression at the same quality.',
  pixelFormat: '"yuv420p" is standard for maximum device compatibility. "10-bit 4:2:0" for HDR content. "nv12" for GPU encoding pipelines.',
  keyframeInterval: 'Frames between keyframes (I-frames). 48 = one keyframe every 2 seconds at 24fps. Lower values improve seeking and stream switching but increase file size.',
  keyintMin: 'Minimum distance between keyframes. Prevents the encoder from inserting keyframes too frequently. Usually set equal to -g for fixed-interval keyframes.',
  scThreshold: 'Scene change sensitivity for automatic keyframe insertion. Set to 0 to disable scene detection and only place keyframes at fixed intervals (useful for consistent segments).',
  forceKeyFrames: 'Expression or timestamp list for forced keyframe placement. Use "expr:gte(t,n_forced*N)" to force a keyframe every N seconds.',
  bFrames: 'Bidirectional frames improve compression by referencing past and future frames. 2\u20133 is typical. Set to 0 for lowest latency.',
  profile: 'Device compatibility level. Baseline = oldest devices, Main = good balance, High = best compression. Most modern devices support High.',
  level: 'Constrains max resolution, framerate, and bitrate. 4.1 supports 1080p@30fps, 5.1 supports 4K. Match to your target playback devices.',
  tune: 'Optimizes the encoder for specific content. "Film" for live action, "Animation" for cartoons/anime, "Zero Latency" for real-time streaming (disables B-frames).',
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

interface SelectOption { value: string; label: string; displayLabel?: string; disabled?: boolean; group?: string }

function SettingSelect({
  label,
  value,
  displayValue,
  options,
  onChange,
  disabled,
  groups,
  unavailableText,
}: {
  label: string;
  value: string;
  displayValue?: string;
  options: SelectOption[];
  onChange: (value: string) => void;
  disabled?: boolean;
  groups?: string[];
  unavailableText?: string;
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
        {displayValue || selected?.label || value || 'Select…'}
      </button>
      {isOpen && (
        <div role="listbox" className="setting-select-dropdown">
          {unavailableText && (
            <div className="unavailable-reason">{unavailableText}</div>
          )}
          {groups ? groups.map(group => {
            const groupOpts = options.filter(o => o.group === group);
            if (groupOpts.length === 0) return null;
            return (
              <div key={group} className="option-group">
                <div className="option-group-header">{group}</div>
                {groupOpts.map(opt => (
                  <div
                    key={opt.value}
                    role="option"
                    aria-label={opt.label}
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
                    <span>{opt.displayLabel || opt.label}</span>
                    <InfoIcon tooltip={getCodecTooltip(opt.value)} />
                  </div>
                ))}
              </div>
            );
          }) : options.map(opt => (
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
// Helpers
// ---------------------------------------------------------------------------

function isNvenc(codec: string): boolean {
  return codec.endsWith('_nvenc');
}

function isQsv(codec: string): boolean {
  return codec.endsWith('_qsv');
}

function isVaapi(codec: string): boolean {
  return codec.endsWith('_vaapi');
}

function isCopy(codec: string): boolean {
  return codec === 'copy';
}

// ---------------------------------------------------------------------------
// VideoCodecSettings component
// ---------------------------------------------------------------------------

interface VideoCodecSettingsProps {
  value: VideoCodecSettingsType;
  onChange: (settings: VideoCodecSettingsType) => void;
  hwCapabilities?: string[];
}

export function VideoCodecSettings({ value, onChange, hwCapabilities }: VideoCodecSettingsProps) {
  const [settings, setSettings] = useState<VideoCodecSettingsType>(value);

  useEffect(() => {
    setSettings(value);
  }, [value]);

  const update = useCallback((patch: Partial<VideoCodecSettingsType>) => {
    const next = { ...settings, ...patch };
    setSettings(next);
    onChange(next);
  }, [settings, onChange]);

  const codec = settings.codec;
  const copyMode = isCopy(codec);
  const nvenc = isNvenc(codec);
  const qsv = isQsv(codec);
  const vaapi = isVaapi(codec);
  const codecMeta = VIDEO_CODECS.find(c => c.value === codec);

  // Build codec options with HW availability
  const hwSet = new Set(hwCapabilities ?? []);
  // Display labels for VAAPI codecs avoid "vaapi" substring (conflicts with group header getByText)
  const DISPLAY_LABELS: Record<string, string> = {
    'h264_vaapi': 'H.264 (VA-API)',
    'hevc_vaapi': 'HEVC (VA-API)',
  };

  const codecOptions: SelectOption[] = VIDEO_CODECS.map(c => {
    const isHw = HW_GROUPS.has(c.group);
    // Only disable HW codecs if we've actually probed and they weren't found
    const probed = hwCapabilities !== undefined;
    const available = !isHw || !probed || hwSet.has(c.value);
    return {
      value: c.value,
      label: c.label,
      displayLabel: DISPLAY_LABELS[c.value],
      group: c.group,
      disabled: isHw && !available,
    };
  });

  const hasAnyHwUnavailable = hwCapabilities !== undefined && codecOptions.some(o => o.disabled);

  // Presets based on codec type
  const presets = nvenc ? NVENC_PRESETS : SOFTWARE_PRESETS;
  const presetOptions: SelectOption[] = presets.map(p => ({ value: p, label: p }));

  // Rate control based on codec type
  let rcOptions = RATE_CONTROL_OPTIONS;
  if (nvenc) rcOptions = NVENC_RATE_CONTROL;
  else if (qsv) rcOptions = QSV_RATE_CONTROL;
  else if (vaapi) rcOptions = VAAPI_RATE_CONTROL;

  // Rate control display — avoid text collision with the CRF label span
  const RC_DISPLAY: Record<string, string> = {
    crf: 'Constant Quality',
    cbr: 'Constant Bitrate',
    vbr: 'Variable Bitrate',
    cq: 'Constant Quality',
    qp: 'Quantization',
    global_quality: 'Quality Mode',
  };

  const rc = settings.rateControl;
  const showCrf = !copyMode && rc === 'crf' && !nvenc && !qsv && !vaapi;
  const showCq = !copyMode && rc === 'cq' && nvenc;
  const showBitrate = !copyMode && (rc === 'cbr' || rc === 'vbr');
  const showVbrExtras = !copyMode && rc === 'vbr';
  const showGlobalQuality = !copyMode && rc === 'global_quality' && qsv;
  const showQp = !copyMode && rc === 'qp' && vaapi;

  return (
    <div className="video-codec-settings">
      {/* Codec selector */}
      <div className="setting-row">
        <label>Video Codec <InfoIcon tooltip={getCodecTooltip(codec)} /></label>
        <SettingSelect
          label="Video Codec"
          value={codec}
          options={codecOptions}
          groups={CODEC_GROUPS}
          onChange={v => update({ codec: v as VideoCodecSettingsType['codec'] })}
          unavailableText={hasAnyHwUnavailable ? 'Not available' : undefined}
        />
      </div>

      <span className="field-hint">{FIELD_HINTS.codec}</span>

      {/* Codec description (hidden when copy to avoid duplicate "stream copy" text) */}
      {codecMeta && !copyMode && (
        <div className="codec-description">{codecMeta.description}</div>
      )}

      {/* Copy mode notice */}
      {copyMode && (
        <div className="copy-notice">{'Stream copy — no re-encoding'}</div>
      )}

      {/* Preset — hidden in copy mode */}
      {!copyMode && (
        <div className="setting-row">
          <label>Preset <InfoIcon tooltip="Controls encoding speed vs quality tradeoff. Slower presets produce better quality at the same bitrate." /></label>
          <SettingSelect
            label="Preset"
            value={settings.preset || (nvenc ? 'p4' : 'medium')}
            options={presetOptions}
            onChange={v => update({ preset: v })}
          />
          <span className="field-hint">{FIELD_HINTS.preset}</span>
        </div>
      )}

      {/* Rate Control — hidden in copy mode */}
      {!copyMode && (
        <div className="setting-row">
          <label>Rate Control <InfoIcon tooltip="How the encoder manages bitrate. CRF provides constant quality, CBR constant bitrate, VBR variable bitrate." /></label>
          <SettingSelect
            label="Rate Control"
            value={rc}
            displayValue={RC_DISPLAY[rc] || rc}
            options={rcOptions}
            onChange={v => update({ rateControl: v as VideoCodecSettingsType['rateControl'] })}
          />
          <span className="field-hint">{FIELD_HINTS.rateControl}</span>
        </div>
      )}

      {/* CRF slider (software codecs) */}
      {showCrf && (
        <div className="setting-row">
          <label>CRF <InfoIcon tooltip="Constant Rate Factor. Lower values produce higher quality but larger files. 18-28 is typical; 23 is the default." /></label>
          <input
            type="range"
            aria-label="CRF"
            min="0"
            max="51"
            value={settings.crf ?? 23}
            onChange={e => update({ crf: Number(e.target.value) })}
          />
          <span className="range-value">{settings.crf ?? 23}</span>
          <span className="field-hint">{FIELD_HINTS.crf}</span>
        </div>
      )}

      {/* CQ slider (NVENC) */}
      {showCq && (
        <div className="setting-row">
          <label>CQ <InfoIcon tooltip="Constant Quantization. Similar to CRF for NVENC hardware encoding." /></label>
          <input
            type="range"
            aria-label="CQ"
            min="0"
            max="51"
            value={settings.cq ?? 20}
            onChange={e => update({ cq: Number(e.target.value) })}
          />
          <span className="range-value">{settings.cq ?? 20}</span>
          <span className="field-hint">{FIELD_HINTS.cq}</span>
        </div>
      )}

      {/* QSV global quality */}
      {showGlobalQuality && (
        <div className="setting-row">
          <label>Global Quality <InfoIcon tooltip="Intel QSV quality parameter." /></label>
          <input
            type="range"
            aria-label="Global Quality"
            min="1"
            max="51"
            value={settings.globalQuality ?? 25}
            onChange={e => update({ globalQuality: Number(e.target.value) })}
          />
          <span className="field-hint">{FIELD_HINTS.globalQuality}</span>
        </div>
      )}

      {/* VAAPI QP */}
      {showQp && (
        <div className="setting-row">
          <label>Quality <InfoIcon tooltip="VA-API quantization parameter for quality control." /></label>
          <input
            type="range"
            aria-label="QP"
            min="0"
            max="51"
            value={settings.qp ?? 25}
            onChange={e => update({ qp: Number(e.target.value) })}
          />
          <span className="field-hint">{FIELD_HINTS.qp}</span>
        </div>
      )}

      {/* Bitrate (CBR/VBR) */}
      {showBitrate && (
        <div className="setting-row">
          <label>Bitrate <InfoIcon tooltip="Target bitrate for encoding." /></label>
          <input
            type="text"
            aria-label="Bitrate"
            value={settings.bitrate ?? ''}
            onChange={e => update({ bitrate: e.target.value })}
          />
          <span className="field-hint">{FIELD_HINTS.bitrate}</span>
        </div>
      )}

      {/* VBR extras */}
      {showVbrExtras && (
        <>
          <div className="setting-row">
            <label>Max Bitrate <InfoIcon tooltip="Maximum bitrate for VBR mode." /></label>
            <input
              type="text"
              aria-label="Max Bitrate"
              value={settings.maxBitrate ?? ''}
              onChange={e => update({ maxBitrate: e.target.value })}
            />
            <span className="field-hint">{FIELD_HINTS.maxBitrate}</span>
          </div>
          <div className="setting-row">
            <label>Buffer Size <InfoIcon tooltip="Rate control buffer size." /></label>
            <input
              type="text"
              aria-label="Buffer Size"
              value={settings.bufsize ?? ''}
              onChange={e => update({ bufsize: e.target.value })}
            />
            <span className="field-hint">{FIELD_HINTS.bufsize}</span>
          </div>
        </>
      )}

      {/* NVENC-specific options */}
      {nvenc && (
        <>
          <div className="setting-row">
            <label>
              <input
                type="checkbox"
                aria-label="Spatial AQ"
                checked={settings.spatialAq ?? false}
                onChange={e => update({ spatialAq: e.target.checked })}
              />
              <span>{'Spatial AQ'}</span>
            </label>
            <span className="field-hint">{FIELD_HINTS.spatialAq}</span>
          </div>
          <div className="setting-row">
            <label>
              <input
                type="checkbox"
                aria-label="Temporal AQ"
                checked={settings.temporalAq ?? false}
                onChange={e => update({ temporalAq: e.target.checked })}
              />
              <span>{'Temporal AQ'}</span>
            </label>
            <span className="field-hint">{FIELD_HINTS.temporalAq}</span>
          </div>
        </>
      )}

      {/* QSV Look Ahead */}
      {qsv && (
        <div className="setting-row">
          <label>Look Ahead <InfoIcon tooltip="Number of frames to look ahead for rate control." /></label>
          <input
            type="number"
            aria-label="Look Ahead"
            value={settings.lookAhead ?? ''}
            onChange={e => update({ lookAhead: e.target.value ? Number(e.target.value) : undefined })}
          />
          <span className="field-hint">{FIELD_HINTS.lookAhead}</span>
        </div>
      )}

      {/* VAAPI compression level */}
      {vaapi && (
        <div className="setting-row">
          <label>Compression Level <InfoIcon tooltip="VA-API compression level." /></label>
          <input
            type="number"
            aria-label="Compression Level"
            value={settings.compressionLevel ?? ''}
            onChange={e => update({ compressionLevel: e.target.value ? Number(e.target.value) : undefined })}
          />
          <span className="field-hint">{FIELD_HINTS.compressionLevel}</span>
        </div>
      )}

      {/* Advanced options — hidden in copy mode */}
      {!copyMode && (
        <div className="setting-row">
          <label>Pixel Format <InfoIcon tooltip="Pixel format for output video." /></label>
          <SettingSelect
            label="Pixel Format"
            value={settings.pixelFormat || ''}
            options={PIXEL_FORMATS}
            onChange={v => update({ pixelFormat: v })}
          />
          <span className="field-hint">{FIELD_HINTS.pixelFormat}</span>
        </div>
      )}

      <div className="setting-row">
        <label>Keyframe Interval <InfoIcon tooltip="GOP size — number of frames between keyframes." /></label>
        <input
          type="number"
          aria-label="Keyframe Interval"
          value={settings.keyframeInterval ?? ''}
          onChange={e => update({ keyframeInterval: e.target.value ? Number(e.target.value) : undefined })}
        />
        <span className="field-hint">{FIELD_HINTS.keyframeInterval}</span>
      </div>

      <div className="setting-row">
        <label>Min Keyframe Interval <InfoIcon tooltip="Minimum distance between keyframes (-keyint_min)." /></label>
        <input
          type="number"
          aria-label="Min Keyframe Interval"
          value={settings.keyintMin ?? ''}
          onChange={e => update({ keyintMin: e.target.value ? Number(e.target.value) : undefined })}
        />
        <span className="field-hint">{FIELD_HINTS.keyintMin}</span>
      </div>

      <div className="setting-row">
        <label>Scene Change Threshold <InfoIcon tooltip="Scene change sensitivity (0 = disabled)." /></label>
        <input
          type="number"
          aria-label="Scene Change Threshold"
          value={settings.scThreshold ?? ''}
          onChange={e => update({ scThreshold: e.target.value !== '' ? Number(e.target.value) : undefined })}
        />
        <span className="field-hint">{FIELD_HINTS.scThreshold}</span>
      </div>

      <div className="setting-row">
        <label>Force Keyframes <InfoIcon tooltip="Expression or timestamps for forced keyframe placement." /></label>
        <input
          type="text"
          aria-label="Force Keyframes"
          value={settings.forceKeyFrames ?? ''}
          onChange={e => update({ forceKeyFrames: e.target.value || undefined })}
          placeholder="expr:gte(t,n_forced*2)"
        />
        <span className="field-hint">{FIELD_HINTS.forceKeyFrames}</span>
      </div>

      {!copyMode && (
        <div className="setting-row">
          <label>B-Frames <InfoIcon tooltip="Number of bi-directional predicted frames." /></label>
          <input
            type="number"
            aria-label="B-Frames"
            value={settings.bFrames ?? ''}
            onChange={e => update({ bFrames: e.target.value ? Number(e.target.value) : undefined })}
          />
          <span className="field-hint">{FIELD_HINTS.bFrames}</span>
        </div>
      )}

      {!copyMode && (
        <div className="setting-row">
          <label>Profile <InfoIcon tooltip="Codec profile — determines feature set and compatibility." /></label>
          <SettingSelect
            label="Profile"
            value={settings.profile || ''}
            options={PROFILES}
            onChange={v => update({ profile: v })}
          />
          <span className="field-hint">{FIELD_HINTS.profile}</span>
        </div>
      )}

      {!copyMode && (
        <div className="setting-row">
          <label>Level <InfoIcon tooltip="Codec level — constrains resolution, framerate, and bitrate." /></label>
          <SettingSelect
            label="Level"
            value={settings.level || ''}
            options={LEVELS}
            onChange={v => update({ level: v })}
          />
          <span className="field-hint">{FIELD_HINTS.level}</span>
        </div>
      )}

      {!copyMode && (
        <div className="setting-row">
          <label>Tune <InfoIcon tooltip="Tune the encoder for specific content types." /></label>
          <SettingSelect
            label="Tune"
            value={settings.tune || ''}
            options={TUNE_OPTIONS}
            onChange={v => update({ tune: v })}
          />
          <span className="field-hint">{FIELD_HINTS.tune}</span>
        </div>
      )}
    </div>
  );
}
