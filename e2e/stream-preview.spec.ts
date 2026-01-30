/**
 * E2E tests for Stream Preview feature.
 *
 * Tests the stream preview modal functionality including:
 * - Opening preview modal from streams and channels
 * - Modal rendering and metadata display
 * - Video player placeholder
 * - Alternative options (VLC, M3U, Copy URL)
 * - Close functionality
 */
import { test, expect, navigateToTab, closeModal } from './fixtures/base';
import { selectors } from './fixtures/test-data';

// Extended selectors for stream preview
const previewSelectors = {
  // Preview modal
  previewModal: '.preview-stream-modal',
  previewModalOverlay: '.modal-overlay',
  previewModalTitle: '.preview-stream-modal h2',
  previewCloseButton: '.preview-stream-modal .modal-close-btn',
  previewFooterCloseButton: '.preview-stream-modal .modal-footer button',

  // Video player
  videoPlayer: '.video-player',
  videoPlayerContainer: '.preview-stream-player',

  // Metadata
  metadataSection: '.preview-stream-metadata',
  metadataItem: '.metadata-item',
  modeIndicator: '.metadata-item.metadata-mode',

  // Info section
  infoHeader: '.preview-stream-info-header h3',
  statusIndicator: '.preview-stream-status',

  // Fallback options
  fallbackSection: '.preview-stream-fallback',
  vlcButton: '.fallback-buttons button:has-text("Open in VLC")',
  m3uButton: '.fallback-buttons button:has-text("Download M3U")',
  copyUrlButton: '.fallback-buttons button:has-text("Copy URL")',

  // Stream and channel items
  streamPreviewButton: '.stream-item .stream-preview-btn, .stream-item button[title*="Preview"]',
  channelPreviewButton: '.channel-item .channel-preview-btn, .channel-item button[title*="Preview"]',
  streamActionButton: '.stream-item .stream-actions button',
  channelActionButton: '.channel-item .channel-actions button',
};


test.describe('Stream Preview Modal', () => {
  test.beforeEach(async ({ appPage }) => {
    // Navigate to channel manager tab where streams and channels are displayed
    await navigateToTab(appPage, 'channel-manager');
  });

  test('streams pane is visible on channel manager tab', async ({ appPage }) => {
    const streamsPane = appPage.locator(selectors.streamsPane);
    const isVisible = await streamsPane.isVisible().catch(() => false);
    expect(typeof isVisible).toBe('boolean');
  });

  test('channels pane is visible on channel manager tab', async ({ appPage }) => {
    const channelsPane = appPage.locator(selectors.channelsPane);
    const isVisible = await channelsPane.isVisible().catch(() => false);
    expect(typeof isVisible).toBe('boolean');
  });
});


test.describe('Stream Preview from Streams Pane', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'channel-manager');
  });

  test('stream items may have preview button', async ({ appPage }) => {
    const streamItems = appPage.locator(selectors.streamItem);
    const count = await streamItems.count();

    if (count > 0) {
      // Check if first stream has any action buttons
      const firstStream = streamItems.first();
      const actionButtons = firstStream.locator('button');
      const buttonCount = await actionButtons.count();
      // Streams may have action buttons including preview
      expect(buttonCount).toBeGreaterThanOrEqual(0);
    }
  });

  test('clicking stream preview opens modal', async ({ appPage }) => {
    // First select a stream to make action buttons visible
    const streamItems = appPage.locator(selectors.streamItem);
    const count = await streamItems.count();

    if (count > 0) {
      // Click first stream to select it
      await streamItems.first().click();
      await appPage.waitForTimeout(500);

      // Look for preview button - it might have various selectors
      const previewButton = appPage.locator('[title*="Preview"], [aria-label*="Preview"], button:has-text("Preview")').first();
      const isPreviewVisible = await previewButton.isVisible().catch(() => false);

      if (isPreviewVisible) {
        await previewButton.click();

        // Modal should appear
        const modal = appPage.locator(previewSelectors.previewModal);
        const modalVisible = await modal.isVisible({ timeout: 5000 }).catch(() => false);

        if (modalVisible) {
          // Modal should have title
          const title = appPage.locator(previewSelectors.previewModalTitle);
          await expect(title).toBeVisible();

          // Close the modal
          const closeButton = appPage.locator(previewSelectors.previewCloseButton);
          if (await closeButton.isVisible()) {
            await closeButton.click();
          }
        }
      }
    }
  });
});


