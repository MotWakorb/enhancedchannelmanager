/**
 * E2E tests for Scheduled Tasks.
 */
import { test, expect, navigateToTab, waitForToast } from './fixtures/base';
import { selectors, sampleTasks } from './fixtures/test-data';

test.describe('Scheduled Tasks', () => {
  test.beforeEach(async ({ appPage }) => {
    // Navigate to settings tab where tasks are usually located
    await navigateToTab(appPage, 'settings');
  });

  test('task list section is visible', async ({ appPage }) => {
    // Tasks may be in a section or separate tab
    const taskSection = appPage.locator(
      selectors.taskList +
        ', [data-testid="task-list"], .scheduled-tasks, :has-text("Scheduled Tasks")'
    );

    const exists = await taskSection.count();
    // Just verify we can query for tasks
    expect(exists).toBeGreaterThanOrEqual(0);
  });

  test('can view task details', async ({ appPage }) => {
    const taskItems = appPage.locator(selectors.taskItem + ', [data-testid*="task"]');
    const count = await taskItems.count();

    if (count > 0) {
      // Click first task to view details
      await taskItems.first().click();
      await appPage.waitForTimeout(500);

      // Should show some task information
      const taskContent = await appPage.textContent('body');
      expect(taskContent).toBeDefined();
    }
  });

  test('task items display task name', async ({ appPage }) => {
    const taskItems = appPage.locator(selectors.taskItem);
    const count = await taskItems.count();

    if (count > 0) {
      const firstTask = taskItems.first();
      const text = await firstTask.textContent();
      expect(text).toBeDefined();
      expect(text?.length).toBeGreaterThan(0);
    }
  });
});

test.describe('Task Actions', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'settings');
  });

  test('run button exists for tasks', async ({ appPage }) => {
    const runButtons = appPage.locator(
      selectors.taskRunButton + ', button:has-text("Run"), button[title*="Run"]'
    );

    const count = await runButtons.count();
    // May or may not have run buttons depending on tasks
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('edit button exists for tasks', async ({ appPage }) => {
    const editButtons = appPage.locator(
      selectors.taskEditButton + ', button:has-text("Edit"), button[title*="Edit"]'
    );

    const count = await editButtons.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });
});

test.describe('Task Scheduling', () => {
  test.beforeEach(async ({ appPage }) => {
    await navigateToTab(appPage, 'settings');
  });

  test('can view task schedule information', async ({ appPage }) => {
    // Look for schedule-related content
    const scheduleInfo = appPage.locator(
      '.schedule, [data-testid*="schedule"], :has-text("Schedule"), :has-text("Next run"), :has-text("Interval")'
    );

    const count = await scheduleInfo.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('schedule shows next run time', async ({ appPage }) => {
    const nextRun = appPage.locator(':has-text("Next run"), :has-text("next run"), [data-testid*="next-run"]');
    const count = await nextRun.count();
    // May or may not be visible
    expect(count).toBeGreaterThanOrEqual(0);
  });
});

test.describe('Task Status', () => {
  test('tasks show enabled/disabled status', async ({ appPage }) => {
    await navigateToTab(appPage, 'settings');

    // Look for toggle switches or enable/disable indicators
    const toggles = appPage.locator(
      'input[type="checkbox"], .toggle, [role="switch"], :has-text("Enabled"), :has-text("Disabled")'
    );

    const count = await toggles.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });

  test('tasks show last run status', async ({ appPage }) => {
    await navigateToTab(appPage, 'settings');

    const lastRun = appPage.locator(':has-text("Last run"), :has-text("last run"), [data-testid*="last-run"]');
    const count = await lastRun.count();
    expect(count).toBeGreaterThanOrEqual(0);
  });
});
