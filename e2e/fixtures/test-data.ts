/**
 * Shared test data for E2E tests.
 *
 * Provides mock data factories and constants for consistent testing.
 */

// =============================================================================
// Test Credentials
// =============================================================================

/**
 * Default test user credentials.
 * These should match a user created during test setup or seeded in the database.
 *
 * To create the test user in a running container:
 * docker exec <container> python -c "
 * from auth.password import hash_password
 * from database import get_session, init_db
 * from models import User
 * init_db()
 * session = get_session()
 * hashed = hash_password('e2e_test_password')
 * new_user = User(username='e2e_test', email='e2e@test.local', password_hash=hashed, auth_provider='local', is_active=True)
 * session.add(new_user)
 * session.commit()
 * print(f'Created user with id {new_user.id}')
 * session.close()
 * "
 */
export const testCredentials = {
  username: process.env.E2E_TEST_USERNAME || 'e2e_test',
  password: process.env.E2E_TEST_PASSWORD || 'e2e_test_password',
}

// =============================================================================
// Settings Data
// =============================================================================

export const mockSettings = {
  configured: true,
  url: 'http://dispatcharr.test:5656',
  username: 'admin',
  theme: 'dark',
  auto_rename_channel_number: false,
  show_stream_urls: true,
  hide_ungrouped_streams: true,
  hide_epg_urls: false,
  hide_m3u_urls: false,
  gracenote_conflict_mode: 'ask' as const,
  epg_auto_match_threshold: 80,
  include_channel_number_in_name: false,
  channel_number_separator: '-',
  remove_country_prefix: false,
  include_country_in_name: false,
  country_separator: '|',
  timezone_preference: 'both',
  default_channel_profile_ids: [],
  custom_network_prefixes: [],
  stream_sort_priority: ['resolution', 'bitrate', 'framerate'],
  stream_sort_enabled: { resolution: true, bitrate: true, framerate: true },
  deprioritize_failed_streams: true,
  hide_auto_sync_groups: false,
  frontend_log_level: 'INFO',
}

// =============================================================================
// Channel Data
// =============================================================================

export interface MockChannel {
  id: number
  uuid: string
  name: string
  channel_number: number | null
  channel_group_id: number | null
  streams: number[]
}

export function createMockChannel(overrides: Partial<MockChannel> = {}): MockChannel {
  const id = overrides.id ?? Math.floor(Math.random() * 10000)
  return {
    id,
    uuid: `channel-uuid-${id}`,
    name: overrides.name ?? `Test Channel ${id}`,
    channel_number: overrides.channel_number ?? id,
    channel_group_id: overrides.channel_group_id ?? 1,
    streams: overrides.streams ?? [id],
    ...overrides,
  }
}

export const sampleChannels: MockChannel[] = [
  createMockChannel({ id: 1, name: 'ESPN', channel_number: 100, channel_group_id: 1 }),
  createMockChannel({ id: 2, name: 'CNN', channel_number: 200, channel_group_id: 2 }),
  createMockChannel({ id: 3, name: 'HBO', channel_number: 300, channel_group_id: 3 }),
  createMockChannel({ id: 4, name: 'Discovery', channel_number: 400, channel_group_id: 4 }),
  createMockChannel({ id: 5, name: 'NBC', channel_number: 500, channel_group_id: 1 }),
]

// =============================================================================
// Channel Group Data
// =============================================================================

export interface MockChannelGroup {
  id: number
  name: string
  channel_count: number
}

export function createMockChannelGroup(overrides: Partial<MockChannelGroup> = {}): MockChannelGroup {
  const id = overrides.id ?? Math.floor(Math.random() * 10000)
  return {
    id,
    name: overrides.name ?? `Group ${id}`,
    channel_count: overrides.channel_count ?? 0,
    ...overrides,
  }
}

export const sampleChannelGroups: MockChannelGroup[] = [
  createMockChannelGroup({ id: 1, name: 'Sports', channel_count: 2 }),
  createMockChannelGroup({ id: 2, name: 'News', channel_count: 1 }),
  createMockChannelGroup({ id: 3, name: 'Entertainment', channel_count: 1 }),
  createMockChannelGroup({ id: 4, name: 'Documentary', channel_count: 1 }),
]

// =============================================================================
// Task Data
// =============================================================================

export interface MockScheduledTask {
  id: number
  task_id: string
  task_name: string
  description: string | null
  enabled: boolean
  schedule_type: string
  last_run_at: string | null
  next_run_at: string | null
}

export function createMockTask(overrides: Partial<MockScheduledTask> = {}): MockScheduledTask {
  const id = overrides.id ?? Math.floor(Math.random() * 10000)
  return {
    id,
    task_id: overrides.task_id ?? `task_${id}`,
    task_name: overrides.task_name ?? `Test Task ${id}`,
    description: overrides.description ?? 'A test task',
    enabled: overrides.enabled ?? true,
    schedule_type: overrides.schedule_type ?? 'manual',
    last_run_at: overrides.last_run_at ?? null,
    next_run_at: overrides.next_run_at ?? null,
    ...overrides,
  }
}

