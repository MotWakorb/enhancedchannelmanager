/**
 * Central data file mapping every FFMPEG option to its plain-English explanation.
 *
 * Used by the SettingExplainer component to show contextual help throughout
 * the Visual FFMPEG Syntax Builder.
 */
import type { ExplanationCategory, FFMPEGExplanation } from '../types/ffmpegBuilder';

// =============================================================================
// Video Codec Explanations
// =============================================================================

export const videoCodecExplanations: Record<string, FFMPEGExplanation> = {
  libx264: {
    title: 'H.264 (x264)',
    description: 'The most widely compatible video codec. Works on virtually every device and browser.',
    useCases: ['Web streaming', 'General-purpose encoding', 'Maximum compatibility'],
    tradeoffs: 'Great compatibility but slower than hardware encoders. Good quality-to-size ratio.',
    example: '-c:v libx264',
  },
  libx265: {
    title: 'H.265/HEVC (x265)',
    description: 'Next-generation codec offering ~50% smaller files at the same quality as H.264.',
    useCases: ['Archival', '4K content', 'Bandwidth-limited streaming'],
    tradeoffs: 'Much slower to encode than H.264. Some older devices cannot decode it.',
    example: '-c:v libx265',
  },
  'libvpx-vp9': {
    title: 'VP9',
    description: 'Google\'s open-source codec. Similar efficiency to H.265 with broader browser support.',
    useCases: ['YouTube-style streaming', 'WebM containers', 'Royalty-free encoding'],
    tradeoffs: 'Very slow encoding speed. Excellent for web delivery.',
    example: '-c:v libvpx-vp9',
  },
  'libaom-av1': {
    title: 'AV1 (libaom)',
    description: 'The newest open-source codec with the best compression efficiency available.',
    useCases: ['Future-proof archival', 'Ultra-low bitrate streaming'],
    tradeoffs: 'Extremely slow encoding. Best quality per bit but impractical for real-time.',
    example: '-c:v libaom-av1',
  },
  libsvtav1: {
    title: 'AV1 (SVT-AV1)',
    description: 'Intel\'s fast AV1 encoder. Much faster than libaom with competitive quality.',
    useCases: ['Production AV1 encoding', 'Streaming services'],
    tradeoffs: 'Faster than libaom but still slower than H.264. Excellent quality.',
    example: '-c:v libsvtav1',
  },
  h264_nvenc: {
    title: 'H.264 (NVIDIA NVENC)',
    description: 'Uses your NVIDIA GPU for encoding. Much faster than CPU with slight quality trade-off at the same bitrate.',
    useCases: ['Real-time encoding', 'Live streaming', 'Quick transcodes'],
    tradeoffs: 'Very fast but slightly lower quality per bit than x264. Requires NVIDIA GPU.',
    example: '-c:v h264_nvenc',
  },
  hevc_nvenc: {
    title: 'H.265 (NVIDIA NVENC)',
    description: 'HEVC encoding on NVIDIA GPU. Fast hardware encoding with next-gen codec efficiency.',
    useCases: ['4K live streaming', 'Fast HEVC transcodes'],
    tradeoffs: 'Fast but lower quality than x265 at same bitrate. Requires NVIDIA GPU with NVENC.',
    example: '-c:v hevc_nvenc',
  },
  h264_qsv: {
    title: 'H.264 (Intel QSV)',
    description: 'Uses Intel integrated GPU for encoding. Available on most Intel CPUs.',
    useCases: ['Server transcoding', 'Energy-efficient encoding'],
    tradeoffs: 'Fast and power-efficient. Quality between software and NVENC.',
    example: '-c:v h264_qsv',
  },
  hevc_qsv: {
    title: 'H.265 (Intel QSV)',
    description: 'HEVC encoding on Intel GPU. Combines hardware speed with HEVC efficiency.',
    useCases: ['Server-side HEVC transcoding'],
    tradeoffs: 'Requires Intel CPU with QSV support (6th gen or newer).',
    example: '-c:v hevc_qsv',
  },
  h264_vaapi: {
    title: 'H.264 (VAAPI)',
    description: 'Linux hardware encoding using VAAPI. Works with Intel, AMD, and some NVIDIA GPUs.',
    useCases: ['Linux server transcoding', 'AMD GPU encoding'],
    tradeoffs: 'Requires Linux with proper GPU drivers. Good performance.',
    example: '-c:v h264_vaapi',
  },
  hevc_vaapi: {
    title: 'H.265 (VAAPI)',
    description: 'HEVC encoding via VAAPI on Linux. Hardware-accelerated next-gen encoding.',
    useCases: ['Linux HEVC transcoding'],
    tradeoffs: 'Requires Linux with VAAPI-compatible GPU and drivers.',
    example: '-c:v hevc_vaapi',
  },
  copy: {
    title: 'Stream Copy',
    description: 'Copies the video stream without re-encoding. Instant and lossless.',
    useCases: ['Remuxing containers', 'Cutting without re-encode', 'Extracting streams'],
    tradeoffs: 'No quality loss and very fast, but cannot change codec settings or apply filters.',
    example: '-c:v copy',
  },
};

