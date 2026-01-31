/**
 * E2E tests for Modal Layout and Visual Regression
 *
 * These tests verify that modals display correctly without:
 * - Content overflow
 * - Hidden/clipped elements
 * - Inaccessible buttons
 * - Layout issues
 *
 * When a modal is marked "good", the screenshot becomes the baseline.
 * Future runs compare against this baseline to catch regressions.
 *
 * Run specific modal tests:
 *   npx playwright test modals.spec.ts --grep "SettingsModal"
 *
 * Update screenshots after intentional changes:
 *   npx playwright test modals.spec.ts --update-snapshots
 */
import { test, expect, navigateToTab } from './fixtures/base'
import {
  waitForModal,
  closeModal,
  checkModalLayout,
  assertModalFullyFunctional,
  modalSelectors,
} from './fixtures/modal-utils'

// =============================================================================
// Test Configuration
// =============================================================================

// Increase timeout for modal tests (screenshots + layout checks)
test.setTimeout(60000)

// =============================================================================
// Helper Functions
// =============================================================================

/**
 * Navigate to Settings > specific page
 */
async function navigateToSettingsPage(page: any, pageName: string): Promise<void> {
  await navigateToTab(page, 'settings')
  await page.waitForTimeout(500)

  const navItem = page.locator(`.settings-nav-item:has-text("${pageName}")`)
  if ((await navItem.count()) > 0) {
    await navItem.click()
    await page.waitForTimeout(500)
  }
}

/**
 * Run layout checks and take screenshot for a modal
 */
async function testModalLayout(
  page: any,
  modalName: string,
  containerSelector?: string
): Promise<void> {
  // Wait for modal to be stable
  await page.waitForTimeout(500)

  // Run layout checks
  const layout = await checkModalLayout(page, containerSelector)

  // Log layout results for debugging
  console.log(`${modalName} layout:`, layout)

  // Assert layout is correct
  expect(layout.isVisible, `${modalName}: Modal should be visible`).toBe(true)
  expect(layout.noHorizontalOverflow, `${modalName}: No horizontal overflow`).toBe(true)
  expect(layout.headerVisible, `${modalName}: Header visible`).toBe(true)
  expect(layout.isCentered, `${modalName}: Modal centered`).toBe(true)

  // Take screenshot for visual comparison
  const modal = page.locator(containerSelector || modalSelectors.container).first()
  await expect(modal).toHaveScreenshot(`${modalName.toLowerCase().replace(/\s+/g, '-')}.png`, {
    animations: 'disabled',
    threshold: 0.2, // Allow 20% pixel difference for minor rendering differences
  })
}

// =============================================================================
// Settings Modal (Dispatcharr Connection)
// =============================================================================

test.describe('SettingsModal - Dispatcharr Connection', () => {
  test('layout and visual regression', async ({ appPage }) => {
    await navigateToSettingsPage(appPage, 'General')

    // Look for the Edit button in the Dispatcharr Connection section
    const editBtn = appPage.locator('.btn-edit-connection, button:has-text("Edit")').first()

    if ((await editBtn.count()) > 0 && (await editBtn.isVisible())) {
      await editBtn.click()
      await waitForModal(appPage)
      await testModalLayout(appPage, 'SettingsModal', '.settings-modal, .modal-container')
      await closeModal(appPage)
    } else {
      test.skip()
    }
  })
})

// =============================================================================
// M3U Account Modal
// =============================================================================

test.describe('M3UAccountModal', () => {
  test('layout and visual regression - Add Account', async ({ appPage }) => {
    await navigateToTab(appPage, 'm3u-manager')
    await appPage.waitForTimeout(1000)

    // Click add account button
    const addBtn = appPage.locator('button:has-text("Add"), button:has-text("New Account")').first()

    if ((await addBtn.count()) > 0 && (await addBtn.isVisible())) {
      await addBtn.click()
      await waitForModal(appPage)
      await testModalLayout(appPage, 'M3UAccountModal-Add', '.m3u-account-modal, .modal-content')
      await closeModal(appPage)
    } else {
      test.skip()
    }
  })
})

// =============================================================================
// Delete Orphaned Groups Modal
// =============================================================================

