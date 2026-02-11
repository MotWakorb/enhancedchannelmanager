/**
 * MSW request handlers for FFMPEG Builder API endpoints.
 *
 * These handlers mock the backend FFMPEG API for unit testing.
 */
import { http, HttpResponse } from 'msw'

const API_BASE = '/api'

// =============================================================================
// Mock Data Types
// =============================================================================

interface MockFFMPEGConfig {
  id: number
  name: string
  description: string | null
  config: object
  created_at: string
  updated_at: string
}

interface MockFFMPEGJob {
  id: string
  name: string
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
  command: string
  progress: object | null
  started_at: string | null
  completed_at: string | null
  error: string | null
  output_path: string | null
  created_at: string
}

// =============================================================================
// Mock Data Store
// =============================================================================

export interface FFMPEGMockDataStore {
  configs: MockFFMPEGConfig[]
  jobs: MockFFMPEGJob[]
  capabilities: object
  queueConfig: object
}

let idCounter = 5000

function nextId(): number {
  return ++idCounter
}

export function resetFFMPEGIdCounter(): void {
  idCounter = 5000
}

export const ffmpegMockDataStore: FFMPEGMockDataStore = {
  configs: [],
  jobs: [],
  capabilities: {
    version: '6.1',
    encoders: [
      'libx264', 'libx265', 'libvpx-vp9', 'libaom-av1', 'libsvtav1',
      'h264_nvenc', 'hevc_nvenc',
      'h264_qsv', 'hevc_qsv',
      'h264_vaapi', 'hevc_vaapi',
      'aac', 'libmp3lame', 'libvorbis', 'libopus', 'ac3', 'eac3', 'flac',
    ],
    decoders: [
      'h264', 'hevc', 'vp9', 'av1',
      'h264_cuvid', 'hevc_cuvid',
      'h264_qsv', 'hevc_qsv',
      'aac', 'mp3', 'vorbis', 'opus', 'ac3', 'eac3', 'flac',
    ],
    formats: [
      'mp4', 'mkv', 'webm', 'ts', 'flv', 'avi', 'mov', 'ogg', 'hls', 'dash',
    ],
    filters: [
      'scale', 'crop', 'pad', 'fps', 'yadif', 'nlmeans', 'unsharp',
      'rotate', 'hflip', 'vflip', 'transpose', 'overlay', 'drawtext',
      'colorbalance', 'format', 'hwupload', 'hwdownload',
      'volume', 'loudnorm', 'aresample', 'atempo', 'equalizer',
      'highpass', 'lowpass', 'aecho', 'adelay', 'amix',
    ],
    hwaccels: [
      {
        api: 'cuda',
        available: true,
        encoders: ['h264_nvenc', 'hevc_nvenc'],
        decoders: ['h264_cuvid', 'hevc_cuvid'],
        devices: ['GPU 0: NVIDIA GeForce RTX 3080'],
        reason: null,
      },
      {
        api: 'qsv',
        available: true,
        encoders: ['h264_qsv', 'hevc_qsv'],
        decoders: ['h264_qsv', 'hevc_qsv'],
        devices: ['Intel iGPU'],
        reason: null,
      },
      {
        api: 'vaapi',
        available: true,
        encoders: ['h264_vaapi', 'hevc_vaapi'],
        decoders: ['h264_vaapi', 'hevc_vaapi'],
        devices: ['/dev/dri/renderD128'],
        reason: null,
      },
    ],
  },
  queueConfig: {
    maxConcurrent: 2,
    maxRetries: 3,
    retryDelay: 30,
    priority: 'fifo',
  },
}

export function resetFFMPEGMockDataStore(): void {
  ffmpegMockDataStore.configs = []
  ffmpegMockDataStore.jobs = []
  resetFFMPEGIdCounter()
}

// =============================================================================
// Factory Functions
// =============================================================================