// =============================================================================
// Audio Codec Explanations
// =============================================================================

export const audioCodecExplanations: Record<string, FFMPEGExplanation> = {
  aac: {
    title: 'AAC',
    description: 'The standard audio codec for MP4 files. Universal playback support.',
    useCases: ['MP4 output', 'Web streaming', 'General purpose'],
    tradeoffs: 'Good quality at 128-256 kbps. The safe default for most use cases.',
    example: '-c:a aac -b:a 192k',
  },
  libmp3lame: {
    title: 'MP3 (LAME)',
    description: 'The classic audio format. Maximum compatibility with legacy devices.',
    useCases: ['Audio-only export', 'Legacy device support'],
    tradeoffs: 'Slightly worse quality than AAC at same bitrate. Maximum compatibility.',
    example: '-c:a libmp3lame -b:a 320k',
  },
  libvorbis: {
    title: 'Vorbis',
    description: 'Open-source audio codec for OGG and WebM containers.',
    useCases: ['WebM output', 'Open-source workflows'],
    tradeoffs: 'Good quality. Only works with OGG/WebM containers.',
    example: '-c:a libvorbis -b:a 192k',
  },
  libopus: {
    title: 'Opus',
    description: 'State-of-the-art audio codec. Best quality at low bitrates.',
    useCases: ['VoIP', 'Low-bitrate streaming', 'WebM output'],
    tradeoffs: 'Excellent quality even at 64 kbps. Works with WebM/MKV/OGG.',
    example: '-c:a libopus -b:a 128k',
  },
  ac3: {
    title: 'Dolby AC3',
    description: 'Standard surround sound codec for DVDs and some broadcast.',
    useCases: ['DVD authoring', 'Surround sound output'],
    tradeoffs: 'Supports 5.1 surround. Higher bitrates needed for quality.',
    example: '-c:a ac3 -b:a 384k',
  },
  eac3: {
    title: 'Dolby E-AC3',
    description: 'Enhanced AC3 with better compression and up to 7.1 channels.',
    useCases: ['Blu-ray authoring', 'Advanced surround sound'],
    tradeoffs: 'Better than AC3 at same bitrate. Less device support than AAC.',
    example: '-c:a eac3 -b:a 256k',
  },
  flac: {
    title: 'FLAC',
    description: 'Lossless audio compression. Perfect quality preservation.',
    useCases: ['Archival', 'Audio mastering', 'Lossless workflow'],
    tradeoffs: 'No quality loss but large files (50-70% of uncompressed).',
    example: '-c:a flac',
  },
  pcm_s16le: {
    title: 'PCM 16-bit',
    description: 'Uncompressed audio. CD quality at 16 bits per sample.',
    useCases: ['WAV output', 'Audio editing input'],
    tradeoffs: 'No compression, no quality loss, very large files.',
    example: '-c:a pcm_s16le',
  },
  pcm_s24le: {
    title: 'PCM 24-bit',
    description: 'Uncompressed audio. Studio quality at 24 bits per sample.',
    useCases: ['Professional audio', 'Studio mastering'],
    tradeoffs: 'No compression, highest quality, largest files.',
    example: '-c:a pcm_s24le',
  },
};

