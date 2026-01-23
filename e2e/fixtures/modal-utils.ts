/**
 * Modal Testing Utilities
 *
 * Provides utilities for testing modal layout, overflow, and visual regression.
 * Use these to ensure modals display correctly after CSS changes.
 */
import { Page, Locator, expect } from '@playwright/test'

// =============================================================================
// Types
// =============================================================================

export interface ModalLayoutCheck {
  /** Modal is visible in viewport */
  isVisible: boolean
  /** Modal container has no horizontal overflow */
  noHorizontalOverflow: boolean
  /** Modal container has no unexpected vertical overflow */
  noVerticalOverflow: boolean
  /** Header is visible and not clipped */
  headerVisible: boolean
  /** Footer is visible and not clipped */
  footerVisible: boolean
  /** Close button is visible and clickable */
  closeButtonAccessible: boolean
  /** Modal is centered in viewport */
  isCentered: boolean
  /** Content area is scrollable if needed */
  bodyScrollable: boolean
  /** All primary action buttons visible */
  actionButtonsVisible: boolean
}

export interface ModalBounds {
  top: number
  left: number
  width: number
  height: number
  viewportWidth: number
  viewportHeight: number
}

// =============================================================================
// Modal Locators
// =============================================================================

/**
 * Common modal selectors used across the app
 */
export const modalSelectors = {
  // Overlay/backdrop
  overlay: '.modal-overlay, [class*="modal-overlay"]',

  // Container (the modal box itself)
  container: '.modal-container, .modal-content, [class*="modal"]:not([class*="overlay"])',

  // Header elements
  header: '.modal-header, [class*="modal-header"]',
  headerTitle: '.modal-header h2, .modal-title, [class*="modal-header"] h2',
  closeButton: '.modal-close-btn, .close-btn, button[class*="close"]',

  // Body/content
  body: '.modal-body, [class*="modal-body"], [class*="modal-content"]',

  // Footer
  footer: '.modal-footer, [class*="modal-footer"]',
  primaryButton: '.modal-footer .btn-primary, .modal-footer button[class*="primary"]',
  secondaryButton: '.modal-footer .btn-secondary, .modal-footer button[class*="secondary"]',
}

// =============================================================================
// Layout Check Functions
// =============================================================================

/**
 * Get the bounding box of the modal relative to viewport
 */
export async function getModalBounds(page: Page, modalLocator?: Locator): Promise<ModalBounds | null> {
  const modal = modalLocator || page.locator(modalSelectors.container).first()

  if (!(await modal.isVisible())) {
    return null
  }

  const box = await modal.boundingBox()
  const viewport = page.viewportSize()

  if (!box || !viewport) {
    return null
  }

  return {
    top: box.y,
    left: box.x,
    width: box.width,
    height: box.height,
    viewportWidth: viewport.width,
    viewportHeight: viewport.height,
  }
}

/**
 * Check if modal content is overflowing its container
 */
export async function checkOverflow(page: Page, selector: string): Promise<{ horizontal: boolean; vertical: boolean }> {
  return await page.evaluate((sel) => {
    const element = document.querySelector(sel)
    if (!element) {
      return { horizontal: false, vertical: false }
    }

    const horizontal = element.scrollWidth > element.clientWidth
    const vertical = element.scrollHeight > element.clientHeight

    return { horizontal, vertical }
  }, selector)
}

/**
 * Check if an element is fully visible within viewport (not clipped)
 */
export async function isFullyVisible(page: Page, locator: Locator): Promise<boolean> {
  if (!(await locator.count())) {
    return false
  }

  const box = await locator.first().boundingBox()
  const viewport = page.viewportSize()

  if (!box || !viewport) {
    return false
  }

  return (
    box.x >= 0 &&
    box.y >= 0 &&
    box.x + box.width <= viewport.width &&
    box.y + box.height <= viewport.height
  )
}

/**
 * Run comprehensive layout checks on a modal
 */