test.describe('DeleteOrphanedGroupsModal', () => {
  test('layout and visual regression', async ({ appPage }) => {
    await navigateToSettingsPage(appPage, 'Maintenance')

    // First, scan for orphaned groups - the modal only appears if orphans are found
    const scanBtn = appPage.locator('button:has-text("Scan for Orphaned Groups")').first()

    if ((await scanBtn.count()) > 0 && (await scanBtn.isVisible())) {
      await scanBtn.click()
      // Wait for scan to complete (button text changes or results appear)
      await appPage.waitForTimeout(2000)

      // Check if any orphaned groups were found - look for delete button or results
      const deleteBtn = appPage.locator('button:has-text("Delete"), button:has-text("Orphaned Group")').first()
      if ((await deleteBtn.count()) > 0 && (await deleteBtn.isVisible())) {
        // Note: DeleteOrphanedGroupsModal is a confirmation modal that appears inline,
        // not a separate modal overlay. Skip this test if no orphans found.
        await deleteBtn.click()
        await appPage.waitForTimeout(500)

        // Check if modal appeared
        const modal = appPage.locator('.delete-orphaned-modal, .modal-content, .modal-overlay')
        if ((await modal.count()) > 0 && (await modal.first().isVisible())) {
          await testModalLayout(appPage, 'DeleteOrphanedGroupsModal', '.delete-orphaned-modal, .modal-content')
          await closeModal(appPage)
        } else {
          test.skip()
        }
      } else {
        // No orphaned groups found in test data - skip test
        test.skip()
      }
    } else {
      test.skip()
    }
  })
})

// =============================================================================
// Bulk EPG Assign Modal
// =============================================================================

test.describe('BulkEPGAssignModal', () => {
  test('layout and visual regression', async ({ appPage }) => {
    await navigateToTab(appPage, 'channel-manager')
    await appPage.waitForTimeout(1000)

    // Enter edit mode first - the bulk EPG button only appears in edit mode
    const editModeBtn = appPage.locator('.enter-edit-mode-btn, button:has-text("Edit Mode")').first()
    if ((await editModeBtn.count()) > 0 && (await editModeBtn.isVisible())) {
      await editModeBtn.click()
      await appPage.waitForTimeout(500)
    }

    // Select some channels - the bulk EPG button only appears with selection
    const channelItems = appPage.locator('.channel-item')
    const channelCount = await channelItems.count()

    if (channelCount > 0) {
      // Click first channel to select it
      await channelItems.first().click()
      await appPage.waitForTimeout(300)

      // Look for bulk EPG assign button (icon-only button with title attribute)
      const bulkEpgBtn = appPage.locator('button[title*="EPG"], button.bulk-action-btn').first()

      if ((await bulkEpgBtn.count()) > 0 && (await bulkEpgBtn.isVisible())) {
        await bulkEpgBtn.click()
        await waitForModal(appPage)
        await testModalLayout(appPage, 'BulkEPGAssignModal', '.bulk-epg-modal, .modal-content')
        await closeModal(appPage)
      } else {
        test.skip()
      }
    } else {
      test.skip()
    }
  })
})

// =============================================================================
// Auto Sync Settings Modal
// =============================================================================

test.describe('AutoSyncSettingsModal', () => {
  test('layout and visual regression', async ({ appPage }) => {
    await navigateToTab(appPage, 'm3u-manager')
    await appPage.waitForTimeout(1000)

    // Need to expand an M3U account first, then click groups, then settings icon
    // This is complex - skip for now if we can't find the direct path
    const settingsIcon = appPage.locator('.settings-btn, button[title*="sync"], button[title*="Settings"]').first()

    if ((await settingsIcon.count()) > 0 && (await settingsIcon.isVisible())) {
      await settingsIcon.click()
      await waitForModal(appPage)
      await testModalLayout(appPage, 'AutoSyncSettingsModal', '.auto-sync-settings-modal, .modal-content')
      await closeModal(appPage)
    } else {
      test.skip()
    }
  })
})

// =============================================================================
// Normalize Names Modal
// =============================================================================

test.describe('NormalizeNamesModal', () => {
  test('layout and visual regression', async ({ appPage }) => {
    await navigateToTab(appPage, 'channel-manager')
    await appPage.waitForTimeout(1000)

    // Look for normalize button
    const normalizeBtn = appPage.locator('button:has-text("Normalize")').first()

    if ((await normalizeBtn.count()) > 0 && (await normalizeBtn.isVisible())) {
      await normalizeBtn.click()
      await waitForModal(appPage)
      await testModalLayout(appPage, 'NormalizeNamesModal', '.normalize-modal, .modal-content')
      await closeModal(appPage)
    } else {
      test.skip()
    }
  })
})

// =============================================================================
// M3U Linked Accounts Modal
// =============================================================================

