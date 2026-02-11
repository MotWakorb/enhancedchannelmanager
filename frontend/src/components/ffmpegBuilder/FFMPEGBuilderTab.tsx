import { useState, useCallback, useEffect } from 'react';
import type {
  FFMPEGBuilderState,
  InputSource,
  OutputConfig as OutputConfigType,
  VideoCodecSettings,
  AudioCodecSettings,
  VideoFilter,
  AudioFilter,
  ProcessingMode,
  ContainerFormat,
} from '../../types/ffmpegBuilder';
import { InputSourceConfig } from './InputSourceConfig';
import { OutputConfig } from './OutputConfig';
import { VideoCodecSettings as VideoCodecSettingsComponent } from './VideoCodecSettings';
import { AudioCodecSettings as AudioCodecSettingsComponent } from './AudioCodecSettings';
import { VideoFilters } from './VideoFilters';
import { AudioFilters } from './AudioFilters';
import { CommandPreview } from './CommandPreview';
import { ProcessingModeSelector } from './ProcessingModeSelector';
import { IPTVPresetBar, fetchSavedProfiles, apiSaveProfile, apiDeleteProfile } from './IPTVPresetBar';
import type { SavedProfile } from './IPTVPresetBar';
import { StreamOptionsPanel } from './StreamOptionsPanel';
import './FFMPEGBuilderTab.css';

// ---------------------------------------------------------------------------
// Stream Options state for IPTV smart defaults
// ---------------------------------------------------------------------------

export interface StreamOptionsState {
  networkResilience: boolean;
  reconnectDelayMax: string;
  streamAnalysis: boolean;
  analyzeduration: string;
  probesize: string;
  errorHandling: boolean;
  bufferSize: string;
  streamMapping: boolean;
}

export const DEFAULT_STREAM_OPTIONS: StreamOptionsState = {
  networkResilience: true,
  reconnectDelayMax: '10',
  streamAnalysis: true,
  analyzeduration: '5000000',
  probesize: '5000000',
  errorHandling: true,
  bufferSize: '512',
  streamMapping: true,
};

/** Convert StreamOptionsState into FFMPEGBuilderState fragments */
export function buildIPTVOptions(opts: StreamOptionsState): {
  inputOptions: Record<string, string>;
  globalOptions: Record<string, string>;
  streamMappings: FFMPEGBuilderState['streamMappings'];
} {
  const inputOptions: Record<string, string> = {};
  const globalOptions: Record<string, string> = {};

  if (opts.errorHandling) {
    globalOptions['fflags'] = '+genpts+discardcorrupt';
    globalOptions['err_detect'] = 'ignore_err';
  }

  if (opts.networkResilience) {
    inputOptions['reconnect'] = '1';
    inputOptions['reconnect_streamed'] = '1';
    inputOptions['reconnect_delay_max'] = opts.reconnectDelayMax;
  }

  if (opts.streamAnalysis) {
    inputOptions['analyzeduration'] = opts.analyzeduration;
    inputOptions['probesize'] = opts.probesize;
  }

  inputOptions['thread_queue_size'] = opts.bufferSize;

  const streamMappings: FFMPEGBuilderState['streamMappings'] = opts.streamMapping
    ? [
        { inputIndex: 0, streamType: 'video', streamIndex: 0, outputIndex: 0, label: 'First video' },
        { inputIndex: 0, streamType: 'audio', streamIndex: 0, outputIndex: 1, label: 'First audio' },
      ]
    : [];

  return { inputOptions, globalOptions, streamMappings };
}

// ---------------------------------------------------------------------------
// Processing mode → codec/hwaccel mapping
// ---------------------------------------------------------------------------

