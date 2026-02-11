/**
 * E2E test selectors and test data for the FFMPEG Builder feature.
 *
 * Provides CSS selectors, test data factories, and helper functions
 * for FFMPEG Builder E2E tests.
 */

// =============================================================================
// FFMPEG Builder Selectors
// =============================================================================

export const ffmpegSelectors = {
  // Tab navigation
  ffmpegTab: '[data-tab="ffmpeg-builder"]',
  ffmpegTabContent: '.ffmpeg-builder-tab, [data-testid="ffmpeg-builder-tab"]',

  // Main layout sections
  builderContainer: '.ffmpeg-builder-container, [data-testid="ffmpeg-builder"]',
  inputSection: '.ffmpeg-input-section, [data-testid="input-section"]',
  outputSection: '.ffmpeg-output-section, [data-testid="output-section"]',
  videoCodecSection: '.ffmpeg-video-codec-section, [data-testid="video-codec-section"]',
  audioCodecSection: '.ffmpeg-audio-codec-section, [data-testid="audio-codec-section"]',
  videoFiltersSection: '.ffmpeg-video-filters-section, [data-testid="video-filters-section"]',
  audioFiltersSection: '.ffmpeg-audio-filters-section, [data-testid="audio-filters-section"]',
  streamMappingSection: '.ffmpeg-stream-mapping-section, [data-testid="stream-mapping-section"]',
  commandPreview: '.ffmpeg-command-preview, [data-testid="command-preview"]',

  // Input Source
  inputTypeSelect: '[data-testid="input-type-select"]',
  inputPathInput: '[data-testid="input-path"], input[name="inputPath"]',
  inputFormatSelect: '[data-testid="input-format-select"]',
  hwaccelSelect: '[data-testid="hwaccel-select"]',
  hwaccelDeviceInput: '[data-testid="hwaccel-device"]',
  startTimeInput: '[data-testid="start-time"], input[name="startTime"]',
  durationInput: '[data-testid="duration"], input[name="duration"]',

  // Output Config
  outputPathInput: '[data-testid="output-path"], input[name="outputPath"]',
  outputFormatSelect: '[data-testid="output-format-select"]',
  overwriteCheckbox: '[data-testid="overwrite-checkbox"]',
  movflagsCheckbox: (flag: string) => `[data-testid="movflag-${flag}"]`,

  // Video Codec
  videoCodecSelect: '[data-testid="video-codec-select"]',
  videoPresetSelect: '[data-testid="video-preset-select"]',
  videoProfileSelect: '[data-testid="video-profile-select"]',
  rateControlSelect: '[data-testid="rate-control-select"]',
  crfInput: '[data-testid="crf-input"], input[name="crf"]',
  crfSlider: '[data-testid="crf-slider"]',
  videoBitrateInput: '[data-testid="video-bitrate"], input[name="videoBitrate"]',
  pixelFormatSelect: '[data-testid="pixel-format-select"]',
  keyframeIntervalInput: '[data-testid="keyframe-interval"], input[name="keyframeInterval"]',

  // Audio Codec
  audioCodecSelect: '[data-testid="audio-codec-select"]',
  audioBitrateInput: '[data-testid="audio-bitrate"], input[name="audioBitrate"]',
  sampleRateSelect: '[data-testid="sample-rate-select"]',
  channelsSelect: '[data-testid="channels-select"]',

  // Filters
  addVideoFilterBtn: 'button:has-text("Add Video Filter"), [data-testid="add-video-filter"]',
  addAudioFilterBtn: 'button:has-text("Add Audio Filter"), [data-testid="add-audio-filter"]',
  filterItem: '[data-testid="filter-item"]',
  filterTypeSelect: '[data-testid="filter-type-select"]',
  filterEnableToggle: '[data-testid="filter-enable-toggle"]',
  filterRemoveBtn: '[data-testid="filter-remove"]',
  filterOrderInput: '[data-testid="filter-order"]',

  // Stream Mapping
  addMappingBtn: 'button:has-text("Add Mapping"), [data-testid="add-mapping"]',
  mappingItem: '[data-testid="mapping-item"]',
  mappingStreamType: '[data-testid="mapping-stream-type"]',
  mappingInputIndex: '[data-testid="mapping-input-index"]',
  mappingStreamIndex: '[data-testid="mapping-stream-index"]',

  // Command Preview
  commandText: '[data-testid="command-text"], .command-text',
  commandCopyBtn: 'button:has-text("Copy"), [data-testid="copy-command"]',
  commandAnnotation: '[data-testid="command-annotation"]',
  commandWarning: '[data-testid="command-warning"]',
  annotatedFlag: '[data-testid="annotated-flag"]',

  // Presets
  presetSelect: '[data-testid="preset-select"]',
  presetCategory: (cat: string) => `[data-testid="preset-category-${cat}"]`,
  presetItem: (id: string) => `[data-testid="preset-${id}"]`,
  savePresetBtn: 'button:has-text("Save Preset"), [data-testid="save-preset"]',
  loadPresetBtn: 'button:has-text("Load Preset"), [data-testid="load-preset"]',

  // Execution
  executeBtn: 'button:has-text("Execute"), button:has-text("Run"), [data-testid="execute-btn"]',
  cancelJobBtn: 'button:has-text("Cancel"), [data-testid="cancel-job"]',
  progressBar: '[data-testid="job-progress"], .job-progress-bar',
  progressPercent: '[data-testid="progress-percent"]',
  progressSpeed: '[data-testid="progress-speed"]',
  progressEta: '[data-testid="progress-eta"]',
  jobStatus: '[data-testid="job-status"]',

  // Job Queue
  jobQueueList: '[data-testid="job-queue-list"]',
  jobQueueItem: '[data-testid="job-queue-item"]',
  queueStatusRunning: '[data-testid="queue-running"]',
  queueStatusQueued: '[data-testid="queue-queued"]',

  // Persistence
  saveConfigBtn: 'button:has-text("Save"), [data-testid="save-config"]',
  loadConfigBtn: 'button:has-text("Load"), [data-testid="load-config"]',
  configNameInput: '[data-testid="config-name"], input[name="configName"]',
  configList: '[data-testid="config-list"]',
  configItem: '[data-testid="config-item"]',
  deleteConfigBtn: '[data-testid="delete-config"]',

  // Explainer / Tooltips
  infoIcon: '[data-testid="info-icon"], .info-icon, .setting-explainer-trigger',
  tooltip: '[data-testid="tooltip"], .tooltip, [role="tooltip"]',
  tooltipText: '[data-testid="tooltip-text"], .tooltip-text',

  // ECM Integration
  profileSelect: '[data-testid="profile-select"]',
  applyToSelect: '[data-testid="apply-to-select"]',
  channelSelect: '[data-testid="channel-select"]',
  groupSelect: '[data-testid="group-select"]',

  // Hardware acceleration badges
  hwBadge: '[data-testid="hw-badge"]',
  hwUnavailable: '[data-testid="hw-unavailable"]',
  codecCategory: (cat: string) => `[data-testid="codec-category-${cat}"]`,
}

