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
  await expect(page.getByRole('heading', { name: /让家的每一处/ })).toBeVisible()
  await page.getByRole('button', { name: '向小智发起智能诊断' }).click()
  await expect(page.getByRole('dialog', { name: '登录账号' })).toBeVisible()
  await page.getByRole('button', { name: '还没有账号？立即注册' }).click()
  await page.getByLabel('账号').fill(username)
  await page.getByLabel('昵称').fill('E2E 用户')
  await page.getByLabel('密码').fill('safe-password-123')
  await page.getByRole('button', { name: '注册并登录' }).click()
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
