import { useState, useMemo, useCallback, useRef } from 'react';
import type {
  FFMPEGBuilderState,
  CommandAnnotation,
  VideoFilter,
  AudioFilter,
  StreamMapping,
} from '../../types/ffmpegBuilder';
import { createStreamProfile } from '../../services/api';
import { useNotifications } from '../../contexts/NotificationContext';

// ---------------------------------------------------------------------------
// Command generation
// ---------------------------------------------------------------------------

interface GeneratedCommand {
  command: string;
  flags: CommandFlag[];
  annotations: CommandAnnotation[];
  warnings: string[];
}

interface CommandFlag {
  text: string;
  category: CommandAnnotation['category'];
  explanation: string;
}

// Codec/container compatibility map
const CONTAINER_CODECS: Record<string, Set<string>> = {
  ts: new Set(['libx264', 'libx265', 'h264_nvenc', 'hevc_nvenc', 'h264_qsv', 'hevc_qsv', 'h264_vaapi', 'hevc_vaapi', 'copy', 'aac', 'ac3', 'eac3']),
  hls: new Set(['libx264', 'libx265', 'h264_nvenc', 'hevc_nvenc', 'h264_qsv', 'hevc_qsv', 'h264_vaapi', 'hevc_vaapi', 'copy', 'aac', 'ac3', 'eac3']),
  dash: new Set(['libx264', 'libx265', 'h264_nvenc', 'hevc_nvenc', 'h264_qsv', 'hevc_qsv', 'h264_vaapi', 'hevc_vaapi', 'copy', 'aac', 'ac3', 'eac3']),
};

const VAAPI_CODECS = new Set(['h264_vaapi', 'hevc_vaapi']);

function buildVideoFilterString(filters: VideoFilter[]): string {
  return filters
    .filter(f => f.enabled)
    .sort((a, b) => a.order - b.order)
    .map(f => {
      switch (f.type) {
        case 'scale': return `scale=${f.params.width || -1}:${f.params.height || -1}`;
        case 'fps': return `fps=${f.params.fps}`;
        case 'deinterlace': return 'yadif';
        case 'format': return `format=${f.params.pixelFormat || 'nv12'}`;
        case 'hwupload': return 'hwupload';
        case 'custom': return String(f.params.filterString || '');
        default: return f.type;
      }
    })
    .filter(Boolean)
    .join(',');
}

function buildAudioFilterString(filters: AudioFilter[]): string {
  return filters
    .filter(f => f.enabled)
    .sort((a, b) => a.order - b.order)
    .map(f => {
      switch (f.type) {
        case 'volume': return `volume=${f.params.volume}`;
        case 'loudnorm': return `loudnorm=I=${f.params.I}:LRA=${f.params.LRA}:TP=${f.params.TP}`;
        case 'aresample': return `aresample=${f.params.sampleRate}`;
        case 'custom': return String(f.params.filterString || '');
        default: return f.type;
      }
    })
    .filter(Boolean)
    .join(',');
}

function buildStreamMappings(mappings: StreamMapping[]): string[] {
  return mappings.map(m => {
    if (m.streamType === 'all') {
      return `-map ${m.inputIndex}`;
    }
    const typeChar = m.streamType === 'video' ? 'v'
      : m.streamType === 'audio' ? 'a'
      : m.streamType === 'subtitle' ? 's'
      : 'd';
    return `-map ${m.inputIndex}:${typeChar}:${m.streamIndex}`;
  });
}

const STREAM_TYPE_LABELS: Record<string, string> = {
  video: 'video',
  audio: 'audio',
  subtitle: 'subtitle',
  data: 'data',
  all: 'all',
};

function describeStreamMapping(m: StreamMapping): string {
  if (m.streamType === 'all') {
    return `Select all streams from input ${m.inputIndex} — includes every video, audio, and subtitle track`;
  }
  const label = STREAM_TYPE_LABELS[m.streamType] || m.streamType;
  const ordinal = m.streamIndex === 0 ? 'first' : m.streamIndex === 1 ? 'second' : `#${m.streamIndex}`;
  return `Select the ${ordinal} ${label} stream from input ${m.inputIndex} — only this track will be included in the output`;
}

const CODEC_DESCRIPTIONS: Record<string, string> = {
  copy: 'Pass through without re-encoding — fastest, no quality loss',
  libx264: 'H.264 software encoder — widely compatible, good quality',
  libx265: 'H.265/HEVC software encoder — better compression, slower',
  'libvpx-vp9': 'VP9 software encoder — open format, good for web',
  'libaom-av1': 'AV1 encoder — best compression, very slow',
  libsvtav1: 'SVT-AV1 encoder — faster AV1 encoding',
  h264_nvenc: 'NVIDIA GPU H.264 encoder — hardware accelerated',
  hevc_nvenc: 'NVIDIA GPU H.265 encoder — hardware accelerated',
  h264_qsv: 'Intel Quick Sync H.264 encoder — hardware accelerated',
  hevc_qsv: 'Intel Quick Sync H.265 encoder — hardware accelerated',
  h264_vaapi: 'VA-API H.264 encoder — Linux hardware accelerated',
  hevc_vaapi: 'VA-API H.265 encoder — Linux hardware accelerated',
  aac: 'AAC audio — standard for IPTV and web streaming',
  ac3: 'Dolby Digital AC-3 — surround sound for broadcast',
  eac3: 'Enhanced AC-3 (Dolby Digital Plus) — improved surround sound for streaming',
};

