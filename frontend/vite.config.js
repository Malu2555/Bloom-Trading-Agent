import { fileURLToPath, URL } from 'node:url'

import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import vueDevTools from 'vite-plugin-vue-devtools'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    vue(),
    vueDevTools(),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    // Dev only: proxy /api to the Django backend so the frontend stays
    // same-origin and the HttpOnly session cookie (silent auth) flows without
    // CORS. Point VITE_API_URL / PROXY_TARGET elsewhere in production.
    proxy: {
      '/api': {
        target: process.env.PROXY_TARGET || 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
