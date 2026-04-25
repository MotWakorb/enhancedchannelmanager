/**
 * Shared test data for E2E tests.
 *
 * Provides mock data factories and constants for consistent testing.
 */
import { randomBytes } from 'crypto'

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

  // Auto-Creation
  autoCreationTab: '.auto-creation-tab, [data-testid="auto-creation-tab"]',
  autoCreationRulesList: '.auto-creation-rules-list, [data-testid="rules-list"]',
  autoCreationRuleItem: '.auto-creation-rule-item, [data-testid="rule-row"]',
  autoCreationCreateRuleBtn: 'button:has-text("Create Rule"), .create-rule-btn',
  autoCreationRunBtn: 'button:has-text("Run"):not(:has-text("Dry")), .run-pipeline-btn',
  autoCreationDryRunBtn: 'button:has-text("Dry Run"), .dry-run-btn',
  autoCreationImportBtn: 'button:has-text("Import"), .import-btn',
  autoCreationExportBtn: 'button:has-text("Export"), .export-btn',
  autoCreationRuleBuilder: '.rule-builder, [data-testid="rule-builder"]',
  autoCreationRuleNameInput: 'input[name="ruleName"], input[aria-label*="Rule name"], .rule-name-input',
  autoCreationRuleDescInput: 'textarea[name="description"], input[name="description"], .rule-description-input',
  autoCreationRulePriorityInput: 'input[name="priority"], input[type="number"][aria-label*="Priority"], .rule-priority-input',
  autoCreationRuleEnabledCheckbox: 'input[name="enabled"], input[type="checkbox"][aria-label*="Enabled"], .rule-enabled-checkbox',
  autoCreationAddConditionBtn: 'button:has-text("Add Condition"), .add-condition-btn',
  autoCreationAddActionBtn: 'button:has-text("Add Action"), .add-action-btn',
  autoCreationConditionEditor: '.condition-editor, [data-testid="condition-editor"]',
  autoCreationActionEditor: '.action-editor, [data-testid="action-editor"]',
  autoCreationSaveRuleBtn: 'button:has-text("Save"), .save-rule-btn',
  autoCreationCancelBtn: 'button:has-text("Cancel"), .cancel-btn',
  autoCreationExecutionHistory: '.execution-history, [data-testid="execution-history"]',
  autoCreationExecutionItem: '.execution-item, [data-testid="execution-row"]',
  autoCreationRollbackBtn: 'button:has-text("Rollback"), .rollback-btn',
  autoCreationViewDetailsBtn: 'button:has-text("View Details"), .view-details-btn',
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
  return `test-${Date.now()}-${randomBytes(4).toString('hex')}`
}
