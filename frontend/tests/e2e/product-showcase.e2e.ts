import { expect, test } from '@playwright/test'
import type { Locator } from '@playwright/test'

const user = {
  id: 'showcase-user',
  username: 'showcase_user',
  nickname: '展示用户',
  avatar_url: '',
  role: 'user',
  created_at: '2026-08-16T00:00:00Z',
}

function envelope(data: unknown) {
  return { code: 'OK', message: 'success', data, request_id: 'showcase-e2e' }
}

async function readWebglPixels(canvas: Locator) {
  return canvas.evaluate((element: HTMLCanvasElement) => {
    const gl = element.getContext('webgl2') || element.getContext('webgl')
    if (!gl) return { colored: 0, total: 0 }
    const width = gl.drawingBufferWidth
    const height = gl.drawingBufferHeight
    const pixels = new Uint8Array(width * height * 4)
    gl.readPixels(0, 0, width, height, gl.RGBA, gl.UNSIGNED_BYTE, pixels)
    let colored = 0
    for (let index = 0; index < pixels.length; index += 16) {
      const r = pixels[index]
      const g = pixels[index + 1]
      const b = pixels[index + 2]
      if (Math.max(r, g, b) - Math.min(r, g, b) > 10 || r < 205 || g < 195 || b < 185) colored += 1
    }
    return { colored, total: pixels.length / 16 }
  })
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript((authUser) => {
    localStorage.setItem('agent.access-token', 'showcase-token')
    localStorage.setItem('agent.access-token-expires-at', String(Date.now() + 60 * 60 * 1000))
    localStorage.setItem('agent.auth-user', JSON.stringify(authUser))
  }, user)

  await page.route('**/api/v1/**', async (route) => {
    const url = new URL(route.request().url())
    if (url.pathname.endsWith('/auth/me')) {
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(envelope(user)) })
      return
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(envelope({ items: [], next_cursor: null })),
    })
  })
})

test('product hover frames the robot and the 3D view renders real pixels', async ({ page }) => {
  await page.setViewportSize({ width: 1920, height: 1080 })
  const pageErrors: string[] = []
  const consoleErrors: string[] = []
  page.on('pageerror', error => pageErrors.push(error.message))
  page.on('console', message => {
    if (message.type() === 'error') consoleErrors.push(message.text())
  })

  await page.goto('/')
  const card = page.locator('.robot-lineup__card').filter({ hasText: 'S8 皓月' })
  await card.scrollIntoViewIfNeeded()
  await card.hover()

  const imageBox = await card.locator('.robot-lineup__image').boundingBox()
  const frame = card.locator('.robot-lineup__focus-frame')
  const frameLabel = frame.getByText('S8 皓月', { exact: true })
  await expect(frameLabel).toBeVisible()
  const frameBox = await frame.boundingBox()
  const labelBox = await frameLabel.boundingBox()
  expect(imageBox).not.toBeNull()
  expect(frameBox).not.toBeNull()
  expect(labelBox).not.toBeNull()
  expect(frameBox!.width).toBeGreaterThan(imageBox!.width * 0.72)
  expect(frameBox!.height).toBeGreaterThan(imageBox!.height * 0.62)
  expect(labelBox!.y).toBeGreaterThanOrEqual(imageBox!.y)
  expect(labelBox!.y + labelBox!.height).toBeLessThanOrEqual(imageBox!.y + imageBox!.height)
  await card.screenshot({ path: test.info().outputPath('s8-hover-frame.png') })

  const alpha = await page.evaluate(async () => {
    const response = await fetch(document.querySelector<HTMLImageElement>('.robot-lineup__card img[alt^="S8 皓月"]')!.src)
    const bitmap = await createImageBitmap(await response.blob())
    const canvas = document.createElement('canvas')
    canvas.width = bitmap.width
    canvas.height = bitmap.height
    const context = canvas.getContext('2d')!
    context.drawImage(bitmap, 0, 0)
    const corners = [[0, 0], [bitmap.width - 1, 0], [0, bitmap.height - 1], [bitmap.width - 1, bitmap.height - 1]]
    return corners.map(([x, y]) => context.getImageData(x, y, 1, 1).data[3])
  })
  expect(alpha).toEqual([0, 0, 0, 0])

  await card.click()
  const modelResponse = page.waitForResponse(response => response.url().endsWith('/models/s8-luna.glb'))
  await page.getByRole('tab', { name: '3D 效果' }).click()
  const response = await modelResponse
  expect(response.status()).toBe(200)
  expect(response.headers()['content-type']).not.toContain('text/html')
  expect(Number(response.headers()['content-length'] ?? 0)).toBeGreaterThan(1_000_000)

  const stage = page.getByRole('region', { name: 'S8 皓月 3D 效果' })
  await expect(stage).toHaveAttribute('data-render-state', 'ready', { timeout: 20_000 })
  await expect(stage.getByRole('button', { name: '重置 3D 视角' })).toBeVisible()

  const pixelStats = await readWebglPixels(stage.locator('canvas'))
  expect(pixelStats.total).toBeGreaterThan(1_000)
  expect(pixelStats.colored / pixelStats.total).toBeGreaterThan(0.015)
  await stage.screenshot({ path: test.info().outputPath('s8-3d-desktop.png') })
  expect(pageErrors).toEqual([])
  expect(consoleErrors).toEqual([])
})