function describeChannels(ch: number): string {
  switch (ch) {
    case 1: return 'Mono — single speaker, typically for voice or talk radio';
    case 2: return 'Stereo — left and right speakers, standard for most content';
    case 6: return '5.1 Surround — front left/right/center, rear left/right, and subwoofer';
    case 8: return '7.1 Surround — 5.1 plus two additional side/rear speakers';
    default: return `${ch} audio channels`;
  }
}

const FORMAT_DESCRIPTIONS: Record<string, string> = {
  ts: 'MPEG Transport Stream — standard for live TV, IPTV, and broadcast streaming',
  hls: 'HTTP Live Streaming — adaptive bitrate streaming for web and mobile delivery',
  dash: 'MPEG-DASH — adaptive bitrate streaming for cross-platform delivery',
  mpegts: 'MPEG Transport Stream — standard for live TV, IPTV, and broadcast streaming',
};

function describeFormat(fmt: string): string {
  return FORMAT_DESCRIPTIONS[fmt] || `Output container format: ${fmt}`;
}

function describeSampleRate(rate: number): string {
  switch (rate) {
    case 22050: return '22,050 Hz — low quality, suitable for speech only';
    case 44100: return '44,100 Hz — CD quality, standard for music';
    case 48000: return '48,000 Hz — standard for video and broadcast audio';
    case 96000: return '96,000 Hz — high-resolution audio for professional production';
    default: return `Sample rate: ${rate.toLocaleString()} Hz`;
  }
}

function describeOutputPath(path: string): string {
  if (path === 'pipe:1') return 'Stream to stdout — Dispatcharr reads this pipe to serve the stream to clients';
  if (path.startsWith('pipe:')) return `Pipe output to file descriptor ${path.slice(5)}`;
  const ext = path.split('.').pop()?.toLowerCase();
  if (ext) return `Save output to file "${path}"`;
  return `Output destination: ${path}`;
}

function describeForceKeyFrames(expr: string): string {
  // Parse common patterns like expr:gte(t,n_forced*N)
  const match = expr.match(/^expr:gte\(t,\s*n_forced\*(\d+(?:\.\d+)?)\)$/);
  if (match) {
    const secs = parseFloat(match[1]);
    if (secs === 0) return 'Force a keyframe at every opportunity — maximizes seeking precision so players can start playback from almost any point in the stream';
    return `Force a keyframe every ${secs} second${secs !== 1 ? 's' : ''} — guarantees regular keyframe spacing so players can reliably start playback or switch channels within ${secs}s`;
  }
  if (expr.startsWith('expr:')) return `Keyframe placement expression: ${expr} — dynamically controls where complete picture frames are inserted for seeking and segment alignment`;
  return `Force keyframes at specified positions: ${expr}`;
}

const MPEGTS_FLAG_DESCRIPTIONS: Record<string, string> = {
  pat_pmt_at_frames: 'write PAT/PMT tables at every keyframe for faster channel switching',
  resend_headers: 'resend stream headers periodically so clients can join mid-stream',
  initial_discontinuity: 'mark the start as a discontinuity so players reset their buffers cleanly',
  latm: 'use LATM packetization for AAC audio',
};

function describeOutputOption(key: string, value: string): string {
  if (key === 'mpegts_flags') {
    const flagNames = value.replace(/^\+/, '').split('+');
    const descriptions = flagNames
      .map(f => MPEGTS_FLAG_DESCRIPTIONS[f])
      .filter(Boolean);
    if (descriptions.length > 0) {
      return `MPEG-TS stream flags — ${descriptions.join('; ')}`;
    }
    return `MPEG-TS stream flags: ${value}`;
  }
  if (key === 'hls_time') return `HLS segment duration: ${value} seconds — shorter segments mean lower latency but more HTTP requests`;
  if (key === 'hls_list_size') return `HLS playlist size: keep the last ${value} segments in the playlist (0 = keep all)`;
  if (key === 'hls_flags') return `HLS flags: ${value}`;
  if (key === 'segment_time') return `Segment duration: ${value} seconds`;
  return `Output option: ${key} = ${value}`;
}

