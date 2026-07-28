import { expect, test } from '@playwright/test'

test('submits a message through the real Docker API stack', async ({ page }) => {
  const username = `e2e_${Date.now()}`
  const sessionResponse = page.waitForResponse(
    response => response.url().endsWith('/api/v1/sessions') && response.request().method() === 'POST',
  )
  const streamResponse = page.waitForResponse(
    response => response.url().endsWith('/api/v1/chat/stream') && response.request().method() === 'POST',
  )

  await page.goto('/')
  await page.getByRole('button', { name: '没有账号？创建一个' }).click()
  await page.getByLabel('账号').fill(username)
  await page.getByLabel('昵称').fill('E2E 用户')
  await page.getByLabel('密码').fill('safe-password-123')
  await page.getByRole('button', { name: '注册并进入' }).click()
  await expect(page.getByLabel('聊天工作区').getByText('E2E 用户', { exact: true })).toBeVisible()
  await page.locator('textarea').fill('verify browser-to-api integration')
  await page.locator('form.composer .send').click()

  await expect(page.locator('.answer-card')).toContainText(
    'Deterministic test response: verify browser-to-api integration',
  )
  const [session, stream] = await Promise.all([sessionResponse, streamResponse])
  expect(session.status()).toBe(201)
  expect(stream.status()).toBe(200)
})
