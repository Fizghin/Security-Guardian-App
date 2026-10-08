import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In development the dashboard runs on :2500 and forwards API and video traffic to the
// backend, so the browser only ever talks to one origin (same as in production, where
// the backend serves the built dashboard itself).
const backend = process.env.GUARDIAN_BACKEND ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 2500,
    proxy: {
      '/api': { target: backend, changeOrigin: true },
      '/ws': { target: backend.replace(/^http/, 'ws'), ws: true },
    },
  },
})