// =============================================================================
// Preset Explanations
// =============================================================================

export const presetExplanations: Record<string, FFMPEGExplanation> = {
  ultrafast: {
    title: 'Ultrafast',
    description: 'Fastest encoding speed. Lowest compression efficiency.',
    tradeoffs: 'Much larger files but encoding is very fast. Good for testing.',
  },
  superfast: {
    title: 'Superfast',
    description: 'Very fast encoding with slightly better compression than ultrafast.',
    tradeoffs: 'Still produces large files but noticeably faster than "fast".',
  },
  veryfast: {
    title: 'Very Fast',
    description: 'Fast encoding with reasonable compression.',
    tradeoffs: 'Good for real-time or near-real-time use cases.',
  },
  faster: {
    title: 'Faster',
    description: 'Above-average speed with decent compression.',
    tradeoffs: 'Good balance for batch processing where speed matters.',
  },
  fast: {
    title: 'Fast',
    description: 'Slightly faster than medium with minimal quality impact.',
    tradeoffs: 'Recommended for general use when encoding speed matters.',
  },
  medium: {
    title: 'Medium',
    description: 'Default preset. Balanced speed and compression.',
    tradeoffs: 'The default choice when you have no specific speed requirement.',
  },
  slow: {
    title: 'Slow',
    description: 'Better compression at the cost of speed.',
    tradeoffs: 'Noticeably smaller files. Good for final delivery when time allows.',
  },
  slower: {
    title: 'Slower',
    description: 'High compression efficiency.',
    tradeoffs: 'Significantly smaller files. Use for archival or bandwidth-critical delivery.',
  },
  veryslow: {
    title: 'Very Slow',
    description: 'Maximum compression efficiency for software encoders.',
    tradeoffs: 'Smallest files possible but encoding takes much longer.',
  },
  // NVENC presets
  p1: {
    title: 'P1 (Fastest)',
    description: 'NVENC fastest preset. Minimal compression, maximum speed.',
    tradeoffs: 'Use for real-time streaming where GPU encoding latency matters.',
  },
  p4: {
    title: 'P4 (Medium)',
    description: 'NVENC balanced preset. Good quality/speed trade-off.',
    tradeoffs: 'Default recommendation for NVENC encoding.',
  },
  p7: {
    title: 'P7 (Slowest)',
    description: 'NVENC maximum quality preset. Best compression for GPU encoding.',
    tradeoffs: 'Slower GPU encoding but best quality NVENC can produce.',
  },
};

// =============================================================================
// Rate Control Explanations
// =============================================================================

