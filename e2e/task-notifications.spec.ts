/** Isolated rendered test of the task run-response / notification-consumer seam. */
import { test, exerciseTaskNotifications } from './fixtures/task-notifications'

test.describe('Task Notification Settings', () => {
  test('show_notifications unchecked prevents notifications in bell icon', async ({ privateTask }) => {
    await exerciseTaskNotifications(privateTask, false)
  })

  test('show_notifications checked allows notifications in bell icon', async ({ privateTask }) => {
    await exerciseTaskNotifications(privateTask, true)
  })
})
