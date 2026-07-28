import { expect, test } from '@playwright/test'

test('submits a message through the real Docker API stack', async ({ page }) => {
  const sessionResponse = page.waitForResponse(
    response => response.url().endsWith('/api/v1/sessions') && response.request().method() === 'POST',
  )
  const streamResponse = page.waitForResponse(
    response => response.url().endsWith('/api/v1/chat/stream') && response.request().method() === 'POST',
  )

  await page.goto('/')
  await page.locator('textarea').fill('verify browser-to-api integration')
  await page.locator('form.composer .send').click()

  await expect(page.locator('.answer-card')).toContainText(
    'Deterministic test response: verify browser-to-api integration',
  )
  const [session, stream] = await Promise.all([sessionResponse, streamResponse])
  expect(session.status()).toBe(201)
  expect(stream.status()).toBe(200)
})