function applyProcessingMode(
  mode: ProcessingMode,
  state: FFMPEGBuilderState,
  streamOptions?: StreamOptionsState,
): FFMPEGBuilderState {
  const opts = streamOptions ?? DEFAULT_STREAM_OPTIONS;
  const iptv = buildIPTVOptions(opts);

  const base = {
    ...state,
    globalOptions: iptv.globalOptions,
    input: { ...state.input, options: iptv.inputOptions },
    streamMappings: iptv.streamMappings,
  };

  switch (mode) {
    case 'copy':
      return {
        ...base,
        videoCodec: { codec: 'copy', rateControl: 'crf' },
        audioCodec: { codec: 'copy' },
        input: { ...base.input, hwaccel: undefined },
      };
    case 'software':
      return {
        ...base,
        videoCodec: { codec: 'libx264', rateControl: 'crf', crf: 23, preset: 'medium', profile: 'main', level: '4.1', keyframeInterval: 48 },
        audioCodec: { codec: 'aac', bitrate: '192k', sampleRate: 48000, channels: 2 },
        input: { ...base.input, hwaccel: undefined },
      };
    case 'nvidia':
      return {
        ...base,
        videoCodec: { codec: 'h264_nvenc', rateControl: 'vbr', bitrate: '8M', maxBitrate: '10M', preset: 'p4', profile: 'main', level: '4.1', keyframeInterval: 48 },
        audioCodec: { codec: 'aac', bitrate: '192k', sampleRate: 48000, channels: 2 },
        input: { ...base.input, hwaccel: { api: 'cuda', outputFormat: 'cuda' } },
      };
    case 'amd':
      return {
        ...base,
        videoCodec: { codec: 'h264_vaapi', rateControl: 'crf', profile: 'main', level: '4.1', keyframeInterval: 48 },
        audioCodec: { codec: 'aac', bitrate: '192k', sampleRate: 48000, channels: 2 },
        input: { ...base.input, hwaccel: { api: 'vaapi', device: '/dev/dri/renderD128', outputFormat: 'vaapi' } },
      };
    case 'qsv':
      return {
        ...base,
        videoCodec: { codec: 'h264_qsv', rateControl: 'global_quality', globalQuality: 25, preset: 'medium', profile: 'main', level: '4.1', keyframeInterval: 48 },
        audioCodec: { codec: 'aac', bitrate: '192k', sampleRate: 48000, channels: 2 },
        input: { ...base.input, hwaccel: { api: 'qsv', outputFormat: 'qsv' } },
      };
    case 'vaapi':
      return {
        ...base,
        videoCodec: { codec: 'h264_vaapi', rateControl: 'crf', profile: 'main', level: '4.1', keyframeInterval: 48 },
        audioCodec: { codec: 'aac', bitrate: '192k', sampleRate: 48000, channels: 2 },
        input: { ...base.input, hwaccel: { api: 'vaapi', device: '/dev/dri/renderD128', outputFormat: 'vaapi' } },
      };
  }
}

function detectProcessingMode(state: FFMPEGBuilderState): ProcessingMode {
  if (state.videoCodec.codec === 'copy') return 'copy';
  if (state.videoCodec.codec === 'h264_nvenc' || state.videoCodec.codec === 'hevc_nvenc') return 'nvidia';
  if (state.videoCodec.codec === 'h264_qsv' || state.videoCodec.codec === 'hevc_qsv') return 'qsv';
  if (state.input?.hwaccel?.api === 'vaapi') {
    if (state.input.hwaccel.device?.includes('renderD128') || state.videoCodec.codec === 'h264_vaapi') {
      // Distinguish AMD from Intel VAAPI based on codec or explicit detection
      // Default to vaapi (Intel) unless we can tell otherwise
      return 'vaapi';
    }
  }
  if (state.videoCodec.codec === 'h264_vaapi' || state.videoCodec.codec === 'hevc_vaapi') return 'vaapi';
  return 'software';
}

// ---------------------------------------------------------------------------
// Default state (now URL-first for IPTV)
// ---------------------------------------------------------------------------

function defaultState(): FFMPEGBuilderState {
  const iptv = buildIPTVOptions(DEFAULT_STREAM_OPTIONS);
  return {
    input: {
      type: 'url',
      path: '{streamUrl}',
      options: iptv.inputOptions,
    },
    output: { path: 'pipe:1', format: 'ts', overwrite: true },
    videoCodec: { codec: 'copy', rateControl: 'crf' },
    audioCodec: { codec: 'copy' },
    videoFilters: [],
    audioFilters: [],
    streamMappings: iptv.streamMappings,
    globalOptions: iptv.globalOptions,
  };
}

// ---------------------------------------------------------------------------
// Simple output format options
// ---------------------------------------------------------------------------