export function createMockFFMPEGConfig(
  overrides: Partial<MockFFMPEGConfig> = {}
): MockFFMPEGConfig {
  const id = overrides.id ?? nextId()
  return {
    id,
    name: `Test Config ${id}`,
    description: null,
    config: {
      input: { type: 'file', path: '/input/test.mp4' },
      output: { path: '/output/test.mp4', format: 'mp4' },
      videoCodec: { codec: 'libx264', rateControl: 'crf', crf: 23 },
      audioCodec: { codec: 'aac', bitrate: '192k' },
      videoFilters: [],
      audioFilters: [],
      streamMappings: [],
    },
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...overrides,
  }
}

export function createMockFFMPEGJob(
  overrides: Partial<MockFFMPEGJob> = {}
): MockFFMPEGJob {
  const id = overrides.id ?? `job-${nextId()}`
  return {
    id,
    name: `Test Job ${id}`,
    status: 'queued',
    command: 'ffmpeg -i input.mp4 -c:v libx264 -crf 23 output.mp4',
    progress: null,
    started_at: null,
    completed_at: null,
    error: null,
    output_path: null,
    created_at: new Date().toISOString(),
    ...overrides,
  }
}

// =============================================================================
// Request Handlers
// =============================================================================

export const ffmpegHandlers = [
  // -------------------------------------------------------------------------
  // Capabilities
  // -------------------------------------------------------------------------

  http.get(`${API_BASE}/ffmpeg/capabilities`, () => {
    return HttpResponse.json(ffmpegMockDataStore.capabilities)
  }),

  // -------------------------------------------------------------------------
  // Validate & Generate Command
  // -------------------------------------------------------------------------

  http.post(`${API_BASE}/ffmpeg/validate`, async ({ request }) => {
    const data = await request.json() as { config: object }
    // Basic validation mock
    const errors: string[] = []
    const warnings: string[] = []

    if (!data.config) {
      errors.push('Configuration is required')
    }

    return HttpResponse.json({
      valid: errors.length === 0,
      errors,
      warnings,
      command: 'ffmpeg -i input.mp4 -c:v libx264 -crf 23 -c:a aac -b:a 192k output.mp4',
    })
  }),

  http.post(`${API_BASE}/ffmpeg/generate-command`, async ({ request }) => {
    const data = await request.json() as { config: object }
    return HttpResponse.json({
      command: 'ffmpeg -i input.mp4 -c:v libx264 -crf 23 -c:a aac -b:a 192k output.mp4',
      annotations: [
        { flag: '-i input.mp4', explanation: 'Input file', category: 'input' },
        { flag: '-c:v libx264', explanation: 'Video codec: H.264 software encoder', category: 'video' },
        { flag: '-crf 23', explanation: 'Constant Rate Factor: balanced quality', category: 'video' },
        { flag: '-c:a aac', explanation: 'Audio codec: AAC', category: 'audio' },
        { flag: '-b:a 192k', explanation: 'Audio bitrate: 192 kbps', category: 'audio' },
        { flag: 'output.mp4', explanation: 'Output file in MP4 container', category: 'output' },
      ],
    })
  }),

  // -------------------------------------------------------------------------
  // Saved Configurations (CRUD)
  // -------------------------------------------------------------------------

  http.get(`${API_BASE}/ffmpeg/configs`, () => {
    return HttpResponse.json({
      configs: ffmpegMockDataStore.configs,
      total: ffmpegMockDataStore.configs.length,
    })
  }),

  http.get(`${API_BASE}/ffmpeg/configs/:id`, ({ params }) => {
    const config = ffmpegMockDataStore.configs.find(c => c.id === Number(params.id))
    if (!config) {
      return HttpResponse.json({ detail: 'Config not found' }, { status: 404 })
    }
    return HttpResponse.json(config)
  }),

  http.post(`${API_BASE}/ffmpeg/configs`, async ({ request }) => {
    const data = await request.json() as Partial<MockFFMPEGConfig>
    const config = createMockFFMPEGConfig(data)
    ffmpegMockDataStore.configs.push(config)
    return HttpResponse.json(config, { status: 201 })
  }),

  http.put(`${API_BASE}/ffmpeg/configs/:id`, async ({ params, request }) => {
    const index = ffmpegMockDataStore.configs.findIndex(c => c.id === Number(params.id))
    if (index === -1) {
      return HttpResponse.json({ detail: 'Config not found' }, { status: 404 })
    }
    const updates = await request.json() as Partial<MockFFMPEGConfig>
    ffmpegMockDataStore.configs[index] = {
      ...ffmpegMockDataStore.configs[index],
      ...updates,
      updated_at: new Date().toISOString(),
    }
    return HttpResponse.json(ffmpegMockDataStore.configs[index])
  }),

  http.delete(`${API_BASE}/ffmpeg/configs/:id`, ({ params }) => {
    const index = ffmpegMockDataStore.configs.findIndex(c => c.id === Number(params.id))
    if (index === -1) {
      return HttpResponse.json({ detail: 'Config not found' }, { status: 404 })
    }
    ffmpegMockDataStore.configs.splice(index, 1)
    return new HttpResponse(null, { status: 204 })
  }),

  // -------------------------------------------------------------------------
  // Presets
  // -------------------------------------------------------------------------

  http.get(`${API_BASE}/ffmpeg/presets`, () => {
    return HttpResponse.json({
      presets: [
        {
          id: 'web-mp4',
          name: 'Web MP4',
          description: 'Optimized for browser playback with fast start',
          category: 'web',
          isBuiltIn: true,
          config: {
            input: { type: 'file', path: '' },
            output: { path: '', format: 'mp4', movflags: ['faststart'] },
            videoCodec: { codec: 'libx264', preset: 'medium', rateControl: 'crf', crf: 23, pixelFormat: 'yuv420p' },
            audioCodec: { codec: 'aac', bitrate: '128k', sampleRate: 48000 },
            videoFilters: [],
            audioFilters: [],
            streamMappings: [],
          },
        },
        {
          id: 'hls-streaming',
          name: 'HLS Streaming',
          description: 'Adaptive HTTP Live Streaming output',
          category: 'streaming',
          isBuiltIn: true,
          config: {
            input: { type: 'file', path: '' },
            output: { path: '', format: 'hls' },
            videoCodec: { codec: 'libx264', preset: 'veryfast', rateControl: 'cbr', bitrate: '4M' },
            audioCodec: { codec: 'aac', bitrate: '128k', sampleRate: 48000 },
            videoFilters: [],
            audioFilters: [],
            streamMappings: [],
          },
        },
        {
          id: 'archive-hevc',
          name: 'Archive (HEVC)',
          description: 'High-quality archival with HEVC for smaller files',
          category: 'archive',
          isBuiltIn: true,
          config: {
            input: { type: 'file', path: '' },
            output: { path: '', format: 'mkv' },
            videoCodec: { codec: 'libx265', preset: 'slow', rateControl: 'crf', crf: 20 },
            audioCodec: { codec: 'flac' },
            videoFilters: [],
            audioFilters: [],
            streamMappings: [],
          },
        },
        {
          id: 'nvenc-fast',
          name: 'NVENC Fast Transcode',
          description: 'GPU-accelerated fast encoding with NVIDIA',
          category: 'streaming',
          isBuiltIn: true,
          config: {
            input: { type: 'file', path: '', hwaccel: { api: 'cuda', outputFormat: 'cuda' } },
            output: { path: '', format: 'mp4', movflags: ['faststart'] },
            videoCodec: { codec: 'h264_nvenc', preset: 'p4', rateControl: 'vbr', cq: 23 },
            audioCodec: { codec: 'aac', bitrate: '192k' },
            videoFilters: [],
            audioFilters: [],
            streamMappings: [],
          },
        },
      ],
    })
  }),

  // -------------------------------------------------------------------------
  // Jobs (Execution & Queue)
  // -------------------------------------------------------------------------

  http.get(`${API_BASE}/ffmpeg/jobs`, ({ request }) => {
    const url = new URL(request.url)
    const status = url.searchParams.get('status')
    const limit = parseInt(url.searchParams.get('limit') ?? '20')
    const offset = parseInt(url.searchParams.get('offset') ?? '0')

    let filtered = ffmpegMockDataStore.jobs
    if (status) {
      filtered = filtered.filter(j => j.status === status)
    }

    return HttpResponse.json({
      jobs: filtered.slice(offset, offset + limit),
      total: filtered.length,
      queue: {
        running: filtered.filter(j => j.status === 'running').length,
        queued: filtered.filter(j => j.status === 'queued').length,
        completed: filtered.filter(j => j.status === 'completed').length,
        failed: filtered.filter(j => j.status === 'failed').length,
        maxConcurrent: 2,
      },
    })
  }),

  http.get(`${API_BASE}/ffmpeg/jobs/:id`, ({ params }) => {
    const job = ffmpegMockDataStore.jobs.find(j => j.id === params.id)
    if (!job) {
      return HttpResponse.json({ detail: 'Job not found' }, { status: 404 })
    }
    return HttpResponse.json(job)
  }),

  http.post(`${API_BASE}/ffmpeg/jobs`, async ({ request }) => {
    const data = await request.json() as { name?: string; config: object; command?: string }
    const job = createMockFFMPEGJob({
      name: data.name || 'New Job',
      status: 'queued',
      command: data.command || 'ffmpeg -i input.mp4 output.mp4',
    })
    ffmpegMockDataStore.jobs.push(job)
    return HttpResponse.json(job, { status: 201 })
  }),

  http.post(`${API_BASE}/ffmpeg/jobs/:id/cancel`, ({ params }) => {
    const job = ffmpegMockDataStore.jobs.find(j => j.id === params.id)
    if (!job) {
      return HttpResponse.json({ detail: 'Job not found' }, { status: 404 })
    }
    if (job.status !== 'running' && job.status !== 'queued') {
      return HttpResponse.json({ detail: 'Job cannot be cancelled' }, { status: 400 })
    }
    job.status = 'cancelled'
    return HttpResponse.json(job)
  }),

  http.delete(`${API_BASE}/ffmpeg/jobs/:id`, ({ params }) => {
    const index = ffmpegMockDataStore.jobs.findIndex(j => j.id === params.id)
    if (index === -1) {
      return HttpResponse.json({ detail: 'Job not found' }, { status: 404 })
    }
    ffmpegMockDataStore.jobs.splice(index, 1)
    return new HttpResponse(null, { status: 204 })
  }),

  // -------------------------------------------------------------------------
  // Job Queue Config
  // -------------------------------------------------------------------------

  http.get(`${API_BASE}/ffmpeg/queue/config`, () => {
    return HttpResponse.json(ffmpegMockDataStore.queueConfig)
  }),

  http.put(`${API_BASE}/ffmpeg/queue/config`, async ({ request }) => {
    const updates = await request.json() as object
    Object.assign(ffmpegMockDataStore.queueConfig, updates)
    return HttpResponse.json(ffmpegMockDataStore.queueConfig)
  }),

  // -------------------------------------------------------------------------
  // ECM Integration
  // -------------------------------------------------------------------------

  http.get(`${API_BASE}/ffmpeg/profiles`, () => {
    return HttpResponse.json({ profiles: [] })
  }),

  http.post(`${API_BASE}/ffmpeg/profiles`, async ({ request }) => {
    const data = await request.json() as object
    return HttpResponse.json({ id: nextId(), ...data, enabled: true }, { status: 201 })
  }),
]