export async function checkModalLayout(page: Page, containerSelector?: string): Promise<ModalLayoutCheck> {
  const container = containerSelector || modalSelectors.container
  const modal = page.locator(container).first()

  // Check visibility
  const isVisible = await modal.isVisible().catch(() => false)

  if (!isVisible) {
    return {
      isVisible: false,
      noHorizontalOverflow: true,
      noVerticalOverflow: true,
      headerVisible: false,
      footerVisible: false,
      closeButtonAccessible: false,
      isCentered: false,
      bodyScrollable: false,
      actionButtonsVisible: false,
    }
  }

  // Check overflow on container
  const containerOverflow = await checkOverflow(page, container)

  // Check overflow on body (should be scrollable, not overflow)
  const bodySelector = `${container} ${modalSelectors.body.split(',')[0]}`
  const bodyOverflow = await checkOverflow(page, bodySelector)

  // Check header visibility
  const header = page.locator(`${container} ${modalSelectors.header.split(',')[0]}, ${container} [class*="header"]`).first()
  const headerVisible = await header.isVisible().catch(() => false)

  // Check footer visibility
  const footer = page.locator(`${container} ${modalSelectors.footer.split(',')[0]}, ${container} [class*="footer"]`).first()
  const footerVisible = await footer.isVisible().catch(() => false)

  // Check close button
  const closeButton = page.locator(`${container} ${modalSelectors.closeButton}`).first()
  const closeButtonAccessible = await closeButton.isVisible().catch(() => false)

  // Check centering
  const bounds = await getModalBounds(page, modal)
  const isCentered = bounds
    ? Math.abs((bounds.viewportWidth - bounds.width) / 2 - bounds.left) < 20
    : false

  // Check if body is scrollable when content overflows
  const bodyScrollable = await page.evaluate((sel) => {
    const body = document.querySelector(sel)
    if (!body) return false
    const style = window.getComputedStyle(body)
    return style.overflowY === 'auto' || style.overflowY === 'scroll'
  }, bodySelector)

  // Check action buttons
  const primaryBtn = page.locator(`${container} button[class*="primary"], ${container} .btn-primary`).first()
  const actionButtonsVisible = await primaryBtn.isVisible().catch(() => false)

  return {
    isVisible,
    noHorizontalOverflow: !containerOverflow.horizontal,
    noVerticalOverflow: !containerOverflow.vertical || bodyScrollable,
    headerVisible,
    footerVisible,
    closeButtonAccessible,
    isCentered,
    bodyScrollable: bodyOverflow.vertical ? bodyScrollable : true,
    actionButtonsVisible: footerVisible ? actionButtonsVisible : true,
  }
}

// =============================================================================
// Modal Interaction Helpers
// =============================================================================

/**
 * Wait for a modal to be fully visible and stable
 */
export async function waitForModal(page: Page, timeout = 5000): Promise<Locator> {
  const modal = page.locator(modalSelectors.overlay).first()
  await modal.waitFor({ state: 'visible', timeout })
  // Wait for animation to complete
  await page.waitForTimeout(300)
  return modal
}

/**
 * Close the currently open modal
 */
export async function closeModal(page: Page): Promise<void> {
  const closeBtn = page.locator(modalSelectors.closeButton).first()
  if (await closeBtn.isVisible()) {
    await closeBtn.click()
    await page.waitForSelector(modalSelectors.overlay, { state: 'hidden', timeout: 3000 }).catch(() => {})
  }
}

/**
 * Click outside modal to close (if overlay click-to-close is enabled)
 */
export async function clickOutsideModal(page: Page): Promise<void> {
  const overlay = page.locator(modalSelectors.overlay).first()
  const modal = page.locator(modalSelectors.container).first()

  const overlayBox = await overlay.boundingBox()
  const modalBox = await modal.boundingBox()

  if (overlayBox && modalBox) {
    // Click to the left of the modal
    await page.mouse.click(modalBox.x - 50, modalBox.y + modalBox.height / 2)
  }
}

// =============================================================================
// Visual Regression Helpers
// =============================================================================

/**
 * Take a screenshot of just the modal for comparison
 */
export async function screenshotModal(page: Page, name: string): Promise<Buffer> {
  const modal = page.locator(modalSelectors.container).first()
  return await modal.screenshot({
    animations: 'disabled',
  })
}

/**
 * Take a full page screenshot with modal visible
 */
export async function screenshotModalInContext(page: Page, name: string): Promise<Buffer> {
  return await page.screenshot({
    fullPage: false,
    animations: 'disabled',
  })
}

// =============================================================================
// Assertion Helpers
// =============================================================================

/**
 * Assert that modal layout is correct (no overflow, elements visible)
 */
export async function assertModalLayoutCorrect(page: Page, modalName: string, containerSelector?: string): Promise<void> {
  const layout = await checkModalLayout(page, containerSelector)

  expect(layout.isVisible, `${modalName}: Modal should be visible`).toBe(true)
  expect(layout.noHorizontalOverflow, `${modalName}: Should have no horizontal overflow`).toBe(true)
  expect(layout.noVerticalOverflow, `${modalName}: Should handle vertical overflow properly`).toBe(true)
  expect(layout.headerVisible, `${modalName}: Header should be visible`).toBe(true)
  expect(layout.isCentered, `${modalName}: Modal should be centered`).toBe(true)
}

/**
 * Assert that modal footer and actions are accessible
 */
export async function assertModalActionsAccessible(page: Page, modalName: string, containerSelector?: string): Promise<void> {
  const layout = await checkModalLayout(page, containerSelector)

  expect(layout.footerVisible, `${modalName}: Footer should be visible`).toBe(true)
  expect(layout.closeButtonAccessible, `${modalName}: Close button should be accessible`).toBe(true)
}

/**
 * Run all modal layout assertions
 */
export async function assertModalFullyFunctional(page: Page, modalName: string, containerSelector?: string): Promise<void> {
  await assertModalLayoutCorrect(page, modalName, containerSelector)
  await assertModalActionsAccessible(page, modalName, containerSelector)
}