// Human-readable annotations for well-known IPTV flags
const IPTV_FLAG_EXPLANATIONS: Record<string, string> = {
  fflags: 'Stream handling behavior flags',
  '+genpts+discardcorrupt': 'Fix missing timing data on packets, and throw away damaged packets instead of passing them to the player (prevents glitches and freezes)',
  '+genpts': 'Fix missing timing data — some streams lack proper timestamps, which causes playback to stutter or fail. This reconstructs them automatically',
  '+discardcorrupt': 'Throw away damaged packets instead of passing them to the player — prevents visual glitches and audio pops from bad data',
  err_detect: 'How FFmpeg handles errors in the stream data',
  ignore_err: 'Keep playing when the stream has errors instead of stopping — essential for live IPTV where minor glitches are expected',
  reconnect: 'Automatically reconnect if the stream drops',
  reconnect_streamed: 'Also reconnect for live streams (not just downloadable files) — required for IPTV since streams are not seekable',
  reconnect_delay_max: 'Maximum seconds to wait between reconnection attempts — after a disconnect, FFmpeg retries with increasing delays up to this limit',
  analyzeduration: 'How long FFmpeg inspects the stream before playing',
  probesize: 'How much data FFmpeg reads to detect the stream format',
  thread_queue_size: 'How many packets to buffer in memory per input stream — larger values prevent dropped packets on slow or bursty connections',
};

function getInputOptionExplanation(key: string, value: string): string {
  const lookup = IPTV_FLAG_EXPLANATIONS[key];
  if (key === 'reconnect' || key === 'reconnect_streamed') {
    return lookup || `Input option: -${key} ${value}`;
  }
  if (key === 'reconnect_delay_max') {
    return lookup ? `${lookup} (${value}s)` : `Max reconnect delay: ${value}s`;
  }
  if (key === 'analyzeduration') {
    const secs = (Number(value) / 1_000_000).toFixed(1);
    return lookup ? `${lookup} (${secs} seconds) — longer values help with complex streams but add startup delay` : `Analyze duration: ${value}µs`;
  }
  if (key === 'probesize') {
    const mb = (Number(value) / 1_000_000).toFixed(1);
    return lookup ? `${lookup} (${mb} MB) — larger values detect more stream details but slow down initial connection` : `Probe size: ${value} bytes`;
  }
  if (key === 'thread_queue_size') {
    return lookup ? `${lookup} (${value} packets)` : `Thread queue size: ${value}`;
  }
  if (lookup) return `${lookup}: ${value}`;
  return `Input option: -${key} ${value}`;
}

function getGlobalOptionExplanation(key: string, value: string): string {
  const flagExplanation = IPTV_FLAG_EXPLANATIONS[key];
  const valueExplanation = IPTV_FLAG_EXPLANATIONS[value];
  if (key === 'err_detect' && value === 'ignore_err') {
    return 'Keep playing when stream errors occur instead of stopping — essential for live IPTV where minor glitches happen naturally';
  }
  if (flagExplanation && valueExplanation) return `${flagExplanation} — ${valueExplanation}`;
  if (flagExplanation) return `${flagExplanation}: ${value}`;
  return `Global option: -${key} ${value}`;
}

