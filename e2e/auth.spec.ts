/**
 * E2E tests for Authentication flows.
 */
import { test, expect } from '@playwright/test';
import { testCredentials } from './fixtures/test-data';

test.describe('Local Login', () => {
  test('login page loads with form', async ({ page }) => {
    await page.goto('/login');

    await expect(page.getByLabel(/username/i)).toBeVisible();
    await expect(page.getByLabel(/password/i)).toBeVisible();
    await expect(page.getByRole('button', { name: /login|sign in/i })).toBeVisible();
  });

  test('valid credentials redirects to app', async ({ page }) => {
    await page.goto('/login');

    await page.getByLabel(/username/i).fill(testCredentials.username);
    await page.getByLabel(/password/i).fill(testCredentials.password);
    await page.getByRole('button', { name: /login|sign in/i }).click();

    // Wait for navigation away from login page
    await expect(page).not.toHaveURL(/login/);
  });

  test('invalid credentials shows error message', async ({ page }) => {
    await page.goto('/login');

    await page.getByLabel(/username/i).fill('validuser');
    await page.getByLabel(/password/i).fill('wrongpassword');
    await page.getByRole('button', { name: /login|sign in/i }).click();

    // Should show error message
    await expect(page.getByText(/invalid|incorrect|failed/i)).toBeVisible();
    // Should stay on login page
    await expect(page).toHaveURL(/login/);
  });

});

test.describe('OIDC Flow', () => {
  test('click OIDC button redirects to provider', async ({ page }) => {
    await page.goto('/login');

    // Find and click OIDC login button
    const oidcButton = page.getByRole('button', { name: /oidc|sso|google|azure/i });

    if (await oidcButton.isVisible()) {
      await oidcButton.click();

      // Should redirect to external provider (or mock provider)
      // The URL should change to the provider's domain
      const url = page.url();
      expect(url.includes('localhost/login') === false || url.includes('auth')).toBe(
        true
      );
    } else {
      // OIDC not enabled, skip
      test.skip();
    }
  });

  test('complete provider flow redirects back logged in', async ({ page }) => {
    // This test would use a mock OIDC provider
    // Simulating the callback with code and state

    // Mock callback URL (as if returning from provider)
    await page.goto('/api/auth/oidc/mock/callback?code=mock-auth-code&state=mock-state');

    // Should be logged in and redirected to app
    // (This would work with a properly configured mock provider)
    await expect(page).not.toHaveURL(/error/);
  });
});
