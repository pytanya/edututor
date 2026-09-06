import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  optimizeDeps: { include: ['katex'] },
  server: {
    proxy: {
      '/chat': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/student': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/knowledge': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/health': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.js'],
    css: false,
    exclude: ['e2e/**', 'node_modules/**', 'dist/**'],
  },
})