export const rateControlExplanations: Record<string, FFMPEGExplanation> = {
  crf: {
    title: 'Constant Rate Factor (CRF)',
    description: 'Targets a constant visual quality. File size varies based on content complexity.',
    useCases: ['Offline encoding', 'Archival', 'When file size can vary'],
    tradeoffs: 'CRF 23: Good balance. Lower = better quality but larger. Range 0-51 for x264.',
    example: '-crf 23',
  },
  cbr: {
    title: 'Constant Bitrate (CBR)',
    description: 'Maintains a fixed bitrate throughout. Predictable file size.',
    useCases: ['Streaming', 'Broadcast', 'When consistent bandwidth is needed'],
    tradeoffs: 'Wastes bits on simple scenes, starves complex scenes.',
    example: '-b:v 5M',
  },
  vbr: {
    title: 'Variable Bitrate (VBR)',
    description: 'Allocates more bits to complex scenes and fewer to simple ones.',
    useCases: ['High-quality streaming', 'Balanced quality delivery'],
    tradeoffs: 'Best quality distribution. File size less predictable than CBR.',
    example: '-b:v 5M -maxrate 8M -bufsize 10M',
  },
  cq: {
    title: 'Constant Quality (CQ)',
    description: 'NVENC equivalent of CRF. GPU-based constant quality encoding.',
    useCases: ['NVENC offline encoding', 'GPU-accelerated quality targeting'],
    tradeoffs: 'Use CQ for NVENC, CRF for software. CQ 23 ≈ CRF 23 roughly.',
    example: '-cq 23 -rc vbr',
  },
  qp: {
    title: 'Constant QP',
    description: 'Fixed quantization parameter. Every frame gets the same compression level.',
    useCases: ['Lossless encoding (QP 0)', 'Scientific/medical video'],
    tradeoffs: 'No psycho-visual optimization. Simpler than CRF but less efficient.',
    example: '-qp 20',
  },
  global_quality: {
    title: 'Global Quality (QSV)',
    description: 'Intel QSV quality-based encoding. Similar concept to CRF.',
    useCases: ['QSV hardware encoding with quality target'],
    tradeoffs: 'Use for QSV. Lower values = higher quality. Typically 20-30.',
    example: '-global_quality 23',
  },
};

// =============================================================================
// Container Format Explanations
// =============================================================================

export const containerExplanations: Record<string, FFMPEGExplanation> = {
  mp4: {
    title: 'MP4',
    description: 'The universal container format. Works everywhere.',
    useCases: ['Web delivery', 'General sharing', 'Mobile playback'],
    tradeoffs: 'Maximum compatibility. Use with H.264/H.265 + AAC.',
  },
  mkv: {
    title: 'MKV (Matroska)',
    description: 'Flexible container supporting virtually any codec and multiple streams.',
    useCases: ['Archival', 'Multi-track audio/subtitle', 'Editing workflows'],
    tradeoffs: 'Less browser support than MP4 but more flexible.',
  },
  webm: {
    title: 'WebM',
    description: 'Web-optimized container for VP9/AV1 + Opus/Vorbis.',
    useCases: ['HTML5 video', 'Web embedding'],
    tradeoffs: 'Only supports VP8/VP9/AV1 video and Vorbis/Opus audio.',
  },
  ts: {
    title: 'MPEG-TS',
    description: 'Transport stream format designed for broadcasting and streaming.',
    useCases: ['Live streaming', 'IPTV', 'HLS segments'],
    tradeoffs: 'Robust against stream interruptions. Larger overhead per packet.',
  },
  flv: {
    title: 'FLV',
    description: 'Flash Video format, still used for RTMP streaming.',
    useCases: ['RTMP streaming to Twitch/YouTube'],
    tradeoffs: 'Legacy format but required for RTMP ingest.',
  },
  hls: {
    title: 'HLS',
    description: 'HTTP Live Streaming. Outputs segmented playlist + chunks.',
    useCases: ['Adaptive streaming', 'CDN delivery', 'Live streaming'],
    tradeoffs: 'Creates multiple files. Best for professional streaming delivery.',
  },
  dash: {
    title: 'DASH',
    description: 'Dynamic Adaptive Streaming over HTTP. Open standard alternative to HLS.',
    useCases: ['Adaptive streaming', 'Cross-platform delivery'],
    tradeoffs: 'Open standard, less iOS support than HLS.',
  },
};

// =============================================================================
// Hardware Acceleration Explanations
// =============================================================================

