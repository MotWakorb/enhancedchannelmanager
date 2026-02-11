/**
 * TypeScript types for the Visual FFMPEG Syntax Builder.
 *
 * These types define the TDD contract for all builder components.
 * Implementation must satisfy these interfaces.
 */

// =============================================================================
// Hardware Acceleration
// =============================================================================

/** Supported hardware acceleration APIs */
export type HWAccelAPI = 'none' | 'cuda' | 'qsv' | 'vaapi';

/** Hardware acceleration configuration for input decoding */
export interface HWAccelConfig {
  api: HWAccelAPI;
  device?: string;           // e.g., '/dev/dri/renderD128' for VAAPI
  outputFormat?: string;     // e.g., 'cuda', 'qsv', 'vaapi'
}

/** Hardware capability detected from the system */
export interface HWCapability {
  api: HWAccelAPI;
  available: boolean;
  encoders: string[];        // e.g., ['h264_nvenc', 'hevc_nvenc']
  decoders: string[];        // e.g., ['h264_cuvid', 'hevc_cuvid']
  devices: string[];         // e.g., ['/dev/dri/renderD128']
  reason?: string;           // Why unavailable, if applicable
}

// =============================================================================
// Input Source (Spec 1.1)
// =============================================================================

/** Input source type */
export type InputSourceType = 'url' | 'pipe';

/** Input source configuration */
export interface InputSource {
  type: InputSourceType;
  path: string;              // URL or 'pipe:0'
  format?: string;           // Force input format (e.g., 'mpegts', 'hls')
  hwaccel?: HWAccelConfig;
  options?: Record<string, string>;  // Additional input options
}

// =============================================================================
// Output Config (Spec 1.2)
// =============================================================================

/** Output container format */
export type ContainerFormat = 'ts' | 'hls' | 'dash';

/** Output configuration */
export interface OutputConfig {
  path: string;
  format?: ContainerFormat;
  overwrite?: boolean;       // -y flag
  options?: Record<string, string>;
}

// =============================================================================
// Video Codec (Spec 1.3)
// =============================================================================

/** Video codec category for UI grouping */
export type VideoCodecCategory = 'software' | 'nvidia' | 'qsv' | 'vaapi';

/** Available video codecs */
export type VideoCodec =
  // Software
  | 'libx264' | 'libx265' | 'libvpx-vp9' | 'libaom-av1' | 'libsvtav1'
  // NVIDIA NVENC
  | 'h264_nvenc' | 'hevc_nvenc'
  // Intel QSV
  | 'h264_qsv' | 'hevc_qsv'
  // VAAPI
  | 'h264_vaapi' | 'hevc_vaapi'
  // Special
  | 'copy';

/** Rate control mode */
export type RateControlMode =
  | 'crf' | 'cbr' | 'vbr' | 'cq' | 'qp'
  | 'global_quality';   // QSV-specific

/** Video codec settings */
export interface VideoCodecSettings {
  codec: VideoCodec;
  preset?: string;          // Encoder preset
  profile?: string;         // H.264/H.265 profile
  level?: string;           // Codec level
  tune?: string;            // Tune option (film, animation, etc.)
  rateControl: RateControlMode;
  bitrate?: string;         // For CBR/VBR (e.g., '5M')
  maxBitrate?: string;      // VBR max
  bufsize?: string;         // VBR buffer size
  crf?: number;             // CRF value (software)
  cq?: number;              // CQ value (NVENC)
  globalQuality?: number;   // QSV quality
  qp?: number;              // Constant QP
  pixelFormat?: string;     // e.g., 'yuv420p', 'yuv420p10le'
  keyframeInterval?: number; // -g (GOP size)
  keyintMin?: number;        // -keyint_min (minimum GOP size)
  scThreshold?: number;      // -sc_threshold (scene change threshold, 0 = disable)
  forceKeyFrames?: string;   // -force_key_frames (expression or timestamps)
  bFrames?: number;         // -bf
  // NVENC-specific
  nvencRc?: string;         // NVENC rate control mode
  spatialAq?: boolean;      // spatial-aq
  temporalAq?: boolean;     // temporal-aq
  bRefMode?: string;        // b_ref_mode
  // VAAPI-specific
  compressionLevel?: number;
  // QSV-specific
  lookAhead?: number;
}

