/**
 * Test setup file - runs before each test file.
 *
 * Configures:
 * - jest-dom matchers for DOM assertions
 * - Cleanup after each test
 * - Global test utilities
 * - MSW server setup (when handlers are defined)
 */
import '@testing-library/jest-dom'
import { cleanup } from '@testing-library/react'
import { afterEach, beforeEach, vi } from 'vitest'

// Cleanup after each test case (e.g. clearing jsdom)
afterEach(() => {
  cleanup()
})

// Ensure navigator.clipboard is writable before every test.
// jsdom / vitest may (re-)define it as a getter-only accessor; this beforeEach
// walks the prototype chain and forces a writable data property.
function ensureClipboardWritable() {
  let obj: object | null = navigator;
  while (obj) {
    const desc = Object.getOwnPropertyDescriptor(obj, 'clipboard');
    if (desc && (desc.get || (!desc.writable && !desc.set))) {
      Object.defineProperty(obj, 'clipboard', {
        value: {
          writeText: vi.fn().mockResolvedValue(undefined),
          readText: vi.fn().mockResolvedValue(''),
        },
        writable: true,
        configurable: true,
      });
    }
    obj = Object.getPrototypeOf(obj);
  }
  // Guarantee an own writable property on navigator itself
  if (!Object.getOwnPropertyDescriptor(navigator, 'clipboard')?.writable) {
    Object.defineProperty(navigator, 'clipboard', {
      value: {
        writeText: vi.fn().mockResolvedValue(undefined),
        readText: vi.fn().mockResolvedValue(''),
      },
      writable: true,
      configurable: true,
    });
  }
}
beforeEach(() => {
  ensureClipboardWritable();
})

// Mock window.matchMedia (used by responsive components)
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(), // deprecated
    removeListener: vi.fn(), // deprecated
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
})

// Mock ResizeObserver (used by some UI components)
class ResizeObserverMock {
  observe = vi.fn()
  unobserve = vi.fn()
  disconnect = vi.fn()
}
window.ResizeObserver = ResizeObserverMock

// Mock IntersectionObserver (used by lazy loading, infinite scroll)
class IntersectionObserverMock {
  readonly root: Element | null = null
  readonly rootMargin: string = ''
  readonly thresholds: ReadonlyArray<number> = []
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  constructor(_callback: IntersectionObserverCallback) {}

  observe = vi.fn()
  unobserve = vi.fn()
  disconnect = vi.fn()
  takeRecords = vi.fn().mockReturnValue([])
}
window.IntersectionObserver = IntersectionObserverMock as unknown as typeof IntersectionObserver

// Run once at file scope too (covers beforeAll blocks that run before beforeEach)
ensureClipboardWritable();

// Mock scrollTo (JSDOM doesn't implement it)
Element.prototype.scrollTo = vi.fn() as unknown as typeof Element.prototype.scrollTo
window.scrollTo = vi.fn() as unknown as typeof window.scrollTo

// Mock fetch if not using MSW (basic mock, MSW preferred for API mocking)
// Uncomment if needed:
// global.fetch = vi.fn()

// Suppress console.error for expected errors during tests
// Uncomment to reduce test noise:
// const originalError = console.error
// beforeAll(() => {
//   console.error = (...args: unknown[]) => {
//     if (
//       typeof args[0] === 'string' &&
//       args[0].includes('Warning: ReactDOM.render is no longer supported')
//     ) {
//       return
//     }
//     originalError.call(console, ...args)
//   }
// })
// afterAll(() => {
//   console.error = originalError
// })

// Set up global test timeout (optional)
// vi.setConfig({ testTimeout: 10000 })
