/**
 * E2E tests for ECM Dummy EPG Profiles.
 *
 * Tests the full flow: create profile -> configure patterns/substitutions ->
 * assign channels -> preview pipeline -> verify XMLTV output.
 */
import { test, expect, navigateToTab, waitForToast } from './fixtures/base';
import { generateTestId } from './fixtures/test-data';

const PROFILE_NAME = `E2E Test Profile ${generateTestId()}`;

test.describe('Dummy EPG Profiles', () => {
  test.describe.configure({ mode: 'serial' });

  let profileId: number;

  test('EPG Manager tab shows ECM Dummy EPG section', async ({ appPage: page }) => {
    await navigateToTab(page, 'epg-manager');

    const heading = page.getByRole('heading', { name: 'ECM Dummy EPG Profiles' });
    await expect(heading).toBeVisible();

    const addButton = page.getByRole('button', { name: /Add Profile/i });
    await expect(addButton).toBeVisible();
  });

  test('create profile with patterns and templates', async ({ appPage: page }) => {
    await navigateToTab(page, 'epg-manager');

    // Open the create modal
    const section = page.locator('.dep-manager-section');
    await section.getByRole('button', { name: /Add Profile/i }).click();

    // Wait for modal
    const modal = page.locator('.modal-overlay');
    await expect(modal).toBeVisible();
    await expect(page.getByRole('heading', { name: 'New Dummy EPG Profile' })).toBeVisible();

    // Fill name
    await page.getByRole('textbox', { name: /Name \*/i }).fill(PROFILE_NAME);

    // Fill title pattern using evaluate to avoid HTML encoding issues with angle brackets
    await page.evaluate((pattern) => {
      const input = document.querySelector('input[placeholder*="(?<league>"]') as HTMLInputElement;
      if (input) {
        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
        setter.call(input, pattern);
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
      }
    }, String.raw`(?<league>\w+)\s+\d+:\s+(?<team1>.*?)\s+VS\s+(?<team2>.*?)(?:\s+@|$)`);

    // Fill time pattern
    await page.evaluate((pattern) => {
      const input = document.querySelector('input[placeholder*="@ (?<hour>"]') as HTMLInputElement;
      if (input) {
        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
        setter.call(input, pattern);
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
      }
    }, String.raw`@\s+(?<hour>\d+):(?<minute>\d+)(?<ampm>AM|PM)`);

    // Fill title template
    await page.getByRole('textbox', { name: /Title Template/i }).fill('{league}: {team1} vs {team2}');

    // Fill description template
    await page.getByRole('textbox', { name: /Description Template/i }).fill('Watch {team1} vs {team2} in {league}');

    // Create the profile
    await page.getByRole('button', { name: /Create Profile/i }).click();

    // Verify profile appears in the list
    const newProfileRow = section.locator('.dep-profile-row', { hasText: PROFILE_NAME });
    await expect(newProfileRow).toBeVisible({ timeout: 5000 });
    await expect(newProfileRow.getByText('0 channels')).toBeVisible();

    // Extract profile ID from the API for later tests
    const response = await page.request.get('/api/dummy-epg/profiles');
    const profiles = await response.json();
    const created = profiles.find((p: { name: string }) => p.name === PROFILE_NAME);
    expect(created).toBeTruthy();
    profileId = created.id;
  });

  test('preview pipeline works with sample name', async ({ appPage: page }) => {
    await navigateToTab(page, 'epg-manager');

    // Open edit modal for the profile
    const section = page.locator('.dep-manager-section');
    const profileRow = section.locator('.dep-profile-row', { hasText: PROFILE_NAME });
    await profileRow.getByRole('button', { name: /edit/i }).click();

    // Wait for modal
    await expect(page.getByRole('heading', { name: /Edit Profile/i })).toBeVisible();

    // Fill sample name for preview using Playwright's native fill (works with React)
    const sampleInput = page.getByPlaceholder('League 01:');
    await sampleInput.fill('DV1 01: North State VS South Valley @ 8:00PM ET');

    // Wait for debounced preview to fire (600ms debounce + network)
    const previewSection = page.locator('.modal-preview-section');
    await expect(previewSection.getByText('Extracted Groups:')).toBeVisible({ timeout: 8000 });
    await expect(previewSection.getByText('"league": "DV1"')).toBeVisible();
    await expect(previewSection.getByText('"team1": "North State"')).toBeVisible();
    await expect(previewSection.getByText('"team2": "South Valley"')).toBeVisible();

    // Verify rendered title
    await expect(previewSection.getByText('DV1: North State vs South Valley')).toBeVisible();

    // Close the modal
    await page.getByRole('button', { name: /Cancel/i }).click();
  });

  test('channel picker opens and shows channels', async ({ appPage: page }) => {
    await navigateToTab(page, 'epg-manager');

    // Open channel picker for the profile
    const section = page.locator('.dep-manager-section');
    const profileRow = section.locator('.dep-profile-row', { hasText: PROFILE_NAME });
    await profileRow.getByRole('button', { name: /people/i }).click();

    // Wait for channel picker modal
    await expect(page.getByRole('heading', { name: new RegExp(`Channels - ${PROFILE_NAME}`) })).toBeVisible({ timeout: 5000 });

    // Verify both panels exist
    await expect(page.getByRole('heading', { name: /Assigned \(0\)/ })).toBeVisible();
    await expect(page.getByRole('heading', { name: /Available \(\d+\)/ })).toBeVisible();

    // Verify search input exists
    await expect(page.getByPlaceholder('Search channels...')).toBeVisible();

    // Close the picker (use the footer Close button, not the X)
    await page.locator('.modal-footer').getByRole('button', { name: 'Close' }).click();
  });

  test('toggle profile enable/disable', async ({ appPage: page }) => {
    await navigateToTab(page, 'epg-manager');

    const section = page.locator('.dep-manager-section');
    const profileRow = section.locator('.dep-profile-row', { hasText: PROFILE_NAME });

    // Click the toggle button (currently enabled = toggle_on)
    const toggleBtn = profileRow.locator('.action-btn').filter({ hasText: 'toggle_on' });
    await toggleBtn.click();

    // Should now show toggle_off (disabled)
    await expect(profileRow.locator('.action-btn').filter({ hasText: 'toggle_off' })).toBeVisible({ timeout: 3000 });

    // Re-enable
    const toggleOffBtn = profileRow.locator('.action-btn').filter({ hasText: 'toggle_off' });
    await toggleOffBtn.click();
    await expect(profileRow.locator('.action-btn').filter({ hasText: 'toggle_on' })).toBeVisible({ timeout: 3000 });
  });

  test('XMLTV endpoint returns valid XML', async ({ appPage: page }) => {
    // Test the combined XMLTV endpoint
    const response = await page.request.get('/api/dummy-epg/xmltv');
    expect(response.status()).toBe(200);
    expect(response.headers()['content-type']).toContain('application/xml');

    const xml = await response.text();
    expect(xml).toContain('<?xml');
    expect(xml).toContain('<tv');

    // Test per-profile XMLTV endpoint
    if (profileId) {
      const profileResponse = await page.request.get(`/api/dummy-epg/xmltv/${profileId}`);
      expect(profileResponse.status()).toBe(200);
      expect(profileResponse.headers()['content-type']).toContain('application/xml');
    }
  });

  test('preview API returns correct structure', async ({ appPage: page }) => {
    const response = await page.request.post('/api/dummy-epg/preview', {
      data: {
        sample_name: 'DV1 01: North State VS South Valley @ 8:00PM ET',
        title_pattern: String.raw`(?<league>\w+)\s+\d+:\s+(?<team1>.*?)\s+VS\s+(?<team2>.*?)(?:\s+@|$)`,
        time_pattern: String.raw`@\s+(?<hour>\d+):(?<minute>\d+)(?<ampm>AM|PM)`,
        substitution_pairs: [],
        title_template: '{league}: {team1} vs {team2}',
        description_template: 'Watch {team1} vs {team2} in {league}',
        event_timezone: 'US/Eastern',
        program_duration: 180,
      },
    });

    expect(response.status()).toBe(200);
    const result = await response.json();

    expect(result.matched).toBe(true);
    expect(result.groups.league).toBe('DV1');
    expect(result.groups.team1).toBe('North State');
    expect(result.groups.team2).toBe('South Valley');
    expect(result.rendered.title).toBe('DV1: North State vs South Valley');
    expect(result.rendered.description).toBe('Watch North State vs South Valley in DV1');
    expect(result.time_variables).toBeTruthy();
    expect(result.time_variables.starttime).toContain('PM');
  });

  test('preview API handles no-match gracefully', async ({ appPage: page }) => {
    const response = await page.request.post('/api/dummy-epg/preview', {
      data: {
        sample_name: 'no match here',
        title_pattern: String.raw`(?<league>\w+)\s+\d+:\s+(?<team1>.*?)\s+VS\s+(?<team2>.*)`,
        substitution_pairs: [],
        event_timezone: 'US/Eastern',
        program_duration: 180,
      },
    });

    expect(response.status()).toBe(200);
    const result = await response.json();
    expect(result.matched).toBe(false);
    expect(result.groups).toBeNull();
  });

  test('preview API with substitution pairs', async ({ appPage: page }) => {
    const response = await page.request.post('/api/dummy-epg/preview', {
      data: {
        sample_name: 'DV1 01: North State VS South Valley @ 8:00PM ET',
        title_pattern: String.raw`(?<league>\w+)\s+\d+:\s+(?<team1>.*?)\s+VS\s+(?<team2>.*?)(?:\s+@|$)`,
        substitution_pairs: [
          { find: 'DV1', replace: 'CLGF', is_regex: false, enabled: true },
        ],
        title_template: '{league}: {team1} vs {team2}',
        event_timezone: 'US/Eastern',
        program_duration: 180,
      },
    });

    expect(response.status()).toBe(200);
    const result = await response.json();

    expect(result.matched).toBe(true);
    expect(result.substituted_name).toContain('CLGF');
    expect(result.groups.league).toBe('CLGF');
    expect(result.substitution_steps).toHaveLength(1);
  });

  test('delete profile cleans up', async ({ appPage: page }) => {
    await navigateToTab(page, 'epg-manager');

    const section = page.locator('.dep-manager-section');
    const profileRow = section.locator('.dep-profile-row', { hasText: PROFILE_NAME });

    // Click delete
    page.on('dialog', dialog => dialog.accept());
    await profileRow.getByRole('button', { name: /delete/i }).click();

    // Profile should be gone from the list
    await expect(profileRow).not.toBeVisible({ timeout: 5000 });

    // Verify via API
    const response = await page.request.get('/api/dummy-epg/profiles');
    const profiles = await response.json();
    const deleted = profiles.find((p: { name: string }) => p.name === PROFILE_NAME);
    expect(deleted).toBeUndefined();
  });
});
