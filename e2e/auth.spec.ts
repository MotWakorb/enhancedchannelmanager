/**
 * E2E tests for Authentication flows.
 *
 * TDD SPEC: These tests define expected auth E2E behavior.
 * They will FAIL initially - implementation makes them pass.
 *
 * Test Spec: E2E Auth Flows (v6dxf.8.14)
 */
import { test, expect } from '@playwright/test';

test.describe('First Run Setup', () => {
  test('fresh install shows setup wizard', async ({ page }) => {
    // Assuming fresh database with no users
    await page.goto('/');

    // Should redirect to setup page
    await expect(page).toHaveURL(/.*setup/);
    await expect(page.getByText(/Welcome|Setup|Create Admin/i)).toBeVisible();
  });

  test('create admin user completes setup', async ({ page }) => {
    await page.goto('/setup');

    // Fill in admin creation form
    await page.getByLabel(/username/i).fill('admin');
    await page.getByLabel(/email/i).fill('admin@example.com');
    await page.getByLabel(/password/i).first().fill('SecureAdmin123!');
    await page.getByLabel(/confirm/i).fill('SecureAdmin123!');

    // Submit form
    await page.getByRole('button', { name: /create|submit|continue/i }).click();

    // Should redirect to login or dashboard
    await expect(page).toHaveURL(/\/(login|dashboard)?$/);
  });

  test('login works after setup', async ({ page }) => {
    // After setup, login should work
    await page.goto('/login');

    await page.getByLabel(/username/i).fill('admin');
    await page.getByLabel(/password/i).fill('SecureAdmin123!');
    await page.getByRole('button', { name: /login|sign in/i }).click();

    // Should be redirected to main app
    await expect(page).not.toHaveURL(/login/);
  });
});

test.describe('Local Login', () => {
  test('login page loads with form', async ({ page }) => {
    await page.goto('/login');

    await expect(page.getByLabel(/username/i)).toBeVisible();
    await expect(page.getByLabel(/password/i)).toBeVisible();
    await expect(page.getByRole('button', { name: /login|sign in/i })).toBeVisible();
  });

  test('valid credentials redirects to app', async ({ page }) => {
    await page.goto('/login');

    await page.getByLabel(/username/i).fill('validuser');
    await page.getByLabel(/password/i).fill('ValidPassword123!');
    await page.getByRole('button', { name: /login|sign in/i }).click();

    // Wait for redirect
    await page.waitForURL(/\/(dashboard|channels)?$/);
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

  test('logout redirects to login', async ({ page }) => {
    // First login
    await page.goto('/login');
    await page.getByLabel(/username/i).fill('validuser');
    await page.getByLabel(/password/i).fill('ValidPassword123!');
    await page.getByRole('button', { name: /login|sign in/i }).click();

    // Wait for app to load
    await page.waitForURL(/\/(dashboard|channels)?$/);

    // Find and click logout
    await page.getByRole('button', { name: /logout|sign out/i }).click();

    // Should redirect to login
    await expect(page).toHaveURL(/login/);
  });
});

test.describe('Session Management', () => {
  test('refresh page maintains session', async ({ page }) => {
    // Login first
    await page.goto('/login');
    await page.getByLabel(/username/i).fill('validuser');
    await page.getByLabel(/password/i).fill('ValidPassword123!');
    await page.getByRole('button', { name: /login|sign in/i }).click();

    await page.waitForURL(/\/(dashboard|channels)?$/);

    // Refresh the page
    await page.reload();

    // Should still be authenticated
    await expect(page).not.toHaveURL(/login/);
    // User indicator should be visible
    await expect(page.getByText(/validuser/i)).toBeVisible();
  });

  test('session expires after timeout', async ({ page }) => {
    // This test would require mocking time or waiting for actual timeout
    // For now, we test the behavior when session is invalid

    // Set an expired/invalid session cookie
    await page.context().addCookies([
      {
        name: 'access_token',
        value: 'expired.invalid.token',
        domain: 'localhost',
        path: '/',
      },
    ]);

    await page.goto('/channels');

    // Should redirect to login
    await expect(page).toHaveURL(/login/);
  });

  test('expired session redirects to login', async ({ page }) => {
    // Attempt to access protected page with no session
    await page.goto('/channels');

    // Should redirect to login
    await expect(page).toHaveURL(/login/);
  });
});

test.describe('Admin Functions', () => {
  test.beforeEach(async ({ page }) => {
    // Login as admin
    await page.goto('/login');
    await page.getByLabel(/username/i).fill('admin');
    await page.getByLabel(/password/i).fill('AdminPassword123!');
    await page.getByRole('button', { name: /login|sign in/i }).click();
    await page.waitForURL(/\/(dashboard|channels)?$/);
  });

  test('admin can access user management', async ({ page }) => {
    // Navigate to admin/users
    await page.goto('/admin/users');

    // Should see user list
    await expect(page.getByText(/users|user management/i)).toBeVisible();
    await expect(page).not.toHaveURL(/forbidden|403/);
  });

  test('admin can create users', async ({ page }) => {
    await page.goto('/admin/users');

    // Click create button
    await page.getByRole('button', { name: /create|add|new/i }).click();

    // Fill form
    await page.getByLabel(/username/i).fill('newuser');
    await page.getByLabel(/email/i).fill('newuser@example.com');
    await page.getByLabel(/password/i).first().fill('NewUserPass123!');

    // Submit
    await page.getByRole('button', { name: /create|save|submit/i }).click();

    // Should see success message or user in list
    await expect(page.getByText(/created|success|newuser/i)).toBeVisible();
  });

  test('admin can manage roles and groups', async ({ page }) => {
    await page.goto('/admin/roles');

    // Should see roles list
    await expect(page.getByText(/roles|permissions/i)).toBeVisible();

    // Navigate to groups
    await page.goto('/admin/groups');
    await expect(page.getByText(/groups/i)).toBeVisible();
  });

  test('non-admin cannot access admin pages', async ({ page }) => {
    // Logout admin
    await page.getByRole('button', { name: /logout|sign out/i }).click();

    // Login as regular user
    await page.getByLabel(/username/i).fill('regularuser');
    await page.getByLabel(/password/i).fill('RegularPass123!');
    await page.getByRole('button', { name: /login|sign in/i }).click();

    // Try to access admin page
    await page.goto('/admin/users');

    // Should be forbidden or redirected
    const url = page.url();
    const text = await page.textContent('body');
    expect(
      url.includes('forbidden') ||
        url.includes('403') ||
        text?.toLowerCase().includes('forbidden') ||
        text?.toLowerCase().includes('permission') ||
        !url.includes('/admin/')
    ).toBe(true);
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