export function generateCommand(config: FFMPEGBuilderState): GeneratedCommand {
  const flags: CommandFlag[] = [];
  const warnings: string[] = [];
  const parts: string[] = ['ffmpeg'];

  flags.push({ text: 'ffmpeg', category: 'global', explanation: 'The FFmpeg tool — converts, streams, and processes audio/video. This is the engine that powers all stream transcoding and passthrough in Dispatcharr' });

  // Overwrite flag
  if (config.output?.overwrite) {
    parts.push('-y');
    flags.push({ text: '-y', category: 'global', explanation: 'Overwrite output without prompting — required for automated streaming so FFmpeg never pauses waiting for user input' });
  }

  // Global options (fflags, err_detect, etc.)
  if (config.globalOptions) {
    for (const [key, value] of Object.entries(config.globalOptions)) {
      parts.push(`-${key} ${value}`);
      flags.push({
        text: `-${key} ${value}`,
        category: 'global',
        explanation: getGlobalOptionExplanation(key, value),
      });
    }
  }

  // Hardware acceleration
  if (config.input?.hwaccel && config.input.hwaccel.api !== 'none') {
    const hw = config.input.hwaccel;
    if (hw.api === 'vaapi' && hw.device) {
      parts.push(`-vaapi_device ${hw.device}`);
      flags.push({ text: `-vaapi_device ${hw.device}`, category: 'input', explanation: `Which GPU to use for hardware acceleration (${hw.device}) — on systems with multiple GPUs, this selects the right one` });
    }
    parts.push(`-hwaccel ${hw.api}`);
    const hwaccelDescriptions: Record<string, string> = {
      cuda: 'Use NVIDIA GPU to decode video — dramatically reduces CPU usage, freeing it for other tasks. Requires an NVIDIA GPU with NVDEC support',
      vaapi: 'Use Linux VA-API to decode video on the GPU — reduces CPU usage on Intel/AMD systems. Common in Linux servers and NAS devices',
      qsv: 'Use Intel Quick Sync to decode video — leverages the built-in GPU on Intel CPUs for fast, low-power decoding',
      d3d11va: 'Use Windows Direct3D 11 to decode video on the GPU — Windows hardware acceleration',
      dxva2: 'Use Windows DXVA2 to decode video on the GPU — older Windows hardware acceleration',
      videotoolbox: 'Use Apple VideoToolbox to decode video — macOS/iOS hardware acceleration using Apple silicon or discrete GPU',
    };
    flags.push({ text: `-hwaccel ${hw.api}`, category: 'input', explanation: hwaccelDescriptions[hw.api] || `Use ${hw.api.toUpperCase()} hardware acceleration for decoding — offloads video processing from CPU to GPU for faster, more efficient transcoding` });
    if (hw.outputFormat) {
      parts.push(`-hwaccel_output_format ${hw.outputFormat}`);
      flags.push({ text: `-hwaccel_output_format ${hw.outputFormat}`, category: 'input', explanation: `Keep decoded video frames in GPU memory (${hw.outputFormat} format) — avoids copying data back to CPU between decoding and encoding, which is much faster` });
    }
  }

  // Input
  if (config.input?.format) {
    parts.push(`-f ${config.input.format}`);
    flags.push({ text: `-f ${config.input.format}`, category: 'input', explanation: `Tell FFmpeg the input is in "${config.input.format}" format — normally FFmpeg auto-detects, but forcing the format helps with streams that don't identify themselves correctly` });
  }

  // Input options (reconnect, analyzeduration, probesize, thread_queue_size, etc.)
  if (config.input?.options) {
    for (const [key, value] of Object.entries(config.input.options)) {
      parts.push(`-${key} ${value}`);
      flags.push({
        text: `-${key} ${value}`,
        category: 'input',
        explanation: getInputOptionExplanation(key, value),
      });
    }
  }

  const inputPath = config.input?.path || 'input';
  parts.push(`-i ${inputPath}`);
  flags.push({ text: `-i ${inputPath}`, category: 'input', explanation: inputPath === '{streamUrl}' || inputPath.startsWith('http') || inputPath.startsWith('rtmp')
    ? `Input stream URL — the live source that FFmpeg will read from. Dispatcharr replaces {streamUrl} with the actual channel URL at runtime`
    : `Input source: ${inputPath} — the file or stream that FFmpeg will read and process` });

  // Stream mappings
  if (config.streamMappings?.length) {
    const mapArgs = buildStreamMappings(config.streamMappings);
    for (let i = 0; i < mapArgs.length; i++) {
      const arg = mapArgs[i];
      const m = config.streamMappings[i];
      parts.push(arg);
      flags.push({ text: arg, category: 'global', explanation: describeStreamMapping(m) });
    }
  }

  // Video codec
  const vc = config.videoCodec;
  if (vc) {
    parts.push(`-c:v ${vc.codec}`);
    flags.push({ text: `-c:v ${vc.codec}`, category: 'video', explanation: CODEC_DESCRIPTIONS[vc.codec] || `Video codec: ${vc.codec}` });

    if (vc.codec !== 'copy') {
      if (vc.preset) {
        parts.push(`-preset ${vc.preset}`);
        const presetExplanations: Record<string, string> = {
          ultrafast: 'Fastest encoding, lowest quality per bitrate — good for testing or when CPU is very limited',
          superfast: 'Very fast encoding with slightly better quality than ultrafast',
          veryfast: 'Fast encoding, reasonable quality — a good choice when speed matters more than compression',
          faster: 'Faster than default, slight quality tradeoff',
          fast: 'Slightly faster than default with minimal quality loss',
          medium: 'Balanced speed and quality — the default choice that works well for most IPTV streams',
          slow: 'Better compression at the cost of slower encoding — same visual quality uses less bandwidth',
          slower: 'High compression, significantly slower encoding — best for bandwidth-constrained setups',
          veryslow: 'Maximum compression, very slow encoding — squeeze every bit of quality from the bandwidth',
          // NVENC presets
          p1: 'NVIDIA fastest preset — minimum encoding latency, lowest compression',
          p4: 'NVIDIA balanced preset — good mix of speed and quality for live streaming',
          p7: 'NVIDIA highest quality preset — best compression, higher GPU usage',
          // QSV presets
          veryfast_qsv: 'Intel Quick Sync fast preset — minimal latency',
          medium_qsv: 'Intel Quick Sync balanced preset — good for IPTV',
        };
        const presetDesc = presetExplanations[vc.preset] || `Encoding speed/quality tradeoff: "${vc.preset}" — faster presets use less CPU but produce larger streams, slower presets compress better but need more processing power`;
        flags.push({ text: `-preset ${vc.preset}`, category: 'video', explanation: presetDesc });
      }
      if (vc.rateControl === 'crf' && vc.crf !== undefined) {
        parts.push(`-crf ${vc.crf}`);
        let crfQuality = 'balanced';
        if (vc.crf <= 18) crfQuality = 'visually lossless (very high quality)';
        else if (vc.crf <= 23) crfQuality = 'good quality (recommended for most content)';
        else if (vc.crf <= 28) crfQuality = 'acceptable quality (saves bandwidth)';
        else crfQuality = 'low quality (significant compression artifacts)';
        flags.push({ text: `-crf ${vc.crf}`, category: 'video', explanation: `Quality level: ${vc.crf} — ${crfQuality}. The encoder automatically adjusts bandwidth to maintain this quality target. Lower numbers = better picture but more bandwidth, higher numbers = more compression` });
      }
      if (vc.rateControl === 'cbr' && vc.bitrate) {
        parts.push(`-b:v ${vc.bitrate}`);
        flags.push({ text: `-b:v ${vc.bitrate}`, category: 'video', explanation: `Fixed video bandwidth: ${vc.bitrate} — the stream will use exactly this much bandwidth at all times. Predictable and easy to plan for, but wastes bandwidth on simple scenes and may not have enough for complex ones` });
      }
      if (vc.rateControl === 'vbr' && vc.bitrate) {
        parts.push(`-b:v ${vc.bitrate}`);
        flags.push({ text: `-b:v ${vc.bitrate}`, category: 'video', explanation: `Target video bandwidth: ${vc.bitrate} — the encoder aims for this average, using more bandwidth for complex scenes (action, fast motion) and less for simple ones (static shots)` });
        if (vc.maxBitrate) {
          parts.push(`-maxrate ${vc.maxBitrate}`);
          flags.push({ text: `-maxrate ${vc.maxBitrate}`, category: 'video', explanation: `Bandwidth ceiling: ${vc.maxBitrate} — the encoder will never exceed this, even during complex scenes. Prevents bandwidth spikes that could cause buffering for viewers` });
        }
        if (vc.bufsize) {
          parts.push(`-bufsize ${vc.bufsize}`);
          flags.push({ text: `-bufsize ${vc.bufsize}`, category: 'video', explanation: `Rate control buffer: ${vc.bufsize} — how much data the encoder can "bank" before hitting the bitrate limit. Larger buffers allow more quality variation between scenes, smaller buffers keep bandwidth more consistent` });
        }
      }
      if (vc.rateControl === 'cq' && vc.cq !== undefined) {
        parts.push(`-cq ${vc.cq}`);
        flags.push({ text: `-cq ${vc.cq}`, category: 'video', explanation: `NVIDIA GPU quality level: ${vc.cq} — similar to CRF but for NVIDIA hardware encoding. Lower values = better picture quality with more bandwidth, higher values = more compression. Values 19-28 work well for most IPTV content` });
      }
      if (vc.rateControl === 'qp' && vc.qp !== undefined) {
        parts.push(`-qp ${vc.qp}`);
        flags.push({ text: `-qp ${vc.qp}`, category: 'video', explanation: `Fixed compression level: ${vc.qp} — every frame gets the same amount of compression regardless of complexity. Lower = better quality, higher = more compression. Unlike CRF, this doesn't adapt to scene content, so bandwidth varies with picture complexity` });
      }
      if (vc.rateControl === 'global_quality' && vc.globalQuality !== undefined) {
        parts.push(`-global_quality ${vc.globalQuality}`);
        flags.push({ text: `-global_quality ${vc.globalQuality}`, category: 'video', explanation: `Intel GPU quality level: ${vc.globalQuality} — controls the quality target for Intel Quick Sync hardware encoding. Lower values = better picture quality with more bandwidth. Values around 20-28 work well for most IPTV content` });
      }
      if (vc.pixelFormat) {
        parts.push(`-pix_fmt ${vc.pixelFormat}`);
        const pixFmtExplanations: Record<string, string> = {
          yuv420p: 'Most compatible color format — works on virtually all devices, standard for streaming and web playback',
          yuv444p: 'Full color detail — preserves all color information (good for text/graphics), but not all players support it',
          yuv420p10le: '10-bit color depth — smoother gradients and less banding (subtle color steps) than standard 8-bit, used for HDR content',
          nv12: 'GPU-optimized color format — same visual quality as yuv420p but stored in a layout that hardware encoders can process faster',
          p010le: '10-bit GPU-optimized format — like nv12 but with 10-bit color depth for HDR',
        };
        const pixDesc = pixFmtExplanations[vc.pixelFormat] || `Color format: ${vc.pixelFormat} — determines how color data is stored. This affects compatibility with players and visual quality`;
        flags.push({ text: `-pix_fmt ${vc.pixelFormat}`, category: 'video', explanation: pixDesc });
      }
      if (vc.profile) {
        parts.push(`-profile:v ${vc.profile}`);
        const profileExplanations: Record<string, string> = {
          baseline: 'Baseline profile — maximum compatibility, works on older phones and low-power devices. No B-frames or advanced features',
          main: 'Main profile — good balance of compatibility and compression. Supports B-frames for better quality. Standard for IPTV and most streaming',
          high: 'High profile — best compression with advanced features (8x8 transforms, custom quantization). Supported by all modern devices',
          high10: 'High 10-bit profile — same as High but with 10-bit color depth for smoother gradients and HDR content',
        };
        const profDesc = profileExplanations[vc.profile] || `Video profile: "${vc.profile}" — controls which encoder features are available. Higher profiles compress better but require more capable playback devices`;
        flags.push({ text: `-profile:v ${vc.profile}`, category: 'video', explanation: profDesc });
      }
      if (vc.level) {
        parts.push(`-level ${vc.level}`);
        const levelExplanations: Record<string, string> = {
          '3.0': 'Level 3.0 — up to 720×480 at 30fps. Suitable for SD content and older devices',
          '3.1': 'Level 3.1 — up to 1280×720 at 30fps. Standard for 720p HD content',
          '4.0': 'Level 4.0 — up to 1920×1080 at 30fps. Minimum for 1080p Full HD',
          '4.1': 'Level 4.1 — up to 1920×1080 at 30fps with higher bitrate. Recommended for IPTV 1080p streaming',
          '4.2': 'Level 4.2 — up to 1920×1080 at 60fps. For high-framerate 1080p content like sports',
          '5.0': 'Level 5.0 — up to 3840×2160 at 30fps. Entry level for 4K content',
          '5.1': 'Level 5.1 — up to 3840×2160 at 60fps. Standard for 4K streaming',
        };
        const levelDesc = levelExplanations[vc.level] || `Codec level: ${vc.level} — sets the maximum resolution, framerate, and bitrate the stream can use. Players that support this level (and above) can decode the stream`;
        flags.push({ text: `-level ${vc.level}`, category: 'video', explanation: levelDesc });
      }
      if (vc.tune) {
        parts.push(`-tune ${vc.tune}`);
        const tuneExplanations: Record<string, string> = {
          film: 'Tuned for live-action movies — preserves film grain and fine detail in cinematic content',
          animation: 'Tuned for cartoons and animation — optimizes for flat areas and sharp edges typical in animated content',
          grain: 'Tuned for grainy/noisy footage — preserves the grain texture instead of smoothing it away',
          stillimage: 'Tuned for slideshows or mostly-static content — optimizes compression for images that rarely change',
          fastdecode: 'Tuned for weak playback devices — avoids encoding features that are hard to decode, reducing player CPU usage',
          zerolatency: 'Tuned for real-time streaming — eliminates encoding delay by disabling frame buffering. Essential for interactive or ultra-low-latency streams',
          psnr: 'Tuned to maximize measured quality metrics (PSNR) — for benchmarking rather than viewing',
          ssim: 'Tuned to maximize perceptual quality metrics (SSIM) — for benchmarking rather than viewing',
          ll: 'NVIDIA low latency — reduces encoding delay for live streaming',
          ull: 'NVIDIA ultra-low latency — minimum possible encoding delay',
        };
        const tuneDesc = tuneExplanations[vc.tune] || `Optimized for "${vc.tune}" content — adjusts encoder decisions to produce the best results for this type of material`;
        flags.push({ text: `-tune ${vc.tune}`, category: 'video', explanation: tuneDesc });
      }
    }

    // Keyframe control flags — emitted regardless of copy mode
    // Keyframes (I-frames) are complete picture frames that don't depend on other frames.
    // Players need a keyframe to start playback or seek, so their spacing affects
    // channel-switch speed, seeking responsiveness, and segment boundaries for HLS/DASH.
    if (vc.keyframeInterval) {
      parts.push(`-g ${vc.keyframeInterval}`);
      const gopSec = vc.keyframeInterval >= 24 ? ` (~${(vc.keyframeInterval / 24).toFixed(1)}s at 24fps)` : '';
      flags.push({ text: `-g ${vc.keyframeInterval}`, category: 'video', explanation: `Insert a keyframe every ${vc.keyframeInterval} frames${gopSec}. Keyframes are complete picture frames that allow players to start playback — closer spacing means faster channel switching and seeking, but uses more bandwidth` });
    }
    if (vc.keyintMin !== undefined) {
      parts.push(`-keyint_min ${vc.keyintMin}`);
      flags.push({ text: `-keyint_min ${vc.keyintMin}`, category: 'video', explanation: `Require at least ${vc.keyintMin} frames between keyframes. Prevents the encoder from inserting extra keyframes too close together, keeping segment sizes consistent` });
    }
    if (vc.scThreshold !== undefined) {
      parts.push(`-sc_threshold ${vc.scThreshold}`);
      flags.push({ text: `-sc_threshold ${vc.scThreshold}`, category: 'video', explanation: vc.scThreshold === 0
        ? 'Disable automatic keyframe insertion on scene changes — keyframes are placed only at fixed intervals, keeping segment sizes predictable for IPTV delivery'
        : `Scene change sensitivity: ${vc.scThreshold} — the encoder normally inserts a keyframe when it detects a scene cut. Higher values make it less sensitive, lower values insert keyframes more often on cuts` });
    }
    if (vc.forceKeyFrames) {
      parts.push(`-force_key_frames ${vc.forceKeyFrames}`);
      flags.push({ text: `-force_key_frames ${vc.forceKeyFrames}`, category: 'video', explanation: describeForceKeyFrames(vc.forceKeyFrames) });
    }
  }

  // Video filters
  if (config.videoFilters?.length) {
    const vfStr = buildVideoFilterString(config.videoFilters);
    if (vfStr) {
      parts.push(`-vf "${vfStr}"`);
      flags.push({ text: `-vf "${vfStr}"`, category: 'filter', explanation: `Video processing pipeline — applies these visual transformations in order: ${vfStr}. Filters run left-to-right, each one modifying the picture before passing it to the next` });
    }
  }

  // Audio codec
  const ac = config.audioCodec;
  if (ac) {
    parts.push(`-c:a ${ac.codec}`);
    flags.push({ text: `-c:a ${ac.codec}`, category: 'audio', explanation: CODEC_DESCRIPTIONS[ac.codec] || `Audio codec: ${ac.codec}` });

    if (ac.codec !== 'copy') {
      if (ac.bitrate) {
        parts.push(`-b:a ${ac.bitrate}`);
        flags.push({ text: `-b:a ${ac.bitrate}`, category: 'audio', explanation: `Audio quality target: ${ac.bitrate}ps — higher values mean better audio fidelity` });
      }
      if (ac.sampleRate) {
        parts.push(`-ar ${ac.sampleRate}`);
        flags.push({ text: `-ar ${ac.sampleRate}`, category: 'audio', explanation: describeSampleRate(ac.sampleRate) });
      }
      if (ac.channels) {
        parts.push(`-ac ${ac.channels}`);
        flags.push({ text: `-ac ${ac.channels}`, category: 'audio', explanation: describeChannels(ac.channels) });
      }
      if (ac.profile) {
        parts.push(`-profile:a ${ac.profile}`);
        const audioProfileExplanations: Record<string, string> = {
          'aac_low': 'AAC-LC (Low Complexity) — the most widely supported AAC profile, works on all devices',
          'aac_he': 'HE-AAC — high-efficiency audio, sounds better than AAC-LC at very low bitrates (under 64k)',
          'aac_he_v2': 'HE-AAC v2 — even more efficient than HE-AAC for stereo at very low bitrates (under 48k)',
        };
        const aProfDesc = audioProfileExplanations[ac.profile] || `Audio encoding profile: "${ac.profile}" — determines which encoding features are used and affects compatibility with playback devices`;
        flags.push({ text: `-profile:a ${ac.profile}`, category: 'audio', explanation: aProfDesc });
      }
    }
  }

  // Audio filters
  if (config.audioFilters?.length) {
    const afStr = buildAudioFilterString(config.audioFilters);
    if (afStr) {
      parts.push(`-af "${afStr}"`);
      flags.push({ text: `-af "${afStr}"`, category: 'filter', explanation: `Audio processing pipeline — applies these audio transformations in order: ${afStr}. Filters run left-to-right, each one modifying the audio before passing it to the next` });
    }
  }

  // Output format
  if (config.output?.format) {
    parts.push(`-f ${config.output.format}`);
    flags.push({ text: `-f ${config.output.format}`, category: 'output', explanation: describeFormat(config.output.format) });
  }

  // Output options
  if (config.output?.options) {
    for (const [key, value] of Object.entries(config.output.options)) {
      parts.push(`-${key} ${value}`);
      flags.push({ text: `-${key} ${value}`, category: 'output', explanation: describeOutputOption(key, String(value)) });
    }
  }

  // Output path
  const outputPath = config.output?.path || 'output';
  parts.push(outputPath);
  flags.push({ text: outputPath, category: 'output', explanation: describeOutputPath(outputPath) });

  // Warnings
  const format = config.output?.format || 'ts';
  const allowed = CONTAINER_CODECS[format];
  if (allowed && vc && vc.codec !== 'copy' && !allowed.has(vc.codec)) {
    warnings.push(`Warning: ${vc.codec} is not recommended for ${format} container`);
  }

  if (ac?.codec === 'copy' && config.audioFilters?.some(f => f.enabled)) {
    warnings.push('Warning: Audio filters cannot apply when audio codec is set to copy');
  }

  if (VAAPI_CODECS.has(vc?.codec || '') && config.videoFilters?.some(f => f.enabled)) {
    const hasHwupload = config.videoFilters.some(f => f.type === 'hwupload' && f.enabled);
    if (!hasHwupload) {
      warnings.push('Warning: VAAPI encoding with video filters requires hwupload filter');
    }
  }

  const annotations: CommandAnnotation[] = flags.map(f => ({
    flag: f.text,
    explanation: f.explanation,
    category: f.category,
  }));

  return {
    command: parts.join(' '),
    flags,
    annotations,
    warnings,
  };
}

