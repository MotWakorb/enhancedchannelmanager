/**
 * MSW request handlers for mocking API endpoints.
 *
 * These handlers intercept HTTP requests and return mock responses,
 * enabling realistic API testing without a backend.
 */
import { http, HttpResponse } from 'msw'

// API base URL
const API_BASE = '/api'

// =============================================================================
// Mock Data Factories
// =============================================================================

let idCounter = 1000

export function resetIdCounter(): void {
  idCounter = 1000
}

function nextId(): number {
  return ++idCounter
}

export function createMockChannel(overrides: Partial<MockChannel> = {}): MockChannel {
  const id = overrides.id ?? nextId()
  return {
    id,
    uuid: `channel-uuid-${id}`,
    name: `Test Channel ${id}`,
    channel_number: id,
    channel_group_id: 1,
    tvg_id: null,
    tvc_guide_stationid: null,
    epg_data_id: null,
    streams: [id],
    stream_profile_id: null,
    logo_id: null,
    auto_created: false,
    auto_created_by: null,
    auto_created_by_name: null,
    ...overrides,
  }
}

export function createMockChannelGroup(overrides: Partial<MockChannelGroup> = {}): MockChannelGroup {
  const id = overrides.id ?? nextId()
  return {
    id,
    name: `Test Group ${id}`,
    channel_count: 0,
    ...overrides,
  }
}

export function createMockStream(overrides: Partial<MockStream> = {}): MockStream {
  const id = overrides.id ?? nextId()
  return {
    id,
    name: `Test Stream ${id}`,
    url: `http://test.stream/${id}.m3u8`,
    m3u_account: 1,
    channel_group_name: 'Test Group',
    logo_url: null,
    ...overrides,
  }
}

export function createMockScheduledTask(overrides: Partial<MockScheduledTask> = {}): MockScheduledTask {
  const id = overrides.id ?? nextId()
  return {
    id,
    task_id: overrides.task_id ?? `task_${id}`,
    task_name: `Test Task ${id}`,
    description: 'A test task',
    enabled: true,
    schedule_type: 'manual',
    interval_seconds: null,
    cron_expression: null,
    schedule_time: null,
    timezone: 'America/New_York',
    config: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    last_run_at: null,
    next_run_at: null,
    ...overrides,
  }
}

export function createMockAlertMethod(overrides: Partial<MockAlertMethod> = {}): MockAlertMethod {
  const id = overrides.id ?? nextId()
  return {
    id,
    name: `Test Alert ${id}`,
    method_type: 'discord',
    enabled: true,
    config: { webhook_url: '********' },
    notify_info: false,
    notify_success: true,
    notify_warning: true,
    notify_error: true,
    alert_sources: null,
    last_sent_at: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...overrides,
  }
}

export function createMockNotification(overrides: Partial<MockNotification> = {}): MockNotification {
  const id = overrides.id ?? nextId()
  return {
    id,
    type: 'info',
    title: `Test Notification ${id}`,
    message: 'This is a test notification',
    read: false,
    source: 'test',
    source_id: `source_${id}`,
    action_label: null,
    action_url: null,
    metadata: null,
    created_at: new Date().toISOString(),
    read_at: null,
    expires_at: null,
    ...overrides,
  }
}

// =============================================================================
// Type Definitions
// =============================================================================

interface MockChannel {
  id: number
  uuid: string
  name: string
  channel_number: number | null
  channel_group_id: number | null
  tvg_id: string | null
  tvc_guide_stationid: string | null
  epg_data_id: number | null
  streams: number[]
  stream_profile_id: number | null
  logo_id: number | null
  auto_created: boolean
  auto_created_by: number | null
  auto_created_by_name: string | null
}

interface MockChannelGroup {
  id: number
  name: string
  channel_count: number
}

interface MockStream {
  id: number
  name: string
  url: string
  m3u_account: number | null
  channel_group_name: string | null
  logo_url: string | null
}

