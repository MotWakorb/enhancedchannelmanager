/// <reference types="vitest" />
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    // Environment
    environment: 'jsdom',

    // Enable globals (describe, it, expect, vi) without importing
    globals: true,

    // Setup files run before each test file
    setupFiles: ['./src/test/setup.ts'],

    // Include patterns for test files
    include: ['src/**/*.{test,spec}.{js,mjs,cjs,ts,mts,cts,jsx,tsx}'],

    // Exclude patterns
    exclude: ['node_modules', 'dist', '.idea', '.git', '.cache'],

    // Coverage configuration
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html'],
      reportsDirectory: './coverage',
      include: ['src/**/*.{ts,tsx}'],
      exclude: [
        'src/**/*.{test,spec}.{ts,tsx}',
        'src/test/**',
        'src/main.tsx',
        'src/vite-env.d.ts',
      ],
      thresholds: {
        // Pragmatic baseline ratchet (bead enhancedchannelmanager-nmlxi, measured 2026-04-20).
        // Full-suite measurement on dev tip:
        //   statements 15.17%, branches 14.13%, functions 15.28%, lines 15.46%
        // Thresholds = measured − 2 (regression guard without instantly breaking CI).
        // Re-ratchet cadence + policy: docs/testing.md § "Coverage ratchet cadence".
        // DO NOT lower without PO approval.
        lines: 13,
        branches: 12,
        functions: 13,
        statements: 13,
      },
    },

    // Reporter configuration
    reporters: ['default'],

    // Watch mode options
    watchExclude: ['node_modules', 'dist'],

    // Clear mocks between tests
    clearMocks: true,
    restoreMocks: true,

    // CSS handling
    css: {
      modules: {
        classNameStrategy: 'non-scoped',
      },
    },
  },
})
