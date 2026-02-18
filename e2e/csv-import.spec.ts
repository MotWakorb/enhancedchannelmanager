/**
 * E2E tests for CSV Import functionality.
 *
 * Tests the CSV import workflow including file upload, preview, validation, and import.
 */
import { test, expect, navigateToTab, enterEditMode } from './fixtures/base';
import { selectors } from './fixtures/test-data';
import { Page } from '@playwright/test';
import path from 'path';

// CSV Import selectors
const csvSelectors = {
  importButton: '[data-testid="csv-import-button"], button:has-text("Import CSV")',
  exportButton: '[data-testid="csv-export-button"], button:has-text("Export CSV")',
  templateButton: '[data-testid="csv-template-button"], button:has-text("Download Template")',
  importModal: '[data-testid="csv-import-modal"], .csv-import-modal',
  modalTitle: '.modal-title, .csv-import-modal h2',
  fileInput: 'input[type="file"]',
  dropzone: '[data-testid="csv-dropzone"], .dropzone, .file-upload-area',
  previewTable: '[data-testid="csv-preview-table"]',
  previewRow: '[data-testid="csv-preview-row"]',
  errorList: '.csv-validation-errors, [data-testid="csv-errors"]',
  errorItem: '[data-testid="csv-error-item"]',
  importSubmitButton: '[data-testid="csv-import-submit"]',
  cancelButton: '.csv-import-modal button:has-text("Cancel")',
  successMessage: '[data-testid="csv-success"]',
  progressIndicator: '[data-testid="csv-progress"]',
  errorBanner: '.modal-error-banner',
};

/**
 * Upload a CSV file and wait for the preview table to appear.
 * Retries the upload if the API returns a transient "Failed to fetch" error,
 * which can happen when the backend is under load from parallel tests.
 */
async function uploadCSVAndWaitForPreview(
  page: Page,
  csvContent: string,
  maxRetries = 2,
): Promise<void> {
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    const fileInput = page.locator(csvSelectors.fileInput);
    await fileInput.setInputFiles({
      name: 'test.csv',
      mimeType: 'text/csv',
      buffer: Buffer.from(csvContent),
    });

    // Wait for either the preview table or an error banner
    const previewTable = page.locator(csvSelectors.previewTable);
    const errorBanner = page.locator(csvSelectors.errorBanner);

    const result = await Promise.race([
      previewTable.waitFor({ state: 'visible', timeout: 10000 }).then(() => 'preview'),
      errorBanner.waitFor({ state: 'visible', timeout: 10000 }).then(() => 'error'),
    ]).catch(() => 'timeout');

    if (result === 'preview') {
      return; // Success
    }

    if (attempt < maxRetries) {
      // Dismiss error and retry: close modal and reopen
      const cancelButton = page.locator(csvSelectors.cancelButton);
      await cancelButton.click();
      await page.waitForTimeout(500);
      const importButton = page.locator(csvSelectors.importButton);
      await importButton.click();
      const modal = page.locator(csvSelectors.importModal);
      await expect(modal).toBeVisible({ timeout: 5000 });
    }
  }

  // Final attempt failed: let the normal assertion fail with a clear message
  const previewTable = page.locator(csvSelectors.previewTable);
  await expect(previewTable).toBeVisible({ timeout: 5000 });
}