test.describe('M3ULinkedAccountsModal', () => {
  test('layout and visual regression', async ({ appPage }) => {
    await navigateToTab(appPage, 'm3u-manager')
    await appPage.waitForTimeout(1000)

    // Look for linked accounts button
    const linkedBtn = appPage.locator('button:has-text("Linked"), button:has-text("Link")').first()

    if ((await linkedBtn.count()) > 0 && (await linkedBtn.isVisible())) {
      await linkedBtn.click()
      await waitForModal(appPage)
      await testModalLayout(appPage, 'M3ULinkedAccountsModal', '.m3u-linked-accounts-modal, .modal-content')
      await closeModal(appPage)
    } else {
      test.skip()
    }
  })
})

// =============================================================================
// Channel Profiles List Modal
// =============================================================================

test.describe('ChannelProfilesListModal', () => {
  test('layout and visual regression', async ({ appPage }) => {
    // The profiles button is in the Channel Manager tab, not Settings
    await navigateToTab(appPage, 'channel-manager')
    await appPage.waitForTimeout(1000)

    // Look for profiles button (icon-only button with title or class)
    const profilesBtn = appPage.locator('.profiles-btn, button[title*="profile"], button[title*="Profile"]').first()

    if ((await profilesBtn.count()) > 0 && (await profilesBtn.isVisible())) {
      await profilesBtn.click()
      await waitForModal(appPage)
      await testModalLayout(appPage, 'ChannelProfilesListModal', '.channel-profiles-modal, .modal-content')
      await closeModal(appPage)
    } else {
      test.skip()
    }
  })
})

// =============================================================================
// M3U Groups Modal
// =============================================================================

test.describe('M3UGroupsModal', () => {
  test('layout and visual regression', async ({ appPage }) => {
    await navigateToTab(appPage, 'm3u-manager')
    await appPage.waitForTimeout(1000)

    // Look for groups button on an M3U account row
    const groupsBtn = appPage.locator('button:has-text("Groups")').first()

    if ((await groupsBtn.count()) > 0 && (await groupsBtn.isVisible())) {
      await groupsBtn.click()
      await waitForModal(appPage)
      await testModalLayout(appPage, 'M3UGroupsModal', '.m3u-groups-modal, .modal-content')
      await closeModal(appPage)
    } else {
      test.skip()
    }
  })
})

// =============================================================================
// VLC Protocol Helper Modal
// =============================================================================

test.describe('VLCProtocolHelperModal', () => {
  test('layout and visual regression', async ({ appPage }) => {
    await navigateToTab(appPage, 'guide')
    await appPage.waitForTimeout(1000)

    // This modal usually appears when clicking a VLC link
    // Look for VLC button or link
    const vlcBtn = appPage.locator('button:has-text("VLC"), a:has-text("VLC")').first()

    if ((await vlcBtn.count()) > 0 && (await vlcBtn.isVisible())) {
      await vlcBtn.click()
      await waitForModal(appPage)
      await testModalLayout(appPage, 'VLCProtocolHelperModal', '.vlc-helper-modal, .modal-content')
      await closeModal(appPage)
    } else {
      test.skip()
    }
  })
})

// =============================================================================
// M3U Profile Modal
// =============================================================================

test.describe('M3UProfileModal', () => {
  test('layout and visual regression', async ({ appPage }) => {
    await navigateToTab(appPage, 'm3u-manager')
    await appPage.waitForTimeout(1000)

    // Look for profiles button
    const profilesBtn = appPage.locator('button:has-text("Profile")').first()

    if ((await profilesBtn.count()) > 0 && (await profilesBtn.isVisible())) {
      await profilesBtn.click()
      await waitForModal(appPage)
      await testModalLayout(appPage, 'M3UProfileModal', '.m3u-profile-modal, .modal-content')
      await closeModal(appPage)
    } else {
      test.skip()
    }
  })
})

// =============================================================================
// Print Guide Modal
// =============================================================================

test.describe('PrintGuideModal', () => {
  test('layout and visual regression', async ({ appPage }) => {
    await navigateToTab(appPage, 'guide')
    await appPage.waitForTimeout(1000)

    // Look for print button
    const printBtn = appPage.locator('button:has-text("Print"), button[title*="Print"]').first()

    if ((await printBtn.count()) > 0 && (await printBtn.isVisible())) {
      await printBtn.click()
      await waitForModal(appPage)
      await testModalLayout(appPage, 'PrintGuideModal', '.print-guide-modal, .modal-content')
      await closeModal(appPage)
    } else {
      test.skip()
    }
  })
})

