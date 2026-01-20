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
        // Start with modest thresholds, increase as coverage improves
        lines: 20,
        branches: 20,
        functions: 20,
        statements: 20,
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