interface MockScheduledTask {
  id: number
  task_id: string
  task_name: string
  description: string | null
  enabled: boolean
  schedule_type: string
  interval_seconds: number | null
  cron_expression: string | null
  schedule_time: string | null
  timezone: string | null
  config: object | null
  created_at: string
  updated_at: string
  last_run_at: string | null
  next_run_at: string | null
}

interface MockAlertMethod {
  id: number
  name: string
  method_type: string
  enabled: boolean
  config: object
  notify_info: boolean
  notify_success: boolean
  notify_warning: boolean
  notify_error: boolean
  alert_sources: object | null
  last_sent_at: string | null
  created_at: string
  updated_at: string
}

interface MockNotification {
  id: number
  type: string
  title: string | null
  message: string
  read: boolean
  source: string | null
  source_id: string | null
  action_label: string | null
  action_url: string | null
  metadata: object | null
  created_at: string
  read_at: string | null
  expires_at: string | null
}

// =============================================================================
// Mock Data Store
// =============================================================================

interface MockDataStore {
  channels: MockChannel[]
  channelGroups: MockChannelGroup[]
  streams: MockStream[]
  tasks: MockScheduledTask[]
  alertMethods: MockAlertMethod[]
  notifications: MockNotification[]
  settings: object
}

export const mockDataStore: MockDataStore = {
  channels: [],
  channelGroups: [],
  streams: [],
  tasks: [],
  alertMethods: [],
  notifications: [],
  settings: {
    configured: true,
    url: 'http://dispatcharr.test',
    theme: 'dark',
    auto_rename_channel_number: false,
    show_stream_urls: true,
    hide_ungrouped_streams: true,
    hide_epg_urls: false,
    hide_m3u_urls: false,
    gracenote_conflict_mode: 'ask',
    epg_auto_match_threshold: 80,
    include_channel_number_in_name: false,
    channel_number_separator: '-',
    remove_country_prefix: false,
    include_country_in_name: false,
    country_separator: '|',
    timezone_preference: 'both',
    default_channel_profile_ids: [],
    custom_network_prefixes: [],
    stream_sort_priority: ['resolution', 'bitrate', 'framerate', 'm3u_priority', 'audio_channels'],
    stream_sort_enabled: { resolution: true, bitrate: true, framerate: true, m3u_priority: false, audio_channels: false },
    m3u_account_priorities: {},
    deprioritize_failed_streams: true,
    hide_auto_sync_groups: false,
    frontend_log_level: 'INFO',
  },
}

/**
 * Reset the mock data store to initial state
 */
export function resetMockDataStore(): void {
  mockDataStore.channels = []
  mockDataStore.channelGroups = []
  mockDataStore.streams = []
  mockDataStore.tasks = []
  mockDataStore.alertMethods = []
  mockDataStore.notifications = []
  resetIdCounter()
}

// =============================================================================
// Request Handlers
// =============================================================================

