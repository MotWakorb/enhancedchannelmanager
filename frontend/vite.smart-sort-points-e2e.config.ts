import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const apiTarget = process.env.SMART_SORT_POINTS_E2E_API
const distDirectory = process.env.SMART_SORT_POINTS_E2E_DIST
const previewPort = Number(process.env.SMART_SORT_POINTS_E2E_PORT)

if (!apiTarget || !distDirectory || !Number.isInteger(previewPort)) {
  throw new Error('Smart Sort Points E2E harness environment is incomplete')
}

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: distDirectory,
    emptyOutDir: true,
  },
  preview: {
    host: '127.0.0.1',
    port: previewPort,
    strictPort: true,
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
})
