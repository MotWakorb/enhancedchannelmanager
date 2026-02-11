import { useState } from 'react';
import type { FFMPEGBuilderState, IPTVPreset } from '../../types/ffmpegBuilder';

// ---------------------------------------------------------------------------
// Saved profile type & API helpers
// ---------------------------------------------------------------------------

export interface SavedProfile {
  id: number;
  name: string;
  config: FFMPEGBuilderState;
  created_at: string;
}

export async function fetchSavedProfiles(): Promise<SavedProfile[]> {
  try {
    const res = await fetch('/api/ffmpeg/profiles');
    if (!res.ok) return [];
    const data = await res.json();
    return (data.profiles || []) as SavedProfile[];
  } catch {
    return [];
  }
}

export async function apiSaveProfile(name: string, config: FFMPEGBuilderState): Promise<SavedProfile | null> {
  try {
    const res = await fetch('/api/ffmpeg/profiles', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, config }),
    });
    if (!res.ok) return null;
    return await res.json() as SavedProfile;
  } catch {
    return null;
  }
}

export async function apiDeleteProfile(id: number): Promise<boolean> {
  try {
    const res = await fetch(`/api/ffmpeg/profiles/${id}`, { method: 'DELETE' });
    return res.ok;
  } catch {
    return false;
  }
}

// ---------------------------------------------------------------------------
// IPTV preset definitions
// ---------------------------------------------------------------------------

// Common IPTV input options for all presets
const IPTV_INPUT_OPTIONS: Record<string, string> = {
  reconnect: '1',
  reconnect_streamed: '1',
  reconnect_delay_max: '10',
  analyzeduration: '5000000',
  probesize: '5000000',
  thread_queue_size: '512',
};

// Common IPTV global options
const IPTV_GLOBAL_OPTIONS: Record<string, string> = {
  fflags: '+genpts+discardcorrupt',
  err_detect: 'ignore_err',
};

// Common IPTV stream mappings (first video + first audio)
const IPTV_STREAM_MAPPINGS: FFMPEGBuilderState['streamMappings'] = [
  { inputIndex: 0, streamType: 'video', streamIndex: 0, outputIndex: 0, label: 'First video' },
  { inputIndex: 0, streamType: 'audio', streamIndex: 0, outputIndex: 1, label: 'First audio' },
];

