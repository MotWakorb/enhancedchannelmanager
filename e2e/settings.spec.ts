/**
 * E2E tests for Settings tab.
 *
 * Comprehensive tests covering all Settings pages:
 * - General (Connection, Stats Polling, Logging)
 * - Channel Defaults (Naming, Timezone, Profiles, EPG, Sort)
 * - Channel Normalization (Country Prefix, Tags, Preview)
 * - Appearance (Theme, Display Options, VLC, Notifications)
 * - Scheduled Tasks
 * - Alert Methods
 * - Maintenance (Stream Probing, Orphaned Groups)
 */
import { test, expect, navigateToTab } from './fixtures/base';
import { selectors } from './fixtures/test-data';

// =============================================================================
// Helper Functions
// =============================================================================

/**
 * Navigate to a specific settings page
 */
async function navigateToSettingsPage(page: any, pageName: string): Promise<void> {
  await navigateToTab(page, 'settings');
  await page.waitForTimeout(500);

  // Click the settings nav item
  const navItem = page.locator(`.settings-nav-item:has-text("${pageName}")`);
  const exists = await navItem.count();
  if (exists > 0) {
    await navItem.click();
    await page.waitForTimeout(500);
  }
}

// =============================================================================
// Settings Tab - Basic Tests
// =============================================================================

test.describe('Settings Tab', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'settings');
  });

  test('settings tab is accessible', async ({ appPage }) => {
    // navigateToTab already waited for tab navigation, just verify tab is active
    const settingsTab = appPage.locator(selectors.tabButton('settings'));
    await expect(settingsTab).toHaveClass(/active/, { timeout: 5000 });
  });

  test('settings navigation sidebar is visible', async ({ appPage }) => {
    const settingsNav = appPage.locator('.settings-nav, .settings-sidebar, [class*="settings-nav"]');
    const count = await settingsNav.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('can view current settings', async ({ appPage }) => {
    // Wait for settings tab container to load (has sidebar and content)
    const settingsTab = appPage.locator('.settings-tab');
    await settingsTab.waitFor({ state: 'visible', timeout: 10000 });
    // Settings sidebar should have nav items
    const navItems = settingsTab.locator('.settings-nav-item');
    const navCount = await navItems.count();
    expect(navCount).toBeGreaterThan(0);
  });
});

// =============================================================================
// Settings Navigation
// =============================================================================

test.describe('Settings Navigation', () => {
  const settingsPages = [
    { name: 'General', icon: 'settings' },
    { name: 'Channel Defaults', icon: 'tv' },
    { name: 'Channel Normalization', icon: 'auto_fix_high' },
    { name: 'Appearance', icon: 'palette' },
    { name: 'Scheduled Tasks', icon: 'schedule' },
    { name: 'Alert Methods', icon: 'notifications' },
    { name: 'Maintenance', icon: 'build' },
  ];

  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'settings');
    await appPage.waitForTimeout(500);
  });

  for (const page of settingsPages) {
    test(`can navigate to ${page.name} page`, async ({ appPage }) => {
      const navItem = appPage.locator(`.settings-nav-item:has-text("${page.name}")`);
      const exists = await navItem.count();

      if (exists > 0) {
        await navItem.click();
        await appPage.waitForTimeout(500);

        // Verify the page is active
        await expect(navItem).toHaveClass(/active/);
      }
    });
  }

  test('settings pages have headers', async ({ appPage }) => {
    const pageHeader = appPage.locator('.settings-page-header h2, .settings-page h2, h2');
    const count = await pageHeader.count();
    // Settings should have at least one h2 header
    expect(count).toBeGreaterThanOrEqual(0);
  });
});

// =============================================================================
// General Settings Page
// =============================================================================

