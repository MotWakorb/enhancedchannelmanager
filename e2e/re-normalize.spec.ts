/**
 * E2E tests for the re-normalize-existing-channels flow (bd-eio04.12).
 *
 * Exercises the Apply-to-Channels modal added to the Channel Normalization
 * settings page: rule-trace drawer, conflict-group winner pick, confirmation
 * modal, and post-execute summary.
 *
 * The backend is mocked via Playwright's `page.route` so the test can seed
 * three channels with known Unicode suffixes without having to stand up a
 * live Dispatcharr instance.
 */
import { test, expect, navigateToTab } from './fixtures/base';

test.describe('Re-normalize existing channels (bd-eio04.12)', () => {
  test.beforeEach(async ({ appPage }) => {
    // -----------------------------------------------------------------
    // Intercept all normalization API calls the settings page makes.
    // The responses are tuned so the Apply-to-Channels modal renders
    // three channels, one conflict group, and one happy-path rename.
    // -----------------------------------------------------------------
    await appPage.route('**/api/normalization/rules', (route) => {
      route.fulfill({ json: { groups: [] } });
    });

    await appPage.route('**/api/tags/groups', (route) => {
      route.fulfill({ json: { groups: [] } });
    });

    // Dry-run returns the preview diff
    await appPage.route(
      '**/api/normalization/apply-to-channels?dry_run=true',
      (route) => {
        route.fulfill({
          json: {
            dry_run: true,
            channels_with_changes: 3,
            diffs: [
              {
                channel_id: 1,
                current_name: 'RTL ᴿᴬᵂ',
                proposed_name: 'RTL',
                normalized_core: 'RTL',
                channel_number_prefix: '',
                group_id: 5,
                group_name: 'DE',
                collision: false,
                collision_target_id: null,
                collision_target_name: null,
                collision_target_group_id: null,
                collision_target_group_name: null,
                suggested_action: 'rename',
                transformations: [
                  { rule_id: 101, before: 'RTL ᴿᴬᵂ', after: 'RTL' },
                ],
              },
              {
                channel_id: 2,
                current_name: 'RTL ꜰʜᴅ',
                proposed_name: 'RTL',
                normalized_core: 'RTL',
                channel_number_prefix: '',
                group_id: 5,
                group_name: 'DE',
                collision: false,
                collision_target_id: null,
                collision_target_name: null,
                collision_target_group_id: null,
                collision_target_group_name: null,
                suggested_action: 'rename',
                transformations: [
                  { rule_id: 102, before: 'RTL ꜰʜᴅ', after: 'RTL' },
                ],
              },
              {
                channel_id: 3,
                current_name: 'Pro7 ᴴᴰ',
                proposed_name: 'Pro7',
                normalized_core: 'Pro7',
                channel_number_prefix: '',
                group_id: 5,
                group_name: 'DE',
                collision: false,
                collision_target_id: null,
                collision_target_name: null,
                collision_target_group_id: null,
                collision_target_group_name: null,
                suggested_action: 'rename',
                transformations: [
                  { rule_id: 103, before: 'Pro7 ᴴᴰ', after: 'Pro7' },
                ],
              },
            ],
          },
        });
      }
    );

    // Execute mode returns the happy-path summary
    await appPage.route(
      '**/api/normalization/apply-to-channels?dry_run=false',
      (route) => {
        route.fulfill({
          json: {
            dry_run: false,
            status: 'completed',
            renamed: [
              { channel_id: 1, old_name: 'RTL ᴿᴬᵂ', new_name: 'RTL' },
              { channel_id: 3, old_name: 'Pro7 ᴴᴰ', new_name: 'Pro7' },
            ],
            merged: [],
            skipped: [{ channel_id: 2, reason: 'skip' }],
            errors: [],
            rule_set_hash: 'e2e-test-hash',
          },
        });
      }
    );

    await navigateToTab(appPage, 'settings');

    // Open the Channel Normalization settings page from the sidebar.
    const navItem = appPage.locator(
      '.settings-nav-item:has-text("Channel Normalization")'
    );
    await navItem.first().click();
    await appPage
      .getByTestId('apply-to-channels-btn')
      .waitFor({ state: 'visible', timeout: 10_000 });
  });

  test('renders the preview, resolves a conflict group, executes and shows summary', async ({
    appPage,
  }) => {
    // Open the Apply-to-Channels modal
    await appPage.getByTestId('apply-to-channels-btn').click();

    const modal = appPage.getByTestId('apply-to-channels-modal');
    await expect(modal).toBeVisible();

    // All three seeded rows render
    await expect(appPage.getByTestId('apply-row-1')).toBeVisible();
    await expect(appPage.getByTestId('apply-row-2')).toBeVisible();
    await expect(appPage.getByTestId('apply-row-3')).toBeVisible();

    // Channels 1 + 2 both normalize to "RTL" → source-collision group
    // with a conflict-group badge on both rows.
    await expect(
      appPage.getByTestId('apply-conflict-badge-1')
    ).toBeVisible();
    await expect(
      appPage.getByTestId('apply-conflict-badge-2')
    ).toBeVisible();

    // Execute stays disabled while the conflict group is unresolved.
    const executeBtn = appPage.getByTestId('apply-to-channels-execute');
    await expect(executeBtn).toBeDisabled();

    // Expand the rule-trace drawer for row 1 and verify it renders
    const traceToggle = appPage.getByTestId('apply-trace-toggle-1');
    await expect(traceToggle).toHaveAttribute('aria-expanded', 'false');
    await traceToggle.click();
    await expect(traceToggle).toHaveAttribute('aria-expanded', 'true');
    const drawer = appPage.getByTestId('apply-trace-drawer-1');
    await expect(drawer).toBeVisible();
    await expect(drawer).toContainText('Rule 101');

    // Pick channel 1 as the conflict-group winner. Loser's action flips
    // to 'skip', winner's to 'rename'; Execute enables.
    await appPage.getByTestId('apply-winner-radio-1').check();
    await expect(executeBtn).toBeEnabled();

    // Execute opens the confirmation modal first.
    await executeBtn.click();
    const confirm = appPage.getByTestId('apply-to-channels-confirm');
    await expect(confirm).toBeVisible();
    await expect(
      appPage.getByTestId('apply-to-channels-confirm-count')
    ).toContainText(/channel/);

    // Confirm — fires the POST and renders the summary block.
    await appPage.getByTestId('apply-to-channels-confirm-execute').click();

    const summary = appPage.getByTestId('apply-to-channels-summary');
    await expect(summary).toBeVisible({ timeout: 10_000 });
    await expect(summary).toContainText('2 renamed');
    await expect(summary).toContainText('0 merged');
    await expect(summary).toContainText('1 skipped');
    await expect(summary).toContainText('0 failed');
    await expect(summary).toContainText('e2e-test-hash');
    await expect(
      appPage.getByTestId('apply-to-channels-summary-journal-link')
    ).toHaveAttribute('href', '#journal');
  });
});