const IPTV_PRESETS: IPTVPreset[] = [
  {
    id: 'passthrough',
    name: 'Pass-through',
    description: 'Copy streams, no re-encoding',
    icon: 'content_copy',
    config: {
      input: { type: 'url', path: '{streamUrl}', options: IPTV_INPUT_OPTIONS },
      output: { path: 'pipe:1', format: 'ts', overwrite: true },
      videoCodec: { codec: 'copy', rateControl: 'crf' },
      audioCodec: { codec: 'copy' },
      videoFilters: [],
      audioFilters: [],
      streamMappings: IPTV_STREAM_MAPPINGS,
      globalOptions: IPTV_GLOBAL_OPTIONS,
    },
  },
  {
    id: 'iptv-standard',
    name: 'IPTV Standard (H.264)',
    description: 'Software encode, good compatibility',
    icon: 'tv',
    config: {
      input: { type: 'url', path: '{streamUrl}', options: IPTV_INPUT_OPTIONS },
      output: { path: 'pipe:1', format: 'ts', overwrite: true },
      videoCodec: { codec: 'libx264', rateControl: 'crf', crf: 23, preset: 'medium', profile: 'main', level: '4.1', keyframeInterval: 48 },
      audioCodec: { codec: 'aac', bitrate: '192k', sampleRate: 48000, channels: 2 },
      videoFilters: [],
      audioFilters: [],
      streamMappings: IPTV_STREAM_MAPPINGS,
      globalOptions: IPTV_GLOBAL_OPTIONS,
    },
  },
  {
    id: 'iptv-nvidia',
    name: 'IPTV HD (NVIDIA)',
    description: 'Hardware encode with NVENC',
    icon: 'developer_board',
    config: {
      input: {
        type: 'url',
        path: '{streamUrl}',
        hwaccel: { api: 'cuda', outputFormat: 'cuda' },
        options: IPTV_INPUT_OPTIONS,
      },
      output: { path: 'pipe:1', format: 'ts', overwrite: true },
      videoCodec: { codec: 'h264_nvenc', rateControl: 'vbr', bitrate: '8M', maxBitrate: '10M', preset: 'p4', profile: 'main', level: '4.1', keyframeInterval: 48 },
      audioCodec: { codec: 'aac', bitrate: '192k', sampleRate: 48000, channels: 2 },
      videoFilters: [],
      audioFilters: [],
      streamMappings: IPTV_STREAM_MAPPINGS,
      globalOptions: IPTV_GLOBAL_OPTIONS,
    },
  },
  {
    id: 'iptv-qsv',
    name: 'IPTV HD (Intel QSV)',
    description: 'Hardware encode with QSV',
    icon: 'developer_board',
    config: {
      input: {
        type: 'url',
        path: '{streamUrl}',
        hwaccel: { api: 'qsv', outputFormat: 'qsv' },
        options: IPTV_INPUT_OPTIONS,
      },
      output: { path: 'pipe:1', format: 'ts', overwrite: true },
      videoCodec: { codec: 'h264_qsv', rateControl: 'global_quality', globalQuality: 25, preset: 'medium', profile: 'main', level: '4.1', keyframeInterval: 48 },
      audioCodec: { codec: 'aac', bitrate: '192k', sampleRate: 48000, channels: 2 },
      videoFilters: [],
      audioFilters: [],
      streamMappings: IPTV_STREAM_MAPPINGS,
      globalOptions: IPTV_GLOBAL_OPTIONS,
    },
  },
  {
    id: 'low-latency-ac3',
    name: 'Low-Latency AC3',
    description: 'Low-Latency Live MPEG-TS w/ AC3 Audio (CPU-driven)',
    icon: 'speed',
    config: {
      input: {
        type: 'url',
        path: '{streamUrl}',
        options: {
          user_agent: '{userAgent}',
        },
      },
      output: {
        path: 'pipe:1',
        format: 'ts',
        overwrite: false,
        options: {
          mpegts_flags: '+pat_pmt_at_frames+resend_headers+initial_discontinuity',
        },
      },
      videoCodec: { codec: 'copy', rateControl: 'crf', keyframeInterval: 60, keyintMin: 60, scThreshold: 0, forceKeyFrames: 'expr:gte(t,n_forced*0)' },
      audioCodec: { codec: 'ac3' },
      videoFilters: [],
      audioFilters: [],
      streamMappings: [
        { inputIndex: 0, streamType: 'all', streamIndex: 0, outputIndex: 0, label: 'All streams' },
      ],
      globalOptions: {
        fflags: '+discardcorrupt+genpts+nobuffer',
      },
    },
  },
  {
    id: 'hls-output',
    name: 'HLS Output',
    description: 'Segmented streaming output',
    icon: 'stream',
    config: {
      input: { type: 'url', path: '{streamUrl}', options: IPTV_INPUT_OPTIONS },
      output: { path: 'stream.m3u8', format: 'hls', overwrite: true },
      videoCodec: { codec: 'libx264', rateControl: 'crf', crf: 23, preset: 'fast', keyframeInterval: 48, profile: 'main', level: '4.1' },
      audioCodec: { codec: 'aac', bitrate: '192k', sampleRate: 48000, channels: 2 },
      videoFilters: [],
      audioFilters: [],
      streamMappings: IPTV_STREAM_MAPPINGS,
      globalOptions: IPTV_GLOBAL_OPTIONS,
    },
  },
  {
    id: 'hd-1080p-aac',
    name: '1080p / AAC',
    description: 'Full HD with stereo AAC — great all-around IPTV profile',
    icon: 'hd',
    config: {
      input: { type: 'url', path: '{streamUrl}', options: IPTV_INPUT_OPTIONS },
      output: { path: 'pipe:1', format: 'ts', overwrite: true },
      videoCodec: { codec: 'libx264', rateControl: 'vbr', bitrate: '6M', maxBitrate: '8M', bufsize: '12M', preset: 'fast', profile: 'high', level: '4.1', keyframeInterval: 48 },
      audioCodec: { codec: 'aac', bitrate: '192k', sampleRate: 48000, channels: 2 },
      videoFilters: [],
      audioFilters: [],
      streamMappings: IPTV_STREAM_MAPPINGS,
      globalOptions: IPTV_GLOBAL_OPTIONS,
    },
  },
  {
    id: 'uhd-4k-ac3',
    name: '4K / AC3',
    description: '4K HEVC with 5.1 surround sound — high quality home theater',
    icon: '4k',
    config: {
      input: { type: 'url', path: '{streamUrl}', options: IPTV_INPUT_OPTIONS },
      output: { path: 'pipe:1', format: 'ts', overwrite: true },
      videoCodec: { codec: 'libx265', rateControl: 'vbr', bitrate: '15M', maxBitrate: '20M', bufsize: '30M', preset: 'fast', profile: 'main', level: '5.1', keyframeInterval: 60 },
      audioCodec: { codec: 'ac3', bitrate: '384k', sampleRate: 48000, channels: 6 },
      videoFilters: [],
      audioFilters: [],
      streamMappings: IPTV_STREAM_MAPPINGS,
      globalOptions: IPTV_GLOBAL_OPTIONS,
    },
  },
  {
    id: 'long-buffer',
    name: 'Long Buffer',
    description: 'Extra buffering for unstable or slow connections',
    icon: 'hourglass_top',
    config: {
      input: {
        type: 'url',
        path: '{streamUrl}',
        options: {
          reconnect: '1',
          reconnect_streamed: '1',
          reconnect_delay_max: '30',
          analyzeduration: '10000000',
          probesize: '10000000',
          thread_queue_size: '4096',
        },
      },
      output: {
        path: 'pipe:1',
        format: 'ts',
        overwrite: true,
        options: {
          mpegts_flags: '+resend_headers',
        },
      },
      videoCodec: { codec: 'copy', rateControl: 'crf' },
      audioCodec: { codec: 'copy' },
      videoFilters: [],
      audioFilters: [],
      streamMappings: IPTV_STREAM_MAPPINGS,
      globalOptions: {
        fflags: '+genpts+discardcorrupt+nobuffer',
        err_detect: 'ignore_err',
      },
    },
  },
];

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface IPTVPresetBarProps {
  activePresetId?: string;
  onApply: (config: FFMPEGBuilderState, presetId: string) => void;
  savedProfiles: SavedProfile[];
  onSaveProfile: (name: string) => void;
  onDeleteProfile: (id: number) => void;
}