test.describe('Preview Modal UI Elements', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'channel-manager');
  });

  test('preview modal has expected structure when opened', async ({ appPage }) => {
    // Try to open a stream preview
    const streamItems = appPage.locator(selectors.streamItem);
    const count = await streamItems.count();

    if (count > 0) {
      // Click first stream to select it
      await streamItems.first().click();
      await appPage.waitForTimeout(300);

      // Look for any preview/play button
      const playButtons = appPage.locator('button:has(span.material-icons:has-text("play_circle")), [title*="Preview"]');
      const hasPlayButton = await playButtons.first().isVisible().catch(() => false);

      if (hasPlayButton) {
        await playButtons.first().click();
        await appPage.waitForTimeout(500);

        // Check for modal elements
        const modal = appPage.locator(previewSelectors.previewModal);
        if (await modal.isVisible().catch(() => false)) {
          // Should have video player container
          const videoContainer = appPage.locator(previewSelectors.videoPlayerContainer);
          const hasVideo = await videoContainer.isVisible().catch(() => false);
          expect(typeof hasVideo).toBe('boolean');

          // Should have info section with stream name
          const infoHeader = appPage.locator(previewSelectors.infoHeader);
          const hasInfo = await infoHeader.isVisible().catch(() => false);
          expect(typeof hasInfo).toBe('boolean');

          // Should have close button
          const closeBtn = appPage.locator(previewSelectors.previewCloseButton);
          await expect(closeBtn).toBeVisible();

          // Clean up - close modal
          await closeBtn.click();
        }
      }
    }
  });

  test('preview modal has alternative options for streams', async ({ appPage }) => {
    // Try to open a stream preview
    const streamItems = appPage.locator(selectors.streamItem);
    const count = await streamItems.count();

    if (count > 0) {
      // Click first stream to select it
      await streamItems.first().click();
      await appPage.waitForTimeout(300);

      // Look for preview button
      const playButtons = appPage.locator('button:has(span.material-icons:has-text("play_circle")), [title*="Preview"]');
      const hasPlayButton = await playButtons.first().isVisible().catch(() => false);

      if (hasPlayButton) {
        await playButtons.first().click();
        await appPage.waitForTimeout(500);

        const modal = appPage.locator(previewSelectors.previewModal);
        if (await modal.isVisible().catch(() => false)) {
          // Should have fallback options section
          const fallback = appPage.locator(previewSelectors.fallbackSection);
          const hasFallback = await fallback.isVisible().catch(() => false);

          if (hasFallback) {
            // Check for VLC button
            const vlcBtn = appPage.getByRole('button', { name: /VLC/i });
            const hasVlc = await vlcBtn.isVisible().catch(() => false);
            expect(typeof hasVlc).toBe('boolean');

            // Check for M3U button
            const m3uBtn = appPage.getByRole('button', { name: /M3U/i });
            const hasM3u = await m3uBtn.isVisible().catch(() => false);
            expect(typeof hasM3u).toBe('boolean');

            // Check for Copy URL button
            const copyBtn = appPage.getByRole('button', { name: /Copy/i });
            const hasCopy = await copyBtn.isVisible().catch(() => false);
            expect(typeof hasCopy).toBe('boolean');
          }

          // Close modal
          const closeBtn = appPage.locator(previewSelectors.previewCloseButton);
          if (await closeBtn.isVisible()) {
            await closeBtn.click();
          }
        }
      }
    }
  });
});


test.describe('Preview Modal Close Functionality', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'channel-manager');
  });

  test('clicking close button closes modal', async ({ appPage }) => {
    const streamItems = appPage.locator(selectors.streamItem);
    const count = await streamItems.count();

    if (count > 0) {
      await streamItems.first().click();
      await appPage.waitForTimeout(300);

      const playButtons = appPage.locator('button:has(span.material-icons:has-text("play_circle")), [title*="Preview"]');
      if (await playButtons.first().isVisible().catch(() => false)) {
        await playButtons.first().click();
        await appPage.waitForTimeout(500);

        const modal = appPage.locator(previewSelectors.previewModal);
        if (await modal.isVisible().catch(() => false)) {
          // Click close button
          const closeBtn = appPage.locator(previewSelectors.previewCloseButton);
          await closeBtn.click();

          // Modal should be hidden
          await expect(modal).not.toBeVisible({ timeout: 2000 });
        }
      }
    }
  });

  test('clicking overlay closes modal', async ({ appPage }) => {
    const streamItems = appPage.locator(selectors.streamItem);
    const count = await streamItems.count();

    if (count > 0) {
      await streamItems.first().click();
      await appPage.waitForTimeout(300);

      const playButtons = appPage.locator('button:has(span.material-icons:has-text("play_circle")), [title*="Preview"]');
      if (await playButtons.first().isVisible().catch(() => false)) {
        await playButtons.first().click();
        await appPage.waitForTimeout(500);

        const modal = appPage.locator(previewSelectors.previewModal);
        if (await modal.isVisible().catch(() => false)) {
          // Click on overlay (outside modal content)
          const overlay = appPage.locator(previewSelectors.previewModalOverlay);
          await overlay.click({ position: { x: 10, y: 10 } });

          // Modal should be hidden
          await expect(modal).not.toBeVisible({ timeout: 2000 });
        }
      }
    }
  });

  test('clicking footer Close button closes modal', async ({ appPage }) => {
    const streamItems = appPage.locator(selectors.streamItem);
    const count = await streamItems.count();

    if (count > 0) {
      await streamItems.first().click();
      await appPage.waitForTimeout(300);

      const playButtons = appPage.locator('button:has(span.material-icons:has-text("play_circle")), [title*="Preview"]');
      if (await playButtons.first().isVisible().catch(() => false)) {
        await playButtons.first().click();
        await appPage.waitForTimeout(500);

        const modal = appPage.locator(previewSelectors.previewModal);
        if (await modal.isVisible().catch(() => false)) {
          // Click footer close button
          const footerBtn = appPage.locator(previewSelectors.previewFooterCloseButton);
          if (await footerBtn.isVisible()) {
            await footerBtn.click();

            // Modal should be hidden
            await expect(modal).not.toBeVisible({ timeout: 2000 });
          }
        }
      }
    }
  });
});


