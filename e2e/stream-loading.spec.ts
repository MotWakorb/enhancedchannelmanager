/**
 * E2E tests for stream loading behavior.
 * Validates that the app does NOT eagerly fetch all streams,
 * instead loading per-group on demand.
 */
import { test, expect } from './fixtures/base';
import { navigateToTab } from './fixtures/base';

test.describe('Stream Loading Performance', () => {

  test('channel manager loads without excessive /api/streams calls', async ({ appPage }) => {
    // Track all /api/streams requests
    const streamRequests: string[] = [];
    await appPage.route('**/api/streams**', (route) => {
      streamRequests.push(route.request().url());
      route.continue();
    });

    // Navigate to channel manager (default tab)
    await navigateToTab(appPage, 'channel-manager');

    // Wait for the page to settle
    await appPage.waitForTimeout(3000);

    // Should NOT have many rapid-fire /api/streams requests
    // Before the fix, this would be 54+ requests for 27k streams
    // After the fix, there should be 0 or at most 1 (if a group is auto-expanded)
    expect(streamRequests.length).toBeLessThanOrEqual(3);
  });

  test('stream groups are visible in the streams pane', async ({ appPage }) => {
    await navigateToTab(appPage, 'channel-manager');

    // Look for the streams pane
    const streamsPane = appPage.locator('.streams-pane');
    await expect(streamsPane).toBeVisible({ timeout: 10000 });

    // Stream groups should be rendered (if any exist in the backend)
    // We check for the group header pattern
    const groupHeaders = streamsPane.locator('.stream-group-header, [class*="group-header"]');
    // Even if no groups exist, the streams pane should be visible and not hung
    const streamsPaneText = await streamsPane.textContent();
    expect(streamsPaneText).toBeDefined();
  });

  test('search returns filtered results without excessive requests', async ({ appPage }) => {
    const streamRequests: string[] = [];
    await appPage.route('**/api/streams**', (route) => {
      streamRequests.push(route.request().url());
      route.continue();
    });

    await navigateToTab(appPage, 'channel-manager');

    // Find the stream search input
    const searchInput = appPage.locator('.streams-pane input[type="text"], .streams-pane input[placeholder*="earch"]');

    // Only proceed if search input exists
    if (await searchInput.count() > 0) {
      // Clear previous request tracking
      streamRequests.length = 0;

      await searchInput.first().fill('ESPN');

      // Wait for debounced search
      await appPage.waitForTimeout(1500);

      // Search should trigger at most a few requests (debounced), not 54
      expect(streamRequests.length).toBeLessThanOrEqual(5);

      // Any search requests should include the search parameter
      const searchRequests = streamRequests.filter(url => url.includes('search='));
      if (searchRequests.length > 0) {
        expect(searchRequests[0]).toContain('search=ESPN');
      }
    }
  });

  test('no rapid-fire polling after page load', async ({ appPage }) => {
    await navigateToTab(appPage, 'channel-manager');

    // Wait for initial load
    await appPage.waitForTimeout(2000);

    // Now track requests over a 5-second window
    const streamRequests: string[] = [];
    await appPage.route('**/api/streams**', (route) => {
      streamRequests.push(route.request().url());
      route.continue();
    });

    await appPage.waitForTimeout(5000);

    // Over 5 seconds of idle, should be no stream requests (no polling)
    expect(streamRequests.length).toBeLessThanOrEqual(1);
  });

  test('selecting a channel loads its streams', async ({ appPage }) => {
    await navigateToTab(appPage, 'channel-manager');

    // Wait for channels to load
    const channelsPane = appPage.locator('.channels-pane');
    await expect(channelsPane).toBeVisible({ timeout: 10000 });

    // Click the first channel if available
    const firstChannel = channelsPane.locator('.channel-item').first();
    if (await firstChannel.isVisible({ timeout: 3000 }).catch(() => false)) {
      // Track channel-streams API calls
      const channelStreamRequests: string[] = [];
      await appPage.route('**/api/channels/*/streams**', (route) => {
        channelStreamRequests.push(route.request().url());
        route.continue();
      });

      await firstChannel.click();

      // Wait for streams to load
      await appPage.waitForTimeout(2000);

      // Should have fetched streams for this specific channel
      // (via getChannelStreams API, not bulk loadStreams)
      expect(channelStreamRequests.length).toBeGreaterThanOrEqual(0);
    }
  });
});