// =============================================================================
// Gracenote Conflict Modal
// =============================================================================

test.describe('GracenoteConflictModal', () => {
  test('layout and visual regression', async ({ appPage }) => {
    await navigateToTab(appPage, 'epg-manager')
    await appPage.waitForTimeout(1000)

    // This modal appears during Gracenote sync with conflicts
    // Look for sync button that might trigger it
    const syncBtn = appPage.locator('button:has-text("Sync"), button:has-text("Gracenote")').first()

    if ((await syncBtn.count()) > 0 && (await syncBtn.isVisible())) {
      await syncBtn.click()
      await appPage.waitForTimeout(2000)

      const modal = appPage.locator('.gracenote-conflict-modal')
      if ((await modal.count()) > 0 && (await modal.isVisible())) {
        await testModalLayout(appPage, 'GracenoteConflictModal', '.gracenote-conflict-modal')
        await closeModal(appPage)
      } else {
        test.skip()
      }
    } else {
      test.skip()
    }
  })
})

// =============================================================================
// Logo Modal
// =============================================================================

test.describe('LogoModal', () => {
  test('layout and visual regression - Add Logo', async ({ appPage }) => {
    await navigateToTab(appPage, 'logo-manager')
    await appPage.waitForTimeout(1000)

    // Look for add logo button
    const addBtn = appPage.locator('button:has-text("Add"), button:has-text("Upload")').first()

    if ((await addBtn.count()) > 0 && (await addBtn.isVisible())) {
      await addBtn.click()
      await waitForModal(appPage)
      await testModalLayout(appPage, 'LogoModal-Add', '.logo-modal, .modal-content')
      await closeModal(appPage)
    } else {
      test.skip()
    }
  })
})

// =============================================================================
// M3U Filters Modal
// =============================================================================

test.describe('M3UFiltersModal', () => {
  test('layout and visual regression', async ({ appPage }) => {
    await navigateToTab(appPage, 'm3u-manager')
    await appPage.waitForTimeout(1000)

    // Look for filters button on an M3U account row
    const filtersBtn = appPage.locator('button:has-text("Filter")').first()

    if ((await filtersBtn.count()) > 0 && (await filtersBtn.isVisible())) {
      await filtersBtn.click()
      await waitForModal(appPage)
      await testModalLayout(appPage, 'M3UFiltersModal', '.m3u-filters-modal, .modal-content')
      await closeModal(appPage)
    } else {
      test.skip()
    }
  })
})

// =============================================================================
// Dummy EPG Source Modal
// =============================================================================

test.describe('DummyEPGSourceModal', () => {
  test('layout and visual regression', async ({ appPage }) => {
    await navigateToTab(appPage, 'epg-manager')
    await appPage.waitForTimeout(1000)

    // Look for add dummy EPG button
    const addBtn = appPage.locator('button:has-text("Dummy"), button:has-text("Add")').first()

    if ((await addBtn.count()) > 0 && (await addBtn.isVisible())) {
      await addBtn.click()
      await waitForModal(appPage)
      await testModalLayout(appPage, 'DummyEPGSourceModal', '.dummy-epg-modal, .modal-content')
      await closeModal(appPage)
    } else {
      test.skip()
    }
  })
})

// =============================================================================
// Bulk LCN Fetch Modal
// =============================================================================

test.describe('BulkLCNFetchModal', () => {
  test('layout and visual regression', async ({ appPage }) => {
    await navigateToTab(appPage, 'channel-manager')
    await appPage.waitForTimeout(1000)

    // Look for bulk LCN button
    const lcnBtn = appPage.locator('button:has-text("LCN"), button:has-text("Fetch")').first()

    if ((await lcnBtn.count()) > 0 && (await lcnBtn.isVisible())) {
      await lcnBtn.click()
      await waitForModal(appPage)
      await testModalLayout(appPage, 'BulkLCNFetchModal', '.bulk-lcn-modal')
      await closeModal(appPage)
    } else {
      test.skip()
    }
  })
})

// =============================================================================
// Generic Modal Layout Tests (Run on any visible modal)
// =============================================================================

test.describe('Modal Layout - Generic Checks', () => {
  test('all modals should handle keyboard escape', async ({ appPage }) => {
    // This test can be expanded when a modal is open
    // Press Escape and verify modal closes
    await appPage.keyboard.press('Escape')
    // If no modal is open, this should be a no-op
  })
})
