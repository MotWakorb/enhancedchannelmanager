/**
 * MSW server setup for Node.js (Vitest) environment.
 *
 * Usage in tests:
 * ```ts
 * import { server } from '../test/mocks/server'
 * import { mockDataStore, createMockChannel } from '../test/mocks/handlers'
 *
 * beforeAll(() => server.listen())
 * afterEach(() => server.resetHandlers())
 * afterAll(() => server.close())
 *
 * test('example', () => {
 *   mockDataStore.channels.push(createMockChannel({ name: 'ESPN' }))
 *   // Test code that makes API calls
 * })
 * ```
 */
import { setupServer } from 'msw/node'
import { handlers, resetMockDataStore } from './handlers'
import { ffmpegHandlers, resetFFMPEGMockDataStore } from './ffmpegHandlers'

// Create the server with default handlers + FFMPEG handlers
export const server = setupServer(...handlers, ...ffmpegHandlers)

// Re-export handlers for custom handler additions
export { handlers } from './handlers'

// Re-export utilities for test setup
export {
  resetMockDataStore,
  mockDataStore,
  createMockChannel,
  createMockChannelGroup,
  createMockStream,
  createMockScheduledTask,
  createMockAlertMethod,
  createMockNotification,
  createMockAutoCreationRule,
  createMockAutoCreationExecution,
  resetIdCounter,
} from './handlers'

// Re-export FFMPEG mock utilities
export {
  ffmpegHandlers,
  ffmpegMockDataStore,
  resetFFMPEGMockDataStore,
  createMockFFMPEGConfig,
  createMockFFMPEGJob,
} from './ffmpegHandlers'
