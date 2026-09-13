import { expect, test } from '@playwright/test'

async function conversationScrollState(page: import('@playwright/test').Page) {
  return page.evaluate(() => {
    const stage = document.querySelector<HTMLElement>('#agent-desk')
    if (!stage) throw new Error('conversation stage is missing')
    const maximum = Math.max(0, stage.scrollHeight - stage.clientHeight)
    return {
      windowY: window.scrollY,
      stageTop: stage.scrollTop,
      maximum,
      atBottom: Math.abs(maximum - stage.scrollTop) <= 2,
    }
  })
}

test('submits a message through the real Docker API stack', async ({ page }) => {
  const phone = `138${String(Date.now()).slice(-8)}`
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
  const registerDialog = page.getByRole('dialog', { name: '创建账号' })
  await registerDialog.getByLabel('手机号').fill(phone)
  await registerDialog.getByLabel('昵称').fill('E2E 用户')
  await registerDialog.getByRole('button', { name: '获取验证码' }).click()
  await registerDialog.getByLabel(/短信验证码/).fill('123456')
  await registerDialog.getByLabel('密码').fill('safe-password-123')
  await registerDialog.getByRole('checkbox').nth(0).check()
  await registerDialog.getByRole('checkbox').nth(1).check()
  await registerDialog.getByRole('button', { name: '注册并登录' }).click()
  await expect(page.getByRole('button', { name: 'E2E 用户，打开个人资料' })).toBeVisible()
  await expect(page.getByLabel('聊天工作区')).toBeVisible()
  await page.locator('textarea').fill('verify browser-to-api integration')
  await page.locator('form.composer .send').click()

  await expect(page.locator('.answer-card')).toContainText(
    'Deterministic test response: verify browser-to-api integration',
  )
  await expect.poll(async () => (await conversationScrollState(page)).atBottom).toBe(true)
  expect((await conversationScrollState(page)).windowY).toBe(0)
  const [session, stream] = await Promise.all([sessionResponse, streamResponse])
  expect(session.status()).toBe(201)
  expect(stream.status()).toBe(200)

  // Reloading and re-entering the desk must restore the persisted transcript
  // and place only the conversation viewport at its latest message.
  await page.reload()
  await page.getByRole('button', { name: /小智问答/ }).last().click()
  await expect(page.getByText('Deterministic test response: verify browser-to-api integration')).toBeVisible()
  await expect.poll(async () => (await conversationScrollState(page)).atBottom).toBe(true)
  expect((await conversationScrollState(page)).windowY).toBe(0)

  const secondStreamResponse = page.waitForResponse(
    response => response.url().endsWith('/api/v1/chat/stream') && response.request().method() === 'POST',
  )
  await page.locator('textarea').fill('继续检查第二轮滚动')
  await page.locator('form.composer .send').click()
  await expect(page.locator('.answer-card').last()).toContainText(
    'Deterministic test response: 继续检查第二轮滚动',
  )
  expect((await secondStreamResponse).status()).toBe(200)
  await expect.poll(async () => (await conversationScrollState(page)).atBottom).toBe(true)
  expect((await conversationScrollState(page)).windowY).toBe(0)
})
