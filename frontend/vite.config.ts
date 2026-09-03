import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    // Explicit IPv4 loopback: on macOS "localhost" resolves to ::1 and Vite
    // would bind IPv6-only, refusing 127.0.0.1 (and the IPv4-only backend).
    host: '127.0.0.1',
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:3000',
        changeOrigin: true,
        ws: true,
      },
      '/ws': {
        target: 'ws://127.0.0.1:3000',
        ws: true,
      },
      '/registry': {
        target: 'http://127.0.0.1:3010',
        changeOrigin: true,
      },
    },
  },
})