// ---------------------------------------------------------------------------
// CommandPreview component
// ---------------------------------------------------------------------------

interface CommandPreviewProps {
  config: FFMPEGBuilderState;
  annotated?: boolean;
}

export function CommandPreview({ config, annotated: initialAnnotated }: CommandPreviewProps) {
  const notifications = useNotifications();
  const [highlightedIdx, setHighlightedIdx] = useState<number | null>(null);
  const [showAnnotated, setShowAnnotated] = useState(initialAnnotated ?? false);
  const [showPushForm, setShowPushForm] = useState(false);
  const [profileName, setProfileName] = useState('');
  const [pushStatus, setPushStatus] = useState<'idle' | 'pushing'>('idle');
  const nameInputRef = useRef<HTMLInputElement>(null);

  const generated = useMemo(() => {
    if (!config) return null;
    return generateCommand(config);
  }, [config]);

  const handleCopy = useCallback(async () => {
    if (!generated) return;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(generated.command);
      }
      notifications.success('Command copied to clipboard');
    } catch {
      notifications.error('Failed to copy to clipboard');
    }
  }, [generated, notifications]);

  const handlePushToDispatcharr = useCallback(async () => {
    if (!generated || !profileName.trim()) return;
    setPushStatus('pushing');
    try {
      // Split "ffmpeg <params>" into command="ffmpeg" and parameters="<params>"
      const fullCmd = generated.command;
      const spaceIdx = fullCmd.indexOf(' ');
      const command = spaceIdx > 0 ? fullCmd.substring(0, spaceIdx) : fullCmd;
      const parameters = spaceIdx > 0 ? fullCmd.substring(spaceIdx + 1) : '';
      await createStreamProfile({
        name: profileName.trim(),
        command,
        parameters,
        is_active: true,
      });
      notifications.success(`Stream profile "${profileName.trim()}" created in Dispatcharr`);
      setShowPushForm(false);
      setProfileName('');
      setPushStatus('idle');
    } catch (e: unknown) {
      notifications.error(e instanceof Error ? e.message : 'Failed to create profile');
      setPushStatus('idle');
    }
  }, [generated, profileName, notifications]);

  const handleAnnotationClick = (idx: number) => {
    setHighlightedIdx(idx);
  };

  if (!config) {
    return (
      <div data-testid="command-preview" className="command-preview">
        <div className="command-empty">No configuration — configure your input to begin</div>
      </div>
    );
  }

  if (!generated) {
    return (
      <div data-testid="command-preview" className="command-preview">
        <div>Command Preview</div>
      </div>
    );
  }

  return (
    <div data-testid="command-preview" className="command-preview">
      <div className="command-preview-header">
        <span>Command Preview</span>
        <div className="command-preview-actions">
          <button
            type="button"
            aria-label="Toggle annotated view"
            onClick={() => setShowAnnotated(!showAnnotated)}
          >
            {showAnnotated ? 'Plain' : 'Annotated'}
          </button>
          <button type="button" aria-label="Copy" onClick={handleCopy}>
            Copy
          </button>
          <button
            type="button"
            data-testid="push-to-dispatcharr"
            aria-label="Push to Dispatcharr"
            onClick={() => {
              setShowPushForm(!showPushForm);
              setPushStatus('idle');
              setTimeout(() => nameInputRef.current?.focus(), 50);
            }}
          >
            <span className="material-icons" style={{ fontSize: '0.875rem', verticalAlign: 'middle', marginRight: '0.25rem' }}>cloud_upload</span>
            Push to Dispatcharr
          </button>
        </div>
      </div>

      {/* Push to Dispatcharr inline form */}
      {showPushForm && (
        <div data-testid="push-form" className="push-to-dispatcharr-form">
          <div className="push-form-row">
            <input
              ref={nameInputRef}
              type="text"
              data-testid="push-profile-name"
              className="push-form-input"
              placeholder="Stream profile name..."
              value={profileName}
              onChange={(e) => setProfileName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handlePushToDispatcharr();
                if (e.key === 'Escape') setShowPushForm(false);
              }}
              disabled={pushStatus === 'pushing'}
            />
            <button
              type="button"
              data-testid="push-confirm"
              className="btn-primary"
              onClick={handlePushToDispatcharr}
              disabled={!profileName.trim() || pushStatus === 'pushing'}
            >
              {pushStatus === 'pushing' ? 'Creating...' : 'Create Profile'}
            </button>
            <button
              type="button"
              className="btn-cancel"
              onClick={() => { setShowPushForm(false); setPushStatus('idle'); }}
            >
              <span className="material-icons" style={{ fontSize: '1rem' }}>close</span>
            </button>
          </div>
        </div>
      )}

      {/* Command text with clickable flags */}
      <div data-testid="command-text" className="command-text">
        {generated.flags.map((flag, i) => (
          <span
            key={i}
            data-testid="command-flag"
            className={highlightedIdx === i ? 'flag-highlight' : ''}
            data-highlighted={highlightedIdx === i ? 'true' : undefined}
            onMouseEnter={() => setHighlightedIdx(i)}
            onMouseLeave={() => setHighlightedIdx(null)}
          >
            {flag.text}
            {highlightedIdx === i && (
              <div role="tooltip" className="tooltip flag-tooltip">{flag.explanation}</div>
            )}
            {' '}
          </span>
        ))}
      </div>

      {/* Warnings */}
      {generated.warnings.map((w, i) => (
        <div key={i} data-testid="command-warning" className="command-warning">
          {w}
        </div>
      ))}

      {/* Annotation list (toggled by Annotated button) */}
      {showAnnotated && (
        <div data-testid="annotation-list" className="annotation-list">
          {generated.annotations.map((ann, i) => (
            <div
              key={i}
              data-testid="annotation-item"
              data-category={ann.category}
              className={`annotation-item category-${ann.category}${highlightedIdx === i ? ' highlighted' : ''}`}
              onClick={() => handleAnnotationClick(i)}
            >
              <code className="annotation-flag">{ann.flag}</code>
              <span data-testid="annotation-explanation" className="annotation-explanation">
                {ann.explanation}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
