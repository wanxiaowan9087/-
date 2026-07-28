import { defineConfig } from '@playwright/test'
import { fileURLToPath } from 'node:url'

const frontendRoot = fileURLToPath(new URL('.', import.meta.url))

export default defineConfig({
  testDir: './tests/e2e',
  testMatch: '**/*.e2e.ts',
  timeout: 30_000,
  use: {
    baseURL: 'http://127.0.0.1:5173',
  },
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 5173',
    cwd: frontendRoot,
    url: 'http://127.0.0.1:5173',
    reuseExistingServer: false,
    env: process.env,
  },
})