test('3D view remains visible on mobile', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  const card = page.locator('.robot-lineup__card').filter({ hasText: 'S8 皓月' })
  await card.scrollIntoViewIfNeeded()
  await card.click()
  await page.getByRole('tab', { name: '3D 效果' }).click()

  const stage = page.getByRole('region', { name: 'S8 皓月 3D 效果' })
  await expect(stage).toHaveAttribute('data-render-state', 'ready', { timeout: 20_000 })
  const stageBox = await stage.boundingBox()
  expect(stageBox).not.toBeNull()
  expect(stageBox!.width).toBeLessThanOrEqual(362)
  const pixelStats = await readWebglPixels(stage.locator('canvas'))
  expect(pixelStats.colored / pixelStats.total).toBeGreaterThan(0.015)
  await stage.screenshot({ path: test.info().outputPath('s8-3d-mobile.png') })
})

test('failed GLB loading shows a product fallback instead of a blank panel', async ({ page }) => {
  await page.route('**/models/s8-luna.glb', route => route.fulfill({ status: 503, body: '' }))
  await page.goto('/')
  const card = page.locator('.robot-lineup__card').filter({ hasText: 'S8 皓月' })
  await card.scrollIntoViewIfNeeded()
  await card.click()
  await page.getByRole('tab', { name: '3D 效果' }).click()

  const stage = page.getByRole('region', { name: 'S8 皓月 3D 效果' })
  await expect(stage).toHaveAttribute('data-render-state', 'error')
  await expect(stage.getByRole('img', { name: 'S8 皓月 产品图' })).toBeVisible()
  await expect(stage.getByText('3D 模型暂时无法加载，已保留产品图作为预览。')).toBeVisible()
  await expect(stage.getByRole('button', { name: '重新加载' })).toBeVisible()
})

test('all six product models render nonblank previews', async ({ page }) => {
  test.setTimeout(90_000)
  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto('/')

  for (const productName of ['S8 皓月', 'S8 Air', 'X9 曜石', 'X9 Edge', 'M6 霞陶', 'M6 Mini']) {
    const card = page.locator('.robot-lineup__card').filter({ hasText: productName })
    await card.scrollIntoViewIfNeeded()
    await card.click()
    await page.getByRole('tab', { name: '3D 效果' }).click()
    const stage = page.getByRole('region', { name: `${productName} 3D 效果` })
    await expect(stage).toHaveAttribute('data-render-state', 'ready', { timeout: 20_000 })
    const pixelStats = await readWebglPixels(stage.locator('canvas'))
    expect(pixelStats.total, `${productName} should create a WebGL drawing buffer`).toBeGreaterThan(1_000)
    expect(pixelStats.colored / pixelStats.total, `${productName} should render visible model pixels`).toBeGreaterThan(0.015)
    await page.getByRole('button', { name: '关闭产品详情' }).click()
  }
})