export const handlers = [
  // -------------------------------------------------------------------------
  // Health & Settings
  // -------------------------------------------------------------------------

  http.get(`${API_BASE}/health`, () => {
    return HttpResponse.json({
      status: 'healthy',
      service: 'Enhanced Channel Manager',
      version: '0.8.2-test',
      release_channel: 'stable',
    })
  }),

  http.get(`${API_BASE}/settings`, () => {
    return HttpResponse.json(mockDataStore.settings)
  }),

  http.put(`${API_BASE}/settings`, async ({ request }) => {
    const updates = await request.json() as object
    Object.assign(mockDataStore.settings, updates)
    return HttpResponse.json(mockDataStore.settings)
  }),

  // -------------------------------------------------------------------------
  // Channels
  // -------------------------------------------------------------------------

  http.get(`${API_BASE}/channels`, ({ request }) => {
    const url = new URL(request.url)
    const page = parseInt(url.searchParams.get('page') ?? '1')
    const pageSize = parseInt(url.searchParams.get('page_size') ?? '100')
    const search = url.searchParams.get('search')

    let results = mockDataStore.channels
    if (search) {
      results = results.filter(c => c.name.toLowerCase().includes(search.toLowerCase()))
    }

    const start = (page - 1) * pageSize
    const paginatedResults = results.slice(start, start + pageSize)
    const totalPages = Math.ceil(results.length / pageSize) || 1

    return HttpResponse.json({
      count: results.length,
      page,
      page_size: pageSize,
      total_pages: totalPages,
      next: start + pageSize < results.length ? `${API_BASE}/channels?page=${page + 1}` : null,
      previous: page > 1 ? `${API_BASE}/channels?page=${page - 1}` : null,
      results: paginatedResults,
    })
  }),

  http.get(`${API_BASE}/channels/:id`, ({ params }) => {
    const channel = mockDataStore.channels.find(c => c.id === Number(params.id))
    if (!channel) {
      return new HttpResponse(null, { status: 404 })
    }
    return HttpResponse.json(channel)
  }),

  http.post(`${API_BASE}/channels`, async ({ request }) => {
    const data = await request.json() as Partial<MockChannel>
    const channel = createMockChannel(data)
    mockDataStore.channels.push(channel)
    return HttpResponse.json(channel, { status: 201 })
  }),

  http.patch(`${API_BASE}/channels/:id`, async ({ params, request }) => {
    const channel = mockDataStore.channels.find(c => c.id === Number(params.id))
    if (!channel) {
      return new HttpResponse(null, { status: 404 })
    }
    const updates = await request.json() as Partial<MockChannel>
    Object.assign(channel, updates)
    return HttpResponse.json(channel)
  }),

  http.delete(`${API_BASE}/channels/:id`, ({ params }) => {
    const index = mockDataStore.channels.findIndex(c => c.id === Number(params.id))
    if (index === -1) {
      return new HttpResponse(null, { status: 404 })
    }
    mockDataStore.channels.splice(index, 1)
    return new HttpResponse(null, { status: 204 })
  }),

  // -------------------------------------------------------------------------
  // Channel Groups
  // -------------------------------------------------------------------------

  http.get(`${API_BASE}/channel-groups`, () => {
    return HttpResponse.json(mockDataStore.channelGroups)
  }),

  http.post(`${API_BASE}/channel-groups`, async ({ request }) => {
    const data = await request.json() as Partial<MockChannelGroup>
    const group = createMockChannelGroup(data)
    mockDataStore.channelGroups.push(group)
    return HttpResponse.json(group, { status: 201 })
  }),

  http.delete(`${API_BASE}/channel-groups/:id`, ({ params }) => {
    const index = mockDataStore.channelGroups.findIndex(g => g.id === Number(params.id))
    if (index === -1) {
      return new HttpResponse(null, { status: 404 })
    }
    mockDataStore.channelGroups.splice(index, 1)
    return new HttpResponse(null, { status: 204 })
  }),

  // -------------------------------------------------------------------------
  // Streams
  // -------------------------------------------------------------------------

  http.get(`${API_BASE}/streams`, ({ request }) => {
    const url = new URL(request.url)
    const page = parseInt(url.searchParams.get('page') ?? '1')
    const pageSize = parseInt(url.searchParams.get('page_size') ?? '100')

    const start = (page - 1) * pageSize
    const paginatedResults = mockDataStore.streams.slice(start, start + pageSize)

    return HttpResponse.json({
      count: mockDataStore.streams.length,
      next: start + pageSize < mockDataStore.streams.length ? `${API_BASE}/streams?page=${page + 1}` : null,
      previous: page > 1 ? `${API_BASE}/streams?page=${page - 1}` : null,
      results: paginatedResults,
    })
  }),

  // -------------------------------------------------------------------------
  // Scheduled Tasks
  // -------------------------------------------------------------------------

  http.get(`${API_BASE}/tasks`, () => {
    return HttpResponse.json(mockDataStore.tasks)
  }),

  http.get(`${API_BASE}/tasks/:taskId`, ({ params }) => {
    const task = mockDataStore.tasks.find(t => t.task_id === params.taskId)
    if (!task) {
      return new HttpResponse(null, { status: 404 })
    }
    return HttpResponse.json(task)
  }),

  http.patch(`${API_BASE}/tasks/:taskId`, async ({ params, request }) => {
    const task = mockDataStore.tasks.find(t => t.task_id === params.taskId)
    if (!task) {
      return new HttpResponse(null, { status: 404 })
    }
    const updates = await request.json() as Partial<MockScheduledTask>
    Object.assign(task, updates, { updated_at: new Date().toISOString() })
    return HttpResponse.json(task)
  }),

  http.post(`${API_BASE}/tasks/:taskId/run`, ({ params }) => {
    const task = mockDataStore.tasks.find(t => t.task_id === params.taskId)
    if (!task) {
      return new HttpResponse(null, { status: 404 })
    }
    return HttpResponse.json({ success: true, message: 'Task started' })
  }),

  // -------------------------------------------------------------------------
  // Alert Methods
  // -------------------------------------------------------------------------

  http.get(`${API_BASE}/alert-methods`, () => {
    return HttpResponse.json(mockDataStore.alertMethods)
  }),

  http.get(`${API_BASE}/alert-methods/:id`, ({ params }) => {
    const method = mockDataStore.alertMethods.find(m => m.id === Number(params.id))
    if (!method) {
      return new HttpResponse(null, { status: 404 })
    }
    return HttpResponse.json(method)
  }),

  http.post(`${API_BASE}/alert-methods`, async ({ request }) => {
    const data = await request.json() as Partial<MockAlertMethod>
    const method = createMockAlertMethod(data)
    mockDataStore.alertMethods.push(method)
    return HttpResponse.json(method, { status: 201 })
  }),

  http.patch(`${API_BASE}/alert-methods/:id`, async ({ params, request }) => {
    const method = mockDataStore.alertMethods.find(m => m.id === Number(params.id))
    if (!method) {
      return new HttpResponse(null, { status: 404 })
    }
    const updates = await request.json() as Partial<MockAlertMethod>
    Object.assign(method, updates, { updated_at: new Date().toISOString() })
    return HttpResponse.json(method)
  }),

  http.delete(`${API_BASE}/alert-methods/:id`, ({ params }) => {
    const index = mockDataStore.alertMethods.findIndex(m => m.id === Number(params.id))
    if (index === -1) {
      return new HttpResponse(null, { status: 404 })
    }
    mockDataStore.alertMethods.splice(index, 1)
    return new HttpResponse(null, { status: 204 })
  }),

  http.post(`${API_BASE}/alert-methods/:id/test`, ({ params }) => {
    const method = mockDataStore.alertMethods.find(m => m.id === Number(params.id))
    if (!method) {
      return new HttpResponse(null, { status: 404 })
    }
    return HttpResponse.json({ success: true, message: 'Test notification sent' })
  }),

  // -------------------------------------------------------------------------
  // Notifications
  // -------------------------------------------------------------------------

  http.get(`${API_BASE}/notifications`, () => {
    return HttpResponse.json(mockDataStore.notifications)
  }),

  http.get(`${API_BASE}/notifications/unread-count`, () => {
    const unreadCount = mockDataStore.notifications.filter(n => !n.read).length
    return HttpResponse.json({ count: unreadCount })
  }),

  http.patch(`${API_BASE}/notifications/:id/read`, ({ params }) => {
    const notification = mockDataStore.notifications.find(n => n.id === Number(params.id))
    if (!notification) {
      return new HttpResponse(null, { status: 404 })
    }
    notification.read = true
    notification.read_at = new Date().toISOString()
    return HttpResponse.json(notification)
  }),

  http.post(`${API_BASE}/notifications/mark-all-read`, () => {
    const now = new Date().toISOString()
    mockDataStore.notifications.forEach(n => {
      n.read = true
      n.read_at = now
    })
    return HttpResponse.json({ success: true })
  }),
]