interface SimpleOutputOption {
  format: ContainerFormat;
  label: string;
  description: string;
  extension: string;
  icon: string;
}

const SIMPLE_OUTPUT_OPTIONS: SimpleOutputOption[] = [
  { format: 'ts', label: 'MPEG-TS', description: 'Pipe to stdout for IPTV proxying', extension: '', icon: 'live_tv' },
  { format: 'hls', label: 'HLS', description: 'HTTP Live Streaming (segmented)', extension: '.m3u8', icon: 'stream' },
];

// ---------------------------------------------------------------------------
// Simple audio codec options
// ---------------------------------------------------------------------------

interface SimpleAudioOption {
  codec: AudioCodecSettings['codec'];
  label: string;
  description: string;
  icon: string;
  defaults?: Partial<AudioCodecSettings>;
}

const SIMPLE_AUDIO_CODECS: SimpleAudioOption[] = [
  { codec: 'copy', label: 'Copy', description: 'Pass-through', icon: 'content_copy' },
  { codec: 'aac', label: 'AAC', description: 'Best compatibility', icon: 'audiotrack', defaults: { bitrate: '192k', sampleRate: 48000 } },
  { codec: 'ac3', label: 'AC3', description: 'Dolby Digital', icon: 'surround_sound', defaults: { bitrate: '384k', sampleRate: 48000 } },
  { codec: 'eac3', label: 'EAC3', description: 'Dolby Digital+', icon: 'surround_sound', defaults: { bitrate: '448k', sampleRate: 48000 } },
];

interface SimpleChannelOption {
  channels: number;
  label: string;
  icon: string;
}

const SIMPLE_CHANNEL_OPTIONS: SimpleChannelOption[] = [
  { channels: 2, label: 'Stereo', icon: 'headphones' },
  { channels: 6, label: '5.1 Surround', icon: 'speaker_group' },
  { channels: 8, label: '7.1 Surround', icon: 'speaker_group' },
];


// ---------------------------------------------------------------------------
// FFMPEGBuilderTab
// ---------------------------------------------------------------------------