export const hwAccelExplanations: Record<string, FFMPEGExplanation> = {
  none: {
    title: 'No Hardware Acceleration',
    description: 'All processing on CPU. Maximum compatibility and quality control.',
    tradeoffs: 'Slower but works everywhere and provides best quality per bit.',
  },
  cuda: {
    title: 'NVIDIA CUDA',
    description: 'Uses NVIDIA GPU for decoding. Keeps video frames in GPU memory for fast pipeline.',
    useCases: ['NVENC encoding pipeline', 'GPU-accelerated filtering'],
    tradeoffs: 'Much faster decode+encode pipeline. Requires NVIDIA GPU with CUDA.',
    example: '-hwaccel cuda -hwaccel_output_format cuda',
  },
  qsv: {
    title: 'Intel Quick Sync Video',
    description: 'Uses Intel integrated GPU for hardware decode/encode.',
    useCases: ['Server transcoding', 'Power-efficient encoding'],
    tradeoffs: 'Available on most Intel CPUs. Good speed with low power draw.',
    example: '-hwaccel qsv',
  },
  vaapi: {
    title: 'VAAPI',
    description: 'Video Acceleration API for Linux. Works with Intel, AMD, and some NVIDIA GPUs.',
    useCases: ['Linux server transcoding', 'AMD GPU acceleration'],
    tradeoffs: 'Requires Linux with proper GPU drivers installed.',
    example: '-vaapi_device /dev/dri/renderD128',
  },
};

// =============================================================================
// Video Filter Explanations
// =============================================================================

export const videoFilterExplanations: Record<string, FFMPEGExplanation> = {
  scale: {
    title: 'Scale',
    description: 'Resize the video to a different resolution.',
    useCases: ['Downscaling 4K to 1080p', 'Creating thumbnail versions'],
    example: 'scale=1920:1080',
  },
  crop: {
    title: 'Crop',
    description: 'Remove pixels from the edges of the video.',
    useCases: ['Removing letterbox bars', 'Framing adjustment'],
    example: 'crop=1920:800:0:140',
  },
  fps: {
    title: 'FPS',
    description: 'Change the video frame rate.',
    useCases: ['Converting 60fps to 30fps', 'Matching output frame rate'],
    example: 'fps=30',
  },
  deinterlace: {
    title: 'Deinterlace',
    description: 'Convert interlaced video to progressive scan.',
    useCases: ['Processing broadcast/DVD content', 'Removing combing artifacts'],
    example: 'yadif=0:-1:0',
  },
  denoise: {
    title: 'Denoise',
    description: 'Reduce visual noise/grain in the video.',
    useCases: ['Cleaning up low-light footage', 'Improving compression efficiency'],
    example: 'nlmeans=6:7:5:3:3',
  },
  drawtext: {
    title: 'Draw Text',
    description: 'Overlay text on the video (watermarks, timestamps, etc.).',
    useCases: ['Watermarking', 'Timecode overlay', 'Branding'],
    example: "drawtext=text='Watermark':fontsize=24:x=10:y=10",
  },
  rotate: {
    title: 'Rotate',
    description: 'Rotate the video by a specified angle.',
    useCases: ['Fixing phone video orientation'],
    example: 'rotate=PI/2',
  },
  hwupload: {
    title: 'HW Upload',
    description: 'Upload video frames from system memory to GPU memory.',
    useCases: ['Required before VAAPI encoding when input is software-decoded'],
    tradeoffs: 'Needed for VAAPI pipeline: format=nv12,hwupload',
    example: 'format=nv12,hwupload',
  },
};

// =============================================================================
// Audio Filter Explanations
// =============================================================================