// =============================================================================
// Audio Codec (Spec 1.4)
// =============================================================================

/** Available audio codecs */
export type AudioCodec = 'aac' | 'ac3' | 'eac3' | 'copy';

/** Audio codec settings */
export interface AudioCodecSettings {
  codec: AudioCodec;
  bitrate?: string;          // e.g., '128k', '320k'
  sampleRate?: number;       // e.g., 44100, 48000
  channels?: number;         // e.g., 2 (stereo), 6 (5.1)
  channelLayout?: string;    // e.g., 'stereo', '5.1'
  profile?: string;          // AAC profile (aac_low, aac_he, aac_he_v2)
}

// =============================================================================
// Video Filters (Spec 1.5)
// =============================================================================

/** Video filter types */
export type VideoFilterType =
  | 'scale' | 'fps' | 'deinterlace'
  | 'format' | 'hwupload'
  | 'custom';

/** A single video filter configuration */
export interface VideoFilter {
  type: VideoFilterType;
  enabled: boolean;
  params: Record<string, string | number | boolean>;
  order: number;            // Filter chain position
}

// =============================================================================
// Audio Filters (Spec 1.6)
// =============================================================================

/** Audio filter types */
export type AudioFilterType =
  | 'volume' | 'loudnorm' | 'aresample'
  | 'custom';

/** A single audio filter configuration */
export interface AudioFilter {
  type: AudioFilterType;
  enabled: boolean;
  params: Record<string, string | number | boolean>;
  order: number;
}

// =============================================================================
// Stream Mapping (Spec 1.7)
// =============================================================================

/** Stream type for mapping */
export type StreamType = 'video' | 'audio' | 'subtitle' | 'data' | 'all';

/** A stream mapping entry */
export interface StreamMapping {
  inputIndex: number;        // Which input (0-based)
  streamType: StreamType;
  streamIndex: number;       // Which stream of that type (0-based)
  outputIndex: number;       // Output position
  label?: string;            // Human-readable label
  language?: string;         // Language metadata
  title?: string;            // Stream title metadata
}

// =============================================================================
// Preset Templates (Spec 1.8)
// =============================================================================

/** Preset category */
export type PresetCategory =
  | 'web' | 'streaming' | 'archive' | 'mobile'
  | 'editing' | 'social' | 'custom';

/** A preset template */
export interface PresetTemplate {
  id: string;
  name: string;
  description: string;       // Plain-English use case description
  category: PresetCategory;
  isBuiltIn: boolean;
  config: FFMPEGBuilderState;
}

// =============================================================================
// Command Preview (Spec 1.9)
// =============================================================================

/** An annotated flag in the command preview */
export interface CommandAnnotation {
  flag: string;              // e.g., '-c:v libx264'
  explanation: string;       // e.g., 'Video codec: H.264 software encoder'
  category: 'input' | 'output' | 'video' | 'audio' | 'filter' | 'global';
}

/** Command preview with annotations */
export interface CommandPreview {
  command: string;           // Full ffmpeg command string
  annotations: CommandAnnotation[];
  warnings: string[];        // Compatibility warnings
  isValid: boolean;
}

// =============================================================================
// Execution (Spec 1.10)
// =============================================================================

/** Job execution status */
export type JobStatus =
  | 'queued' | 'running' | 'completed' | 'failed' | 'cancelled';

/** FFMPEG job progress */
export interface FFMPEGJobProgress {
  percent: number;           // 0-100
  fps: number;
  speed: string;             // e.g., '2.5x'
  time: string;              // Current position (HH:MM:SS)
  size: string;              // Current output size
  bitrate: string;           // Current bitrate
  eta?: string;              // Estimated time remaining
}

