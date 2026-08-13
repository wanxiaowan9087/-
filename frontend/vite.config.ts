import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const backendProxyTarget = process.env.VITE_BACKEND_PROXY_TARGET ?? 'http://127.0.0.1:8001'

export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5174,
    proxy: {
      '/api': {
        target: backendProxyTarget,
        changeOrigin: true,
      },
      '/uploads': {
        target: backendProxyTarget,
        changeOrigin: true,
      },
    },
  },
})