test.describe('CSV Import', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'channel-manager');
  });

  test('import button is visible in channels toolbar', async ({ appPage }) => {
    // Enter edit mode first (CSV import requires edit mode)
    await enterEditMode(appPage);

    const importButton = appPage.locator(csvSelectors.importButton);
    await expect(importButton).toBeVisible({ timeout: 5000 });
  });

  test('clicking import button opens import modal', async ({ appPage }) => {
    await enterEditMode(appPage);

    const importButton = appPage.locator(csvSelectors.importButton);
    await importButton.click();

    const modal = appPage.locator(csvSelectors.importModal);
    await expect(modal).toBeVisible({ timeout: 5000 });
  });

  test('import modal has file upload area', async ({ appPage }) => {
    await enterEditMode(appPage);

    const importButton = appPage.locator(csvSelectors.importButton);
    await importButton.click();

    const dropzone = appPage.locator(csvSelectors.dropzone);
    await expect(dropzone).toBeVisible({ timeout: 5000 });
  });

  test('import modal can be closed with cancel button', async ({ appPage }) => {
    await enterEditMode(appPage);

    const importButton = appPage.locator(csvSelectors.importButton);
    await importButton.click();

    const modal = appPage.locator(csvSelectors.importModal);
    await expect(modal).toBeVisible();

    const cancelButton = appPage.locator(csvSelectors.cancelButton);
    await cancelButton.click();

    await expect(modal).not.toBeVisible({ timeout: 5000 });
  });

  test('uploading valid CSV shows preview', async ({ appPage }) => {
    await enterEditMode(appPage);

    const importButton = appPage.locator(csvSelectors.importButton);
    await importButton.click();

    // Upload a valid CSV file with retry on transient API failures
    await uploadCSVAndWaitForPreview(appPage, 'channel_number,name,group_name\n101,ESPN HD,Sports\n102,CNN,News');

    // Should show 2 rows in preview
    const previewRows = appPage.locator(csvSelectors.previewRow);
    await expect(previewRows).toHaveCount(2);
  });

  test('uploading CSV with validation errors shows error list', async ({ appPage }) => {
    await enterEditMode(appPage);

    const importButton = appPage.locator(csvSelectors.importButton);
    await importButton.click();

    // Wait for modal to be fully open
    const modal = appPage.locator(csvSelectors.importModal);
    await expect(modal).toBeVisible({ timeout: 5000 });

    // Upload CSV with invalid data (missing name)
    const fileInput = appPage.locator(csvSelectors.fileInput);
    await fileInput.setInputFiles({
      name: 'test.csv',
      mimeType: 'text/csv',
      buffer: Buffer.from('channel_number,name,group_name\n101,,Sports')
    });

    // Should show error list (allow time for CSV parsing)
    const errorList = appPage.locator(csvSelectors.errorList);
    await expect(errorList).toBeVisible({ timeout: 10000 });
  });

  test('uploading CSV without name column shows error', async ({ appPage }) => {
    await enterEditMode(appPage);

    const importButton = appPage.locator(csvSelectors.importButton);
    await importButton.click();

    // Wait for modal to be fully open
    const modal = appPage.locator(csvSelectors.importModal);
    await expect(modal).toBeVisible({ timeout: 5000 });

    // Upload CSV missing required column
    const fileInput = appPage.locator(csvSelectors.fileInput);
    await fileInput.setInputFiles({
      name: 'test.csv',
      mimeType: 'text/csv',
      buffer: Buffer.from('channel_number,group_name\n101,Sports')
    });

    // Should show error about missing name column (allow time for CSV parsing)
    const errorList = appPage.locator(csvSelectors.errorList);
    await expect(errorList).toBeVisible({ timeout: 10000 });
  });

  test('can submit valid CSV for import', async ({ appPage }) => {
    test.slow(); // This test makes two API calls (preview + import)
    await enterEditMode(appPage);

    const importButton = appPage.locator(csvSelectors.importButton);
    await importButton.click();

    // Wait for modal to be fully open
    const modal = appPage.locator(csvSelectors.importModal);
    await expect(modal).toBeVisible({ timeout: 5000 });

    // Upload valid CSV with retry on transient API failures
    await uploadCSVAndWaitForPreview(appPage, 'channel_number,name,group_name\n999,Test Channel,Test Group');

    // Click import button
    const submitButton = appPage.locator(csvSelectors.importSubmitButton);
    await submitButton.click();

    // Should show progress or success (allow time for API import)
    const progress = appPage.locator(`${csvSelectors.progressIndicator}, ${csvSelectors.successMessage}`);
    await expect(progress).toBeVisible({ timeout: 20000 });
  });

  test('shows success message after import completes', async ({ appPage }) => {
    test.slow(); // This test makes two API calls (preview + import)
    await enterEditMode(appPage);

    const importButton = appPage.locator(csvSelectors.importButton);
    await importButton.click();

    // Wait for modal to be fully open
    const modal = appPage.locator(csvSelectors.importModal);
    await expect(modal).toBeVisible({ timeout: 5000 });

    // Upload valid CSV with retry on transient API failures
    await uploadCSVAndWaitForPreview(appPage, 'name\nImport Test Channel');

    const submitButton = appPage.locator(csvSelectors.importSubmitButton);
    await submitButton.click();

    // Should show success message (allow time for API import to complete)
    const successMessage = appPage.locator(csvSelectors.successMessage);
    await expect(successMessage).toBeVisible({ timeout: 60000 });
  });

  test('import modal shows channel count summary', async ({ appPage }) => {
    await enterEditMode(appPage);

    const importButton = appPage.locator(csvSelectors.importButton);
    await importButton.click();

    // Upload valid CSV with multiple channels, with retry on transient API failures
    await uploadCSVAndWaitForPreview(appPage, 'name,group_name\nChannel 1,Group A\nChannel 2,Group A\nChannel 3,Group B');

    // Should show count of channels to be imported
    const modal = appPage.locator(csvSelectors.importModal);
    const modalText = await modal.textContent();
    expect(modalText).toMatch(/3|channels/i);
  });
});

test.describe('CSV Import with Edit Mode', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'channel-manager');
  });

  test('import button is hidden when not in edit mode', async ({ appPage }) => {
    // Don't enter edit mode
    const importButton = appPage.locator(csvSelectors.importButton);

    // Button should either not exist or not be visible
    const isVisible = await importButton.isVisible().catch(() => false);
    expect(isVisible).toBe(false);
  });

  test('import button appears when entering edit mode', async ({ appPage }) => {
    // Initially not visible
    let importButton = appPage.locator(csvSelectors.importButton);
    let isVisible = await importButton.isVisible().catch(() => false);
    expect(isVisible).toBe(false);

    // Enter edit mode
    await enterEditMode(appPage);

    // Now should be visible
    importButton = appPage.locator(csvSelectors.importButton);
    await expect(importButton).toBeVisible({ timeout: 5000 });
  });
});