export const sampleTasks: MockScheduledTask[] = [
  createMockTask({
    id: 1,
    task_id: 'stream_probe',
    task_name: 'Stream Probe',
    description: 'Probe all streams for metadata',
    enabled: true,
    schedule_type: 'interval',
  }),
  createMockTask({
    id: 2,
    task_id: 'epg_refresh',
    task_name: 'EPG Refresh',
    description: 'Refresh EPG data from sources',
    enabled: true,
    schedule_type: 'daily',
  }),
  createMockTask({
    id: 3,
    task_id: 'm3u_refresh',
    task_name: 'M3U Refresh',
    description: 'Refresh M3U playlists',
    enabled: false,
    schedule_type: 'manual',
  }),
]

// =============================================================================
// Alert Method Data
// =============================================================================

export interface MockAlertMethod {
  id: number
  name: string
  method_type: 'discord' | 'telegram' | 'smtp'
  enabled: boolean
  config: Record<string, unknown>
  notify_success: boolean
  notify_warning: boolean
  notify_error: boolean
}

export function createMockAlertMethod(overrides: Partial<MockAlertMethod> = {}): MockAlertMethod {
  const id = overrides.id ?? Math.floor(Math.random() * 10000)
  return {
    id,
    name: overrides.name ?? `Alert ${id}`,
    method_type: overrides.method_type ?? 'discord',
    enabled: overrides.enabled ?? true,
    config: overrides.config ?? { webhook_url: '********' },
    notify_success: overrides.notify_success ?? true,
    notify_warning: overrides.notify_warning ?? true,
    notify_error: overrides.notify_error ?? true,
    ...overrides,
  }
}

export const sampleAlertMethods: MockAlertMethod[] = [
  createMockAlertMethod({
    id: 1,
    name: 'Discord Alerts',
    method_type: 'discord',
    enabled: true,
  }),
  createMockAlertMethod({
    id: 2,
    name: 'Telegram Alerts',
    method_type: 'telegram',
    enabled: false,
  }),
]

// =============================================================================
// Notification Data
// =============================================================================

export interface MockNotification {
  id: number
  type: 'info' | 'success' | 'warning' | 'error'
  title: string | null
  message: string
  read: boolean
  created_at: string
}

export function createMockNotification(overrides: Partial<MockNotification> = {}): MockNotification {
  const id = overrides.id ?? Math.floor(Math.random() * 10000)
  return {
    id,
    type: overrides.type ?? 'info',
    title: overrides.title ?? `Notification ${id}`,
    message: overrides.message ?? 'This is a test notification',
    read: overrides.read ?? false,
    created_at: overrides.created_at ?? new Date().toISOString(),
    ...overrides,
  }
}

// =============================================================================
// Test Selectors (CSS selectors for common UI elements)
// =============================================================================

export const selectors = {
  // Authentication
  loginPage: '.login-page, .login-container, form:has(input[name="username"]):has(input[name="password"])',
  loginUsername: 'input[name="username"]',
  loginPassword: 'input[name="password"]',
  loginSubmit: 'button[type="submit"], button:has-text("Sign In"), button:has-text("Login")',
  loginError: '.login-error, .error-message, [role="alert"]',

  // Header
  header: 'header.header',
  headerTitle: 'header h1',
  editModeButton: '.enter-edit-mode-btn',
  editModeDoneButton: '.edit-mode-done-btn',
  editModeCancelButton: '.edit-mode-cancel-btn',
  notificationCenter: '.notification-center',

  // Navigation
  tabNavigation: '.tab-navigation',
  tabButton: (tabId: string) => `[data-tab="${tabId}"]`,

  // Settings Tab
  settingsTab: '[data-tab="settings"]',
  settingsForm: '.settings-form',
  settingsSaveButton: '.settings-save-btn',

  // Scheduled Tasks
  taskList: '.task-list',
  taskItem: '.task-item',
  taskRunButton: '.task-run-btn',
  taskEditButton: '.task-edit-btn',

  // Alert Methods
  alertMethodList: '.alert-method-list',
  alertMethodItem: '.alert-method-item',
  alertMethodAddButton: '.alert-method-add-btn',
  alertMethodTestButton: '.alert-method-test-btn',

  // Channel Manager
  channelsPane: '.channels-pane',
  streamsPane: '.streams-pane',
  channelItem: '.channel-item',
  streamItem: '.stream-item',
  channelGroup: '.channel-group',

  // Modals
  modal: '.modal',
  modalOverlay: '.modal-overlay',
  modalClose: '.modal-close',
  modalConfirm: '.modal-confirm',
  modalCancel: '.modal-cancel',

  // Forms
  input: (name: string) => `input[name="${name}"]`,
  select: (name: string) => `select[name="${name}"]`,
  checkbox: (name: string) => `input[type="checkbox"][name="${name}"]`,
  submitButton: 'button[type="submit"]',

  // Toast notifications
  toast: '.toast',
  toastSuccess: '.toast.toast-success',
  toastError: '.toast.toast-error',
  toastWarning: '.toast.toast-warning',
}

// =============================================================================
// Test Utilities
// =============================================================================

/**
 * Wait for a specific amount of time (use sparingly)
 */
export function sleep(ms: number): Promise<void> {
  return new Promise(resolve => setTimeout(resolve, ms))
}

/**
 * Generate a unique test ID for data isolation
 */
export function generateTestId(): string {
  return `test-${Date.now()}-${Math.random().toString(36).substring(2, 9)}`
}
