import { defineConfig } from '@playwright/test'
import { fileURLToPath } from 'node:url'

const frontendRoot = fileURLToPath(new URL('.', import.meta.url))
const webPort = Number(process.env.PLAYWRIGHT_WEB_PORT ?? '5173')
const baseURL = `http://127.0.0.1:${webPort}`

export default defineConfig({
  testDir: './tests/e2e',
  testMatch: '**/*.e2e.ts',
  timeout: 30_000,
  use: {
    baseURL,
  },
  webServer: {
    command: `npm run dev -- --host 127.0.0.1 --port ${webPort}`,
    cwd: frontendRoot,
    url: baseURL,
    reuseExistingServer: false,
    env: process.env,
  },
})