/** FFMPEG job record */
export interface FFMPEGJob {
  id: string;
  name: string;
  status: JobStatus;
  command: string;
  progress?: FFMPEGJobProgress;
  startedAt?: string;
  completedAt?: string;
  error?: string;
  outputPath?: string;
  createdAt: string;
}

// =============================================================================
// Persistence (Spec 1.11)
// =============================================================================

/** Saved builder configuration */
export interface SavedConfig {
  id: number;
  name: string;
  description?: string;
  config: FFMPEGBuilderState;
  createdAt: string;
  updatedAt: string;
}

// =============================================================================
// ECM Integration (Spec 1.12)
// =============================================================================

/** Channel profile using FFMPEG settings */
export interface FFMPEGChannelProfile {
  id: number;
  name: string;
  description?: string;
  configId: number;          // References SavedConfig
  applyTo: 'all' | 'group' | 'channel';
  targetIds?: number[];      // Channel or group IDs
  enabled: boolean;
}

// =============================================================================
// Builder State (Full State Object)
// =============================================================================

/** Complete FFMPEG builder state */
export interface FFMPEGBuilderState {
  input: InputSource;
  output: OutputConfig;
  videoCodec: VideoCodecSettings;
  audioCodec: AudioCodecSettings;
  videoFilters: VideoFilter[];
  audioFilters: AudioFilter[];
  streamMappings: StreamMapping[];
  globalOptions?: Record<string, string>;
}

// =============================================================================
// Job Queue (Spec 1.15)
// =============================================================================

/** Job queue status */
export interface JobQueueStatus {
  running: number;
  queued: number;
  completed: number;
  failed: number;
  maxConcurrent: number;
}

/** Job queue configuration */
export interface JobQueueConfig {
  maxConcurrent: number;
  maxRetries: number;
  retryDelay: number;        // Seconds
  priority: 'fifo' | 'priority';
}

// =============================================================================
// Backend API Types (Spec 1.14)
// =============================================================================

/** FFMPEG capabilities response */
export interface FFMPEGCapabilities {
  version: string;
  encoders: string[];
  decoders: string[];
  formats: string[];
  filters: string[];
  hwaccels: HWCapability[];
}

/** Validate config request */
export interface ValidateConfigRequest {
  config: FFMPEGBuilderState;
}

/** Validate config response */
export interface ValidateConfigResponse {
  valid: boolean;
  errors: string[];
  warnings: string[];
  command: string;           // Generated command
}

/** API response for listing saved configs */
export interface SavedConfigsListResponse {
  configs: SavedConfig[];
  total: number;
}

/** API response for listing jobs */
export interface JobsListResponse {
  jobs: FFMPEGJob[];
  total: number;
  queue: JobQueueStatus;
}

// =============================================================================
// Explanation Types (Cross-cutting)
// =============================================================================

/** An explanation entry for an FFMPEG option */
export interface FFMPEGExplanation {
  title: string;             // Short label
  description: string;       // Plain-English explanation
  useCases?: string[];       // Common use cases
  tradeoffs?: string;        // Quality/speed/size trade-offs
  example?: string;          // Example value
}

/** Category of explanations */
export type ExplanationCategory =
  | 'codec' | 'preset' | 'rateControl' | 'filter'
  | 'container' | 'hwaccel' | 'global' | 'input' | 'output';

// =============================================================================
// IPTV Wizard Types
// =============================================================================

/** Processing mode for the IPTV-focused simple wizard */
export type ProcessingMode = 'copy' | 'software' | 'nvidia' | 'amd' | 'qsv' | 'vaapi';

/** IPTV preset with a full builder state snapshot */
export interface IPTVPreset {
  id: string;
  name: string;
  description: string;
  icon: string;
  config: FFMPEGBuilderState;
}