export function FFMPEGBuilderTab() {
  const [state, setState] = useState<FFMPEGBuilderState>(defaultState);
  const [mode, setMode] = useState<'simple' | 'advanced'>('simple');
  const [processingMode, setProcessingMode] = useState<ProcessingMode>('copy');
  const [activePresetId, setActivePresetId] = useState<string | undefined>();
  const [streamOptions, setStreamOptions] = useState<StreamOptionsState>(DEFAULT_STREAM_OPTIONS);
  const [savedProfiles, setSavedProfiles] = useState<SavedProfile[]>([]);

  // Fetch saved profiles from API on mount
  useEffect(() => {
    fetchSavedProfiles().then(setSavedProfiles);
  }, []);

  // --- Shared updaters ---

  const updateInput = useCallback((input: InputSource) => {
    setState(prev => ({ ...prev, input }));
    setActivePresetId(undefined);
  }, []);

  const updateOutput = useCallback((output: OutputConfigType) => {
    setState(prev => ({ ...prev, output }));
    setActivePresetId(undefined);
  }, []);

  const updateVideoCodec = useCallback((videoCodec: VideoCodecSettings) => {
    setState(prev => ({ ...prev, videoCodec }));
    setActivePresetId(undefined);
  }, []);

  const updateAudioCodec = useCallback((audioCodec: AudioCodecSettings) => {
    setState(prev => ({ ...prev, audioCodec }));
    setActivePresetId(undefined);
  }, []);

  const updateVideoFilters = useCallback((videoFilters: VideoFilter[]) => {
    setState(prev => ({ ...prev, videoFilters }));
  }, []);

  const updateAudioFilters = useCallback((audioFilters: AudioFilter[]) => {
    setState(prev => ({ ...prev, audioFilters }));
  }, []);

  // --- Simple mode handlers ---

  const handleSourceUrlChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const url = e.target.value;
    setState(prev => ({ ...prev, input: { ...prev.input, type: 'url', path: url } }));
    setActivePresetId(undefined);
  }, []);

  const handleProcessingModeChange = useCallback((pm: ProcessingMode) => {
    setProcessingMode(pm);
    setState(prev => applyProcessingMode(pm, prev, streamOptions));
    setActivePresetId(undefined);
  }, [streamOptions]);

  const handleSimpleOutputFormatChange = useCallback((opt: SimpleOutputOption) => {
    setState(prev => {
      const isPipe = opt.format === 'ts';
      const baseName = prev.output.path?.replace(/^pipe:\d+$/, 'output').replace(/\.[^.]+$/, '') || 'output';
      return {
        ...prev,
        output: {
          ...prev.output,
          format: opt.format,
          path: isPipe ? 'pipe:1' : baseName + opt.extension,
          movflags: opt.format === 'mp4' ? ['faststart'] : undefined,
        },
      };
    });
    setActivePresetId(undefined);
  }, []);

  const handleSimpleAudioCodecChange = useCallback((opt: SimpleAudioOption) => {
    setState(prev => ({
      ...prev,
      audioCodec: {
        ...prev.audioCodec,
        codec: opt.codec,
        ...(opt.defaults || {}),
        // Keep existing channels when switching codec
        channels: prev.audioCodec.channels,
      },
    }));
    setActivePresetId(undefined);
  }, []);

  const handleSimpleChannelChange = useCallback((opt: SimpleChannelOption) => {
    setState(prev => ({
      ...prev,
      audioCodec: {
        ...prev.audioCodec,
        channels: opt.channels,
      },
    }));
    setActivePresetId(undefined);
  }, []);

  const handleIptvPresetApply = useCallback((config: FFMPEGBuilderState, presetId: string) => {
    // Built-in presets use {streamUrl} as placeholder — keep the user's actual URL.
    // Saved profiles restore everything as-is.
    const isSavedProfile = presetId.startsWith('profile-');
    setState(prev => ({
      ...config,
      input: {
        ...config.input,
        path: isSavedProfile ? config.input.path : (prev.input.path || config.input.path),
      },
    }));
    setProcessingMode(detectProcessingMode(config));
    setActivePresetId(presetId);
  }, []);

  const handleSaveProfile = useCallback(async (name: string) => {
    const config = { ...state };
    const profile = await apiSaveProfile(name, config);
    if (profile) {
      setSavedProfiles(prev => [...prev, profile]);
      setActivePresetId(`profile-${profile.id}`);
    }
  }, [state]);

  const handleDeleteProfile = useCallback(async (id: number) => {
    const ok = await apiDeleteProfile(id);
    if (ok) {
      setSavedProfiles(prev => prev.filter(p => p.id !== id));
      setActivePresetId(prev => prev === `profile-${id}` ? undefined : prev);
    }
  }, []);

  const handleStreamOptionsChange = useCallback((newOpts: StreamOptionsState) => {
    setStreamOptions(newOpts);
    // Rebuild IPTV options and merge into state
    const iptv = buildIPTVOptions(newOpts);
    setState(prev => ({
      ...prev,
      globalOptions: iptv.globalOptions,
      input: { ...prev.input, options: iptv.inputOptions },
      streamMappings: iptv.streamMappings,
    }));
    setActivePresetId(undefined);
  }, []);

  // --- Mode toggle ---

  const handleModeToggle = useCallback(() => {
    setMode(prev => {
      const next = prev === 'simple' ? 'advanced' : 'simple';
      if (next === 'simple') {
        // Sync the processing mode from the current state
        setProcessingMode(detectProcessingMode(state));
      }
      return next;
    });
  }, [state]);

  // --- Render ---

  return (
    <div data-testid="ffmpeg-builder" className="ffmpeg-builder-tab">
      <div className="ffmpeg-builder-page">
        {/* Page Header */}
        <div className="settings-page-header">
          <div className="ffmpeg-header-row">
            <div>
              <h2>FFMPEG Builder</h2>
              <p>
                {mode === 'simple'
                  ? 'Configure your IPTV stream in three simple steps.'
                  : 'Build FFmpeg commands visually. Configure your input, encoding settings, and filters below — the command updates in real time at the bottom.'}
              </p>
            </div>
            <button
              type="button"
              data-testid="mode-toggle"
              className="btn-secondary"
              onClick={handleModeToggle}
            >
              <span className="material-icons">{mode === 'simple' ? 'tune' : 'auto_fix_high'}</span>
              {mode === 'simple' ? 'Advanced' : 'Simple'}
            </button>
          </div>
        </div>

        {/* ================================================================= */}
        {/* SIMPLE MODE                                                       */}
        {/* ================================================================= */}
        {mode === 'simple' && (
          <>
            {/* IPTV Preset Bar */}
            <IPTVPresetBar
              activePresetId={activePresetId}
              onApply={handleIptvPresetApply}
              savedProfiles={savedProfiles}
              onSaveProfile={handleSaveProfile}
              onDeleteProfile={handleDeleteProfile}
            />

            {/* Step 1: Source */}
            <div data-testid="wizard-step-source" className="wizard-step">
              <div className="wizard-step-header">
                <span className="wizard-step-number">1</span>
                <h3>Source</h3>
              </div>
              <p className="ffmpeg-section-description">
                Enter the URL of your IPTV stream or media source.
              </p>
              <div className="form-group">
                <input
                  type="url"
                  data-testid="source-url-input"
                  className="form-input"
                  placeholder="http://example.com/stream.m3u8"
                  value={state.input.path}
                  onChange={handleSourceUrlChange}
                />
              </div>
            </div>

            {/* Step 2: Processing Mode */}
            <div data-testid="wizard-step-processing" className="wizard-step">
              <div className="wizard-step-header">
                <span className="wizard-step-number">2</span>
                <h3>Processing</h3>
              </div>
              <p className="ffmpeg-section-description">
                Choose how the stream should be processed. Copy is fastest; hardware encoding uses your GPU.
              </p>
              <ProcessingModeSelector
                value={processingMode}
                onChange={handleProcessingModeChange}
              />
            </div>

            {/* Step 3: Output */}
            <div data-testid="wizard-step-output" className="wizard-step">
              <div className="wizard-step-header">
                <span className="wizard-step-number">3</span>
                <h3>Output</h3>
              </div>
              <p className="ffmpeg-section-description">
                Pick the output format for your stream.
              </p>
              <div className="simple-output-grid">
                {SIMPLE_OUTPUT_OPTIONS.map((opt) => (
                  <button
                    key={opt.format}
                    type="button"
                    data-testid={`simple-output-${opt.format}`}
                    className={`simple-output-card${state.output.format === opt.format ? ' active' : ''}`}
                    aria-pressed={state.output.format === opt.format}
                    onClick={() => handleSimpleOutputFormatChange(opt)}
                  >
                    <span className="material-icons">{opt.icon}</span>
                    <span className="simple-output-label">{opt.label}</span>
                    <span className="simple-output-desc">{opt.description}</span>
                  </button>
                ))}
              </div>
              {/* Audio options */}
              <div className="simple-audio-section">
                <div className="simple-audio-row">
                  <label className="simple-audio-label">Audio Codec</label>
                  <div className="simple-audio-options">
                    {SIMPLE_AUDIO_CODECS.map((opt) => (
                      <button
                        key={opt.codec}
                        type="button"
                        data-testid={`simple-audio-codec-${opt.codec}`}
                        className={`simple-audio-btn${state.audioCodec.codec === opt.codec ? ' active' : ''}`}
                        aria-pressed={state.audioCodec.codec === opt.codec}
                        title={opt.description}
                        onClick={() => handleSimpleAudioCodecChange(opt)}
                      >
                        <span className="material-icons">{opt.icon}</span>
                        {opt.label}
                      </button>
                    ))}
                  </div>
                </div>
                {state.audioCodec.codec !== 'copy' && (
                  <div className="simple-audio-row">
                    <label className="simple-audio-label">Channels</label>
                    <div className="simple-audio-options">
                      {SIMPLE_CHANNEL_OPTIONS.map((opt) => (
                        <button
                          key={opt.channels}
                          type="button"
                          data-testid={`simple-audio-channels-${opt.channels}`}
                          className={`simple-audio-btn${state.audioCodec.channels === opt.channels ? ' active' : ''}`}
                          aria-pressed={state.audioCodec.channels === opt.channels}
                          onClick={() => handleSimpleChannelChange(opt)}
                        >
                          <span className="material-icons">{opt.icon}</span>
                          {opt.label}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>

              <div className="form-group" style={{ marginTop: '1rem' }}>
                <label htmlFor="simple-output-path">Output path</label>
                <input
                  id="simple-output-path"
                  type="text"
                  data-testid="simple-output-path"
                  className="form-input"
                  placeholder="output.ts"
                  value={state.output.path}
                  onChange={(e) =>
                    setState(prev => ({ ...prev, output: { ...prev.output, path: e.target.value } }))
                  }
                />
              </div>
            </div>

            {/* Stream Options Panel */}
            <StreamOptionsPanel
              value={streamOptions}
              onChange={handleStreamOptionsChange}
            />

            {/* Command Preview */}
            <CommandPreview config={state} />
          </>
        )}

        {/* ================================================================= */}
        {/* ADVANCED MODE                                                     */}
        {/* ================================================================= */}
        {mode === 'advanced' && (
          <>
            {/* IPTV Preset Bar */}
            <IPTVPresetBar
              activePresetId={activePresetId}
              onApply={handleIptvPresetApply}
              savedProfiles={savedProfiles}
              onSaveProfile={handleSaveProfile}
              onDeleteProfile={handleDeleteProfile}
            />

            {/* Input Section */}
            <div data-testid="input-section" className="ffmpeg-section">
              <div className="ffmpeg-section-header">
                <span className="material-icons">input</span>
                <h3>Input Source</h3>
              </div>
              <p className="ffmpeg-section-description">
                Where does your media come from? Choose a local file, network URL, or hardware device.
              </p>
              <InputSourceConfig value={state.input} onChange={updateInput} />
            </div>

            {/* Output Section */}
            <div data-testid="output-section" className="ffmpeg-section">
              <div className="ffmpeg-section-header">
                <span className="material-icons">save_alt</span>
                <h3>Output</h3>
              </div>
              <p className="ffmpeg-section-description">
                Where should the result be saved? Pick your output format and file path.
              </p>
              <OutputConfig value={state.output} onChange={updateOutput} />
            </div>

            {/* Video Codec Section */}
            <div data-testid="video-codec-section" className="ffmpeg-section">
              <div className="ffmpeg-section-header">
                <span className="material-icons">videocam</span>
                <h3>Video Codec</h3>
              </div>
              <p className="ffmpeg-section-description">
                How should video be encoded? H.264 is the most compatible choice. Use &ldquo;Copy&rdquo; to keep the original encoding without re-processing.
              </p>
              <VideoCodecSettingsComponent value={state.videoCodec} onChange={updateVideoCodec} />
            </div>

            {/* Audio Codec Section */}
            <div data-testid="audio-codec-section" className="ffmpeg-section">
              <div className="ffmpeg-section-header">
                <span className="material-icons">audiotrack</span>
                <h3>Audio Codec</h3>
              </div>
              <p className="ffmpeg-section-description">
                How should audio be encoded? AAC at 192k is a good default for most use cases.
              </p>
              <AudioCodecSettingsComponent value={state.audioCodec} onChange={updateAudioCodec} />
            </div>

            {/* Video Filters Section */}
            <div data-testid="video-filters-section" className="ffmpeg-section">
              <div className="ffmpeg-section-header">
                <span className="material-icons">tune</span>
                <h3>Video Filters</h3>
              </div>
              <p className="ffmpeg-section-description">
                Optional processing applied to the video — resize, crop, adjust speed, add text overlays, and more.
              </p>
              <VideoFilters value={state.videoFilters} onChange={updateVideoFilters} />
            </div>

            {/* Audio Filters Section */}
            <div data-testid="audio-filters-section" className="ffmpeg-section">
              <div className="ffmpeg-section-header">
                <span className="material-icons">equalizer</span>
                <h3>Audio Filters</h3>
              </div>
              <p className="ffmpeg-section-description">
                Optional processing applied to the audio — adjust volume, normalize loudness, or apply effects.
              </p>
              <AudioFilters value={state.audioFilters} onChange={updateAudioFilters} />
            </div>

            {/* Command Preview — always at bottom */}
            <CommandPreview config={state} />
          </>
        )}
      </div>
    </div>
  );
}