// =============================================================================
// Test Data
// =============================================================================

export const ffmpegTestData = {
  sampleInputs: {
    localFile: '/media/videos/input.mp4',
    httpUrl: 'http://example.com/stream.m3u8',
    rtspUrl: 'rtsp://camera.local/stream',
    device: '/dev/video0',
  },

  sampleOutputs: {
    mp4: '/media/output/result.mp4',
    mkv: '/media/output/result.mkv',
    hls: '/media/output/playlist.m3u8',
  },

  sampleConfigs: {
    basic: {
      name: 'Basic Transcode',
      input: { type: 'file' as const, path: '/media/input.mp4' },
      output: { path: '/media/output.mp4', format: 'mp4' as const },
      videoCodec: { codec: 'libx264' as const, rateControl: 'crf' as const, crf: 23 },
      audioCodec: { codec: 'aac' as const, bitrate: '192k' },
    },
    nvenc: {
      name: 'NVENC Transcode',
      input: {
        type: 'file' as const,
        path: '/media/input.mp4',
        hwaccel: { api: 'cuda' as const, outputFormat: 'cuda' },
      },
      output: { path: '/media/output.mp4', format: 'mp4' as const },
      videoCodec: { codec: 'h264_nvenc' as const, preset: 'p4', rateControl: 'vbr' as const, cq: 23 },
      audioCodec: { codec: 'aac' as const, bitrate: '192k' },
    },
  },

  expectedCommands: {
    basic: 'ffmpeg -i /media/input.mp4 -c:v libx264 -crf 23 -c:a aac -b:a 192k /media/output.mp4',
    nvenc: 'ffmpeg -hwaccel cuda -hwaccel_output_format cuda -i /media/input.mp4 -c:v h264_nvenc -preset p4 -rc vbr -cq 23 -c:a aac -b:a 192k /media/output.mp4',
    copy: 'ffmpeg -i /media/input.mp4 -c:v copy -c:a copy /media/output.mp4',
  },
}

// =============================================================================
// Helper Functions
// =============================================================================

/** Generate a unique test config name */
export function generateConfigName(): string {
  return `test-config-${Date.now()}-${Math.random().toString(36).substring(2, 7)}`
}

/** Generate a unique test job name */
export function generateJobName(): string {
  return `test-job-${Date.now()}-${Math.random().toString(36).substring(2, 7)}`
}