export function IPTVPresetBar({ activePresetId, onApply, savedProfiles, onSaveProfile, onDeleteProfile }: IPTVPresetBarProps) {
  const [showSaveDialog, setShowSaveDialog] = useState(false);
  const [saveName, setSaveName] = useState('');

  const handleSave = () => {
    const name = saveName.trim();
    if (!name) return;
    onSaveProfile(name);
    setSaveName('');
    setShowSaveDialog(false);
  };

  const handleSaveKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') handleSave();
    if (e.key === 'Escape') { setShowSaveDialog(false); setSaveName(''); }
  };

  return (
    <div data-testid="iptv-preset-bar" className="iptv-preset-bar-container">
      {/* Built-in presets row */}
      <div className="iptv-preset-bar">
        <span className="material-icons iptv-preset-bar-icon">bolt</span>
        <span className="iptv-preset-bar-label">Quick Start</span>
        <div className="iptv-preset-buttons">
          {IPTV_PRESETS.map((preset) => (
            <button
              key={preset.id}
              type="button"
              data-testid={`iptv-preset-${preset.id}`}
              className={`iptv-preset-btn${activePresetId === preset.id ? ' active' : ''}`}
              aria-pressed={activePresetId === preset.id}
              title={preset.description}
              onClick={() => onApply(preset.config, preset.id)}
            >
              <span className="material-icons">{preset.icon}</span>
              {preset.name}
            </button>
          ))}
        </div>
      </div>

      {/* Saved profiles row */}
      <div className="iptv-preset-bar saved-profiles-bar">
        <span className="material-icons iptv-preset-bar-icon">folder</span>
        <span className="iptv-preset-bar-label">My Profiles</span>
        <div className="iptv-preset-buttons">
          {savedProfiles.map((profile) => {
            const profileKey = `profile-${profile.id}`;
            return (
            <div key={profile.id} className={`saved-profile-chip${activePresetId === profileKey ? ' active' : ''}`}>
              <button
                type="button"
                data-testid={`saved-profile-${profile.id}`}
                className="saved-profile-load"
                aria-pressed={activePresetId === profileKey}
                title={`Load "${profile.name}"`}
                onClick={() => onApply(profile.config, profileKey)}
              >
                <span className="material-icons">person</span>
                {profile.name}
              </button>
              <button
                type="button"
                data-testid={`delete-profile-${profile.id}`}
                className="saved-profile-delete"
                aria-label={`Delete ${profile.name}`}
                title={`Delete "${profile.name}"`}
                onClick={(e) => { e.stopPropagation(); onDeleteProfile(profile.id); }}
              >
                <span className="material-icons">close</span>
              </button>
            </div>
            );
          })}

          {/* Save button / inline dialog */}
          {showSaveDialog ? (
            <div className="save-profile-inline">
              <input
                type="text"
                data-testid="save-profile-name"
                className="save-profile-input"
                placeholder="Profile name..."
                value={saveName}
                onChange={(e) => setSaveName(e.target.value)}
                onKeyDown={handleSaveKeyDown}
                autoFocus
              />
              <button
                type="button"
                data-testid="save-profile-confirm"
                className="save-profile-confirm"
                onClick={handleSave}
                disabled={!saveName.trim()}
                title="Save profile"
              >
                <span className="material-icons">check</span>
              </button>
              <button
                type="button"
                className="save-profile-cancel"
                onClick={() => { setShowSaveDialog(false); setSaveName(''); }}
                title="Cancel"
              >
                <span className="material-icons">close</span>
              </button>
            </div>
          ) : (
            <button
              type="button"
              data-testid="save-profile-btn"
              className="iptv-preset-btn save-profile-btn"
              title="Save current settings as a profile"
              onClick={() => setShowSaveDialog(true)}
            >
              <span className="material-icons">add</span>
              Save Profile
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

export { IPTV_PRESETS };