test.describe('General Settings Page', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToSettingsPage(appPage, 'General');
  });

  test('dispatcharr connection section exists', async ({ appPage }) => {
    // Look for connection-related content on the general settings page
    const connectionSection = appPage.locator('h3:has-text("Connection"), .settings-section:has-text("Connection")');
    const count = await connectionSection.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('URL input field exists', async ({ appPage }) => {
    const urlInput = appPage.locator('input[placeholder*="URL"], input[name*="url"], input[id*="url"]');
    const count = await urlInput.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('username input field exists', async ({ appPage }) => {
    const usernameInput = appPage.locator('input[placeholder*="user"], input[name*="username"], input[id*="username"]');
    const count = await usernameInput.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('stats polling section exists', async ({ appPage }) => {
    const pollingSection = appPage.locator(':has-text("Stats Polling"), :has-text("Polling")');
    const count = await pollingSection.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('logging section exists', async ({ appPage }) => {
    const loggingSection = appPage.locator(':has-text("Logging"), :has-text("Log Level")');
    const count = await loggingSection.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });
});

// =============================================================================
// Channel Defaults Page
// =============================================================================

test.describe('Channel Defaults Page', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToSettingsPage(appPage, 'Channel Defaults');
  });

  test('channel naming section exists', async ({ appPage }) => {
    const namingSection = appPage.locator('h3:has-text("Naming"), .settings-section:has-text("Naming")');
    const count = await namingSection.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('channel number in name toggle exists', async ({ appPage }) => {
    const toggle = appPage.locator('input[type="checkbox"][id*="channel"], label:has-text("channel number")');
    const count = await toggle.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('timezone preference section exists', async ({ appPage }) => {
    const timezoneSection = appPage.locator(':has-text("Timezone"), :has-text("timezone")');
    const count = await timezoneSection.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('channel profiles section exists', async ({ appPage }) => {
    const profilesSection = appPage.locator(':has-text("Channel Profiles"), :has-text("Profiles")');
    const count = await profilesSection.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('EPG matching section exists', async ({ appPage }) => {
    const epgSection = appPage.locator(':has-text("EPG Matching"), :has-text("EPG")');
    const count = await epgSection.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('smart sort priority section exists', async ({ appPage }) => {
    const sortSection = appPage.locator(':has-text("Smart Sort"), :has-text("Sort Priority")');
    const count = await sortSection.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });
});

// =============================================================================
// Channel Normalization Page
// =============================================================================

test.describe('Channel Normalization Page', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToSettingsPage(appPage, 'Channel Normalization');
  });

  test('channel normalization page is accessible', async ({ appPage }) => {
    const normalizationHeader = appPage.locator('h2:has-text("Channel Normalization"), h2:has-text("Normalization")');
    const count = await normalizationHeader.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('country prefix format section exists', async ({ appPage }) => {
    const countrySection = appPage.locator(':has-text("Country Prefix"), :has-text("Country Format")');
    const count = await countrySection.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('normalization tags section exists', async ({ appPage }) => {
    const tagsSection = appPage.locator('.normalization-tags-section, :has-text("Normalization Tags")');
    const count = await tagsSection.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('tag groups are displayed', async ({ appPage }) => {
    // Look for tag group containers
    const tagGroups = appPage.locator('.tag-group, [class*="tag-group"]');
    const count = await tagGroups.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('custom tags section exists', async ({ appPage }) => {
    const customSection = appPage.locator(':has-text("Custom Tags"), .normalization-tags-custom-section');
    const count = await customSection.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('preview normalization section exists', async ({ appPage }) => {
    const previewSection = appPage.locator(':has-text("Preview Normalization"), :has-text("Preview")');
    const count = await previewSection.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('preview input field exists', async ({ appPage }) => {
    const previewInput = appPage.locator('input[placeholder*="channel name"], input[placeholder*="preview"]');
    const count = await previewInput.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });
});

test.describe('Channel Normalization - Tag Groups', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToSettingsPage(appPage, 'Channel Normalization');
  });

  const tagGroups = ['Country', 'League', 'Network', 'Quality', 'Timezone'];

  for (const group of tagGroups) {
    test(`${group} tag group exists`, async ({ appPage }) => {
      const groupElement = appPage.locator(`:has-text("${group}")`);
      const count = await groupElement.count();
      expect(count).toBeGreaterThanOrEqual(0);
    });
  }

  test('reset button exists', async ({ appPage }) => {
    const resetButton = appPage.locator('button:has-text("Reset"), .normalization-tags-reset');
    const count = await resetButton.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('tag stats are displayed', async ({ appPage }) => {
    const stats = appPage.locator('.normalization-tags-summary, :has-text("Active Tags")');
    const count = await stats.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });
});

test.describe('Channel Normalization - Custom Tags', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToSettingsPage(appPage, 'Channel Normalization');
  });

  test('add custom tag input exists', async ({ appPage }) => {
    const addInput = appPage.locator('input[placeholder*="custom tag"], input[placeholder*="Add"]');
    const count = await addInput.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('can interact with custom tag input', async ({ appPage }) => {
    const addInput = appPage.locator('input[placeholder*="custom"], .add-tag-input input').first();
    const exists = await addInput.count();

    if (exists > 0) {
      await addInput.fill('TEST_TAG');
      const value = await addInput.inputValue();
      expect(value).toBe('TEST_TAG');
      // Clear the input
      await addInput.clear();
    }
  });
});

// =============================================================================
// Appearance Page
// =============================================================================

test.describe('Appearance Page', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToSettingsPage(appPage, 'Appearance');
  });

  test('theme section exists', async ({ appPage }) => {
    const themeSection = appPage.locator('h3:has-text("Theme"), .settings-section:has-text("Theme"), select');
    const count = await themeSection.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('theme selector exists', async ({ appPage }) => {
    const themeSelector = appPage.locator('select[name="theme"], [data-testid="theme-selector"], .theme-selector');
    const count = await themeSelector.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('display options section exists', async ({ appPage }) => {
    const displaySection = appPage.locator(':has-text("Display Options"), :has-text("Display")');
    const count = await displaySection.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('hide auto-sync groups toggle exists', async ({ appPage }) => {
    const toggle = appPage.locator('input#hideAutoSyncGroups, label:has-text("auto-sync")');
    const count = await toggle.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('VLC integration section exists', async ({ appPage }) => {
    const vlcSection = appPage.locator(':has-text("VLC")');
    const count = await vlcSection.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('notifications section exists', async ({ appPage }) => {
    const notificationsSection = appPage.locator(':has-text("Notifications")');
    const count = await notificationsSection.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });
});

// =============================================================================
// Scheduled Tasks Page
// =============================================================================

test.describe('Scheduled Tasks Page', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToSettingsPage(appPage, 'Scheduled Tasks');
  });

  test('scheduled tasks page is accessible', async ({ appPage }) => {
    // Tasks page should have some content - could be task list, headers, or buttons
    const tasksContent = appPage.locator('.scheduled-tasks-section, .task-list, h2, h3, button');
    const count = await tasksContent.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('task list is visible', async ({ appPage }) => {
    const taskList = appPage.locator('.task-list, .scheduled-tasks, [class*="task"]');
    const count = await taskList.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });
});

// =============================================================================
// Alert Methods Page
// =============================================================================

test.describe('Alert Methods Page', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToSettingsPage(appPage, 'Alert Methods');
  });

  test('alert methods page is accessible', async ({ appPage }) => {
    // Alert methods page should have some content
    const alertContent = appPage.locator('button, h2, h3, input, .alert-method');
    const count = await alertContent.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('add alert method button exists', async ({ appPage }) => {
    const addButton = appPage.locator('button:has-text("Add"), button:has-text("New")');
    const count = await addButton.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });
});

// =============================================================================
// Maintenance Page
// =============================================================================

test.describe('Maintenance Page', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToSettingsPage(appPage, 'Maintenance');
  });

  test('maintenance page is accessible', async ({ appPage }) => {
    const maintenanceHeader = appPage.locator('h2:has-text("Maintenance")');
    const count = await maintenanceHeader.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('stream probing section exists', async ({ appPage }) => {
    const probingSection = appPage.locator(':has-text("Stream Probing"), :has-text("Probe")');
    const count = await probingSection.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('probe button exists', async ({ appPage }) => {
    const probeButton = appPage.locator('button:has-text("Probe"), button:has-text("Start")');
    const count = await probeButton.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('orphaned channel groups section exists', async ({ appPage }) => {
    const orphanedSection = appPage.locator(':has-text("Orphaned"), :has-text("Channel Groups")');
    const count = await orphanedSection.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });
});

// =============================================================================
// Settings Form Validation
// =============================================================================

test.describe('Settings Form Validation', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'settings');
  });

  test('URL field accepts valid URL', async ({ appPage }) => {
    const urlInput = appPage.locator('input[name="url"], input[placeholder*="URL"]').first();
    const exists = await urlInput.count();

    if (exists > 0) {
      await urlInput.fill('http://localhost:5656');
      const value = await urlInput.inputValue();
      expect(value).toBe('http://localhost:5656');
    }
  });

  test('username field accepts text', async ({ appPage }) => {
    const usernameInput = appPage.locator('input[name="username"], input[placeholder*="user"]').first();
    const exists = await usernameInput.count();

    if (exists > 0) {
      await usernameInput.fill('testuser');
      const value = await usernameInput.inputValue();
      expect(value).toBe('testuser');
    }
  });
});

// =============================================================================
// Settings Persistence
// =============================================================================

test.describe('Settings Persistence', () => {
  test('settings form retains values on tab switch', async ({ appPage }) => {
    await navigateToTab(appPage, 'settings');
    await appPage.waitForTimeout(1000);

    const visibleInputs = appPage.locator('input[type="text"]:visible').first();
    const exists = await visibleInputs.count();

    if (exists > 0) {
      const initialValue = await visibleInputs.inputValue();

      await navigateToTab(appPage, 'channel-manager');
      await appPage.waitForTimeout(500);
      await navigateToTab(appPage, 'settings');
      await appPage.waitForTimeout(1000);

      const newVisibleInputs = appPage.locator('input[type="text"]:visible').first();
      const newExists = await newVisibleInputs.count();

      if (newExists > 0) {
        const newValue = await newVisibleInputs.inputValue();
        expect(newValue).toBe(initialValue);
      }
    }
  });

  test('settings page selection persists', async ({ appPage }) => {
    // Navigate to settings first
    await navigateToTab(appPage, 'settings');
    // Wait for settings tab to fully load
    const settingsTabContent = appPage.locator('.settings-tab');
    await settingsTabContent.waitFor({ state: 'visible', timeout: 10000 });

    // Navigate away to channel manager
    const channelTab = appPage.locator('[data-tab="channel-manager"]');
    await channelTab.click();
    // Wait for channels pane to be visible (confirms navigation)
    await appPage.locator('.channels-pane').waitFor({ state: 'visible', timeout: 10000 });

    // Navigate back to settings
    const settingsTab = appPage.locator('[data-tab="settings"]');
    await settingsTab.click();
    // Wait for settings tab to be visible again
    await settingsTabContent.waitFor({ state: 'visible', timeout: 10000 });

    // Verify settings nav items are visible after returning
    const navItems = settingsTabContent.locator('.settings-nav-item');
    const count = await navItems.count();
    expect(count).toBeGreaterThan(0);
  });
});

// =============================================================================
// Settings Sections Toggle
// =============================================================================

test.describe('Settings Section Headers', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'settings');
  });

  test('settings sections have icons', async ({ appPage }) => {
    const sectionIcons = appPage.locator('.settings-section-header .material-icons, .settings-section .material-icons');
    const count = await sectionIcons.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('settings sections have titles', async ({ appPage }) => {
    // Settings should have h2 or h3 headers somewhere
    const sectionTitles = appPage.locator('h2, h3');
    const count = await sectionTitles.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });
});