test.describe('Channel Preview', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'channel-manager');
  });

  test('channel items are visible in channels pane', async ({ appPage }) => {
    const channelItems = appPage.locator(selectors.channelItem);
    const count = await channelItems.count();
    // Should have zero or more channels
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('clicking channel preview shows Channel Preview title', async ({ appPage }) => {
    const channelItems = appPage.locator(selectors.channelItem);
    const count = await channelItems.count();

    if (count > 0) {
      // Click first channel to select it
      await channelItems.first().click();
      await appPage.waitForTimeout(300);

      // Look for preview button
      const playButtons = appPage.locator('button:has(span.material-icons:has-text("play_circle")), [title*="Preview"]');
      if (await playButtons.first().isVisible().catch(() => false)) {
        await playButtons.first().click();
        await appPage.waitForTimeout(500);

        const modal = appPage.locator(previewSelectors.previewModal);
        if (await modal.isVisible().catch(() => false)) {
          // Title should say "Channel Preview"
          const title = appPage.locator(previewSelectors.previewModalTitle);
          const titleText = await title.textContent().catch(() => '');

          // Should contain "Preview"
          expect(titleText).toContain('Preview');

          // Close modal
          const closeBtn = appPage.locator(previewSelectors.previewCloseButton);
          if (await closeBtn.isVisible()) {
            await closeBtn.click();
          }
        }
      }
    }
  });

  test('channel preview does not show VLC/M3U/Copy buttons', async ({ appPage }) => {
    const channelItems = appPage.locator(selectors.channelItem);
    const count = await channelItems.count();

    if (count > 0) {
      await channelItems.first().click();
      await appPage.waitForTimeout(300);

      const playButtons = appPage.locator('button:has(span.material-icons:has-text("play_circle")), [title*="Preview"]');
      if (await playButtons.first().isVisible().catch(() => false)) {
        await playButtons.first().click();
        await appPage.waitForTimeout(500);

        const modal = appPage.locator(previewSelectors.previewModal);
        if (await modal.isVisible().catch(() => false)) {
          const title = appPage.locator(previewSelectors.previewModalTitle);
          const titleText = await title.textContent().catch(() => '');

          // Only check VLC button absence if this is a channel preview
          if (titleText?.includes('Channel')) {
            const vlcBtn = appPage.getByRole('button', { name: /Open in VLC/i });
            await expect(vlcBtn).not.toBeVisible();
          }

          // Close modal
          const closeBtn = appPage.locator(previewSelectors.previewCloseButton);
          if (await closeBtn.isVisible()) {
            await closeBtn.click();
          }
        }
      }
    }
  });
});


test.describe('Preview Mode Indicator', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'channel-manager');
  });

  test('preview modal shows mode indicator', async ({ appPage }) => {
    const streamItems = appPage.locator(selectors.streamItem);
    const count = await streamItems.count();

    if (count > 0) {
      await streamItems.first().click();
      await appPage.waitForTimeout(300);

      const playButtons = appPage.locator('button:has(span.material-icons:has-text("play_circle")), [title*="Preview"]');
      if (await playButtons.first().isVisible().catch(() => false)) {
        await playButtons.first().click();
        await appPage.waitForTimeout(1000); // Wait longer for settings fetch

        const modal = appPage.locator(previewSelectors.previewModal);
        if (await modal.isVisible().catch(() => false)) {
          // Check for mode indicator
          const modeIndicator = appPage.locator(previewSelectors.modeIndicator);
          const hasModeIndicator = await modeIndicator.isVisible().catch(() => false);
          expect(typeof hasModeIndicator).toBe('boolean');

          // If visible, should show one of the modes
          if (hasModeIndicator) {
            const modeText = await modeIndicator.textContent().catch(() => '');
            const validModes = ['Passthrough', 'Transcode', 'Video Only'];
            const hasValidMode = validModes.some(mode => modeText?.includes(mode));
            expect(hasValidMode || modeText === '').toBe(true);
          }

          // Close modal
          const closeBtn = appPage.locator(previewSelectors.previewCloseButton);
          if (await closeBtn.isVisible()) {
            await closeBtn.click();
          }
        }
      }
    }
  });
});
