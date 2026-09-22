import { expect, test } from '@playwright/test'

/**
 * 背景插画的运行期入口：选图 → 渲染 → 刷新后仍在 → 清除。
 *
 * 这条链路横跨三处，每一处都可能单独坏掉：设置面板（选文件）、`settings/lib/image`（缩放
 * 与编码）、界面域（持久化）与 `ui/glass/Backdrop`（渲染）。所以断言按**行为**走，不看
 * 内部状态：真塞一个文件、真刷新、真清除。
 *
 * 需要 AVID_E2E=1 且内核已起（脚本模型即可）。
 */
test.skip(!process.env.AVID_E2E, '需要 AVID_E2E=1 且内核已启动')

const BASE = process.env.AVID_BASE_URL ?? 'http://127.0.0.1:8765'
/** 1×1 的 PNG：够验证整条链路，又不让用例依赖任何外部素材。 */
const PIXEL_PNG = Buffer.from(
  'iVBORw0KGgoAAAANSUhEUgAAAAgAAAAICAIAAABLbSncAAAAGklEQVR4nGO0SVnAAAMLNObA2UwMOMDglAAAAO4CtGy9oY4AAAAASUVORK5CYII=',
  'base64',
)

const ART = '.app-backdrop-art'

test('选一张图当背景：渲染出来、刷新后仍在、清除后消失', async ({ page }) => {
  await page.goto(`${BASE}/settings`)
  const picker = page.locator('#settings-backdrop')
  await expect(picker).toBeVisible()

  // 一开始没有插画层（默认是纯 CSS 光斑）。
  await expect(page.locator(ART)).toHaveCount(0)

  await picker.setInputFiles({ name: 'pixel.png', mimeType: 'image/png', buffer: PIXEL_PNG })

  // 渲染：插画层出现，且它的背景是编好的 data URL（不是原图直接塞进去）。
  const art = page.locator(ART)
  await expect(art).toHaveCount(1)
  await expect
    .poll(async () => art.evaluate((element) => getComputedStyle(element).backgroundImage), {
      timeout: 5_000,
    })
    .toContain('data:image/jpeg')

  // 无障碍：它是纯装饰，不进无障碍树、不吃点击。
  expect(await art.getAttribute('aria-hidden'), '装饰层不进无障碍树').toBe('true')
  expect(await art.evaluate((el) => getComputedStyle(el).pointerEvents)).toBe('none')

  // 持久化：刷新后还在（这是界面域的偏好，不是服务端状态）。
  await page.reload()
  await expect(page.locator(ART)).toHaveCount(1)

  // 清除：按钮在选中图之后才出现，点掉之后再刷新也不回来。
  await page.getByRole('button', { name: '清除背景插画' }).click()
  await expect(page.locator(ART)).toHaveCount(0)
  await page.reload()
  await expect(page.locator(ART)).toHaveCount(0)
})

test('选一个非图片文件：如实报错，不留下半个背景', async ({ page }) => {
  await page.goto(`${BASE}/settings`)
  await page
    .locator('#settings-backdrop')
    .setInputFiles({ name: 'not-an-image.txt', mimeType: 'text/plain', buffer: Buffer.from('hi') })

  await expect(page.getByText('这不是图片文件。')).toBeVisible()
  await expect(page.locator(ART)).toHaveCount(0)
})