export const audioFilterExplanations: Record<string, FFMPEGExplanation> = {
  volume: {
    title: 'Volume',
    description: 'Adjust the audio volume level.',
    useCases: ['Normalizing quiet audio', 'Reducing loud audio'],
    example: 'volume=1.5',
  },
  loudnorm: {
    title: 'Loudness Normalization',
    description: 'Normalize audio to broadcast standards (EBU R128).',
    useCases: ['Podcast production', 'Broadcast compliance', 'Consistent playback volume'],
    tradeoffs: 'Ensures consistent volume across different content.',
    example: 'loudnorm=I=-16:LRA=11:TP=-1.5',
  },
  aresample: {
    title: 'Audio Resample',
    description: 'Change the audio sample rate.',
    useCases: ['Converting 48kHz to 44.1kHz', 'Matching output format requirements'],
    example: 'aresample=44100',
  },
  atempo: {
    title: 'Audio Tempo',
    description: 'Change audio playback speed without affecting pitch.',
    useCases: ['Speed up/slow down audio', 'Time-stretch'],
    tradeoffs: 'Range: 0.5 to 100.0. Chain multiple for extreme values.',
    example: 'atempo=1.5',
  },
  equalizer: {
    title: 'Equalizer',
    description: 'Apply parametric EQ to boost or cut frequency bands.',
    useCases: ['Tone shaping', 'Reducing bass rumble', 'Boosting clarity'],
    example: 'equalizer=f=1000:t=h:w=200:g=-10',
  },
  highpass: {
    title: 'High-Pass Filter',
    description: 'Remove frequencies below a cutoff point.',
    useCases: ['Removing low rumble', 'Cleaning up voice recordings'],
    example: 'highpass=f=200',
  },
  lowpass: {
    title: 'Low-Pass Filter',
    description: 'Remove frequencies above a cutoff point.',
    useCases: ['Removing high-frequency noise', 'Simulating muffled audio'],
    example: 'lowpass=f=3000',
  },
};

// =============================================================================
// Global Option Explanations
// =============================================================================

export const globalOptionExplanations: Record<string, FFMPEGExplanation> = {
  '-y': {
    title: 'Overwrite Output',
    description: 'Automatically overwrite the output file without asking.',
    example: '-y',
  },
  '-n': {
    title: 'No Overwrite',
    description: 'Exit immediately if the output file already exists.',
    example: '-n',
  },
  '-ss': {
    title: 'Seek To',
    description: 'Start processing from this timestamp.',
    useCases: ['Trimming video', 'Skipping intro'],
    example: '-ss 00:01:30',
  },
  '-t': {
    title: 'Duration',
    description: 'Limit processing to this duration.',
    useCases: ['Extracting a clip', 'Limiting output length'],
    example: '-t 00:05:00',
  },
  '-threads': {
    title: 'Thread Count',
    description: 'Number of CPU threads to use for encoding.',
    tradeoffs: '0 = auto-detect. More threads = faster but more CPU load.',
    example: '-threads 4',
  },
  movflags: {
    title: 'MOV Flags',
    description: 'MP4/MOV container options like faststart for web streaming.',
    useCases: ['Web delivery (faststart)', 'Fragmented MP4 (frag_keyframe)'],
    tradeoffs: 'faststart moves metadata to front for progressive download.',
    example: '-movflags +faststart',
  },
};

// =============================================================================
// Master lookup function
// =============================================================================

/** Get an explanation by category and key */
export function getExplanation(
  category: ExplanationCategory,
  key: string
): FFMPEGExplanation | undefined {
  const maps: Record<ExplanationCategory, Record<string, FFMPEGExplanation>> = {
    codec: { ...videoCodecExplanations, ...audioCodecExplanations },
    preset: presetExplanations,
    rateControl: rateControlExplanations,
    filter: { ...videoFilterExplanations, ...audioFilterExplanations },
    container: containerExplanations,
    hwaccel: hwAccelExplanations,
    global: globalOptionExplanations,
    input: globalOptionExplanations,
    output: containerExplanations,
  };

  return maps[category]?.[key];
}

/** Get all explanations for a category */
export function getExplanationsByCategory(
  category: ExplanationCategory
): Record<string, FFMPEGExplanation> {
  const maps: Record<ExplanationCategory, Record<string, FFMPEGExplanation>> = {
    codec: { ...videoCodecExplanations, ...audioCodecExplanations },
    preset: presetExplanations,
    rateControl: rateControlExplanations,
    filter: { ...videoFilterExplanations, ...audioFilterExplanations },
    container: containerExplanations,
    hwaccel: hwAccelExplanations,
    global: globalOptionExplanations,
    input: globalOptionExplanations,
    output: containerExplanations,
  };

  return maps[category] ?? {};
}
