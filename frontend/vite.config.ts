import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Vite config. The dev server proxies /api to the FastAPI backend so the SPA
// and API share an origin in dev (no CORS needed); the production build emits
// static files the backend serves from frontend/dist.
export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5000,
    allowedHosts: true,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
})
