import { expect, test } from '@playwright/test'
import type { Locator, Page } from '@playwright/test'

/**
 * 消息卡片回归：模型回复与用户消息是**同一族对话框**（墨框 + 手绘形状 + `--sticker-4`
 * 硬阴影），差别只在方向与角色标记；模型卡片带一枚手绘小标记（装饰性，不进无障碍树）；
 * 形状按条目序号轮换、相邻卡片不同形；只声明工具调用的空回合退化成 chip。
 *
 * 需要 AVID_E2E=1 且内核已起（脚本模型即可）。
 */
test.skip(!process.env.AVID_E2E, '需要 AVID_E2E=1 且内核已启动')

const BASE = process.env.AVID_BASE_URL ?? 'http://127.0.0.1:8765'
const INK_BORDER = '4px'
const STICKER_4 = '4px 4px 0px 0px'
const CARD_BG = 'rgb(255, 255, 255)'

interface CardStyle {
  borderWidth: string
  borderColor: string
  borderRadius: string
  backgroundColor: string
  boxShadow: string
  marginLeft: string
  marginRight: string
}

async function styleOf(locator: Locator): Promise<CardStyle> {
  return locator.evaluate((element) => {
    const style = getComputedStyle(element)
    return {
      borderWidth: style.borderTopWidth,
      borderColor: style.borderColor,
      borderRadius: style.borderTopLeftRadius,
      backgroundColor: style.backgroundColor,
      boxShadow: style.boxShadow,
      marginLeft: style.marginLeft,
      marginRight: style.marginRight,
    }
  })
}

async function openSession(page: Page): Promise<void> {
  const listed = await page.request.get(`${BASE}/api/sessions`)
  const sessions: { id: string; message_count: number }[] = (await listed.json()).sessions
  const target = sessions.find((item) => item.message_count > 0)
  expect(target, '需要一个含工具调用的会话').toBeTruthy()
  await page.goto(`${BASE}/sessions/${target?.id}`)
  await expect(page.getByLabel('输入指令，Enter 发送，Shift+Enter 换行')).toBeVisible()
  await expect
    .poll(async () => (await page.getByRole('log').innerText()).length, { timeout: 10_000 })
    .toBeGreaterThan(50)
}

/**
 * 定位消息卡片必须用**精确**角色名：`hasText` 不区分大小写，而用户消息里带着
 * 工作区路径 `/home/fishy/Avid`，用 `hasText: 'Avid'` 会把用户卡也匹配进来。
 */
function cardWithRole(page: Page, role: string, variant = 'sketch-card'): Locator {
  return page
    .getByRole('log')
    .locator(`article.${variant}`)
    .filter({ has: page.getByText(role, { exact: true }) })
}

function userCards(page: Page): Locator {
  return cardWithRole(page, '用户')
}

function assistantCards(page: Page): Locator {
  return cardWithRole(page, 'Avid')
}

function assistantChips(page: Page): Locator {
  return cardWithRole(page, 'Avid', 'sketch-chip')
}

/** 窗口化默认只渲染尾部一组：要看全量条目先展开。 */
async function expandAll(page: Page): Promise<void> {
  const earlier = page.getByRole('button', { name: '加载更早' })
  for (let guard = 0; guard < 20 && (await earlier.count()) > 0; guard += 1) {
    // dispatchEvent 而不是 click()：click() 先滚进视口 → scrollTop 归零 → 触发碰顶
    // 自动加载 → 高度补偿把按钮推走 → 再滚再加载，按钮最后消失导致点击超时。
    await earlier.first().dispatchEvent('click')
    await page.waitForTimeout(120)
  }
}

test('模型回复与用户消息是同一族对话框（同墨框、同硬阴影、方向相反）', async ({ page }) => {
  await openSession(page)

  const user = userCards(page).first()
  const model = assistantCards(page).first()
  await expect(user).toBeVisible()
  await expect(model).toBeVisible()

  const userStyle = await styleOf(user)
  const modelStyle = await styleOf(model)

  for (const style of [userStyle, modelStyle]) {
    expect(style.borderWidth, '墨框 4px（--stroke-bold）').toBe(INK_BORDER)
    expect(style.borderColor, '边框用墨色 token').toBe('rgb(26, 26, 26)')
    expect(style.backgroundColor, '底色用纸卡 token').toBe(CARD_BG)
    expect(style.boxShadow, '高度取 --sticker-4 档').toContain(STICKER_4)
  }

  // 方向：用户靠右（ml-auto），模型靠左（mr-auto）
  expect(userStyle.marginLeft, '用户消息靠右').not.toBe('0px')
  expect(modelStyle.marginRight, '模型回复靠左').not.toBe('0px')
})

test('模型卡片带手绘小标记，且它是装饰性的', async ({ page }) => {
  await openSession(page)
  const model = assistantCards(page).first()
  await expect(model).toBeVisible()

  const mark = model.locator('svg')
  await expect(mark).toHaveCount(1)
  expect(await mark.getAttribute('aria-hidden'), '装饰不进无障碍树').toBe('true')

  // 标记用 currentColor（父级 text-ink）→ 换主题跟着走；笔画粗、非缩放描边
  const stroke = await mark.locator('path').first().evaluate((path) => ({
    stroke: path.getAttribute('stroke'),
    width: path.getAttribute('stroke-width'),
    effect: path.getAttribute('vector-effect'),
  }))
  expect(stroke.stroke, '颜色来自 currentColor 而不是写死').toBe('currentColor')
  expect(stroke.width, '粗笔画（涂鸦感）').toBe('4.5')
  expect(stroke.effect, '描边不随缩放变细').toBe('non-scaling-stroke')
})

test('用户卡片也带手绘标记，且与模型卡片的标记不同形', async ({ page }) => {
  await openSession(page)
  const user = userCards(page).first()
  const model = assistantCards(page).first()
  await expect(user).toBeVisible()
  await expect(model).toBeVisible()

  const userMark = user.locator('svg')
  await expect(userMark, '用户卡也有一枚标记').toHaveCount(1)
  expect(await userMark.getAttribute('aria-hidden'), '装饰不进无障碍树').toBe('true')

  const pathsOf = (locator: Locator) =>
    locator.locator('path').evaluateAll((paths) => paths.map((path) => path.getAttribute('d')))
  const userPaths = await pathsOf(userMark)
  const modelPaths = await pathsOf(model.locator('svg'))
  expect(userPaths.length, '用户标记是两条笔画').toBe(2)
  expect(userPaths, '两枚标记必须不同形，否则等于没标作者').not.toEqual(modelPaths)

  // 形状不同，但笔触语言必须一致（同一套涂鸦契约，不是两套风格）
  const stroke = await userMark.locator('path').first().evaluate((path) => ({
    stroke: path.getAttribute('stroke'),
    width: path.getAttribute('stroke-width'),
    effect: path.getAttribute('vector-effect'),
  }))
  expect(stroke.stroke, '颜色来自 currentColor').toBe('currentColor')
  expect(stroke.width, '粗笔画（涂鸦感）').toBe('4.5')
  expect(stroke.effect, '描边不随缩放变细').toBe('non-scaling-stroke')

  // 角色名仍由文字承担：图标 aria-hidden，所以读屏器只念一次「用户」
  await expect(user.getByText('用户', { exact: true })).toBeVisible()
})

test('角色名与图标同在一行，accessible name 由文字承担', async ({ page }) => {
  await openSession(page)
  const model = assistantCards(page).first()
  await expect(model.getByText('Avid')).toBeVisible()
  // 图标 aria-hidden + 文字标签 → 读屏器只念一次角色名
  await expect(model.locator('[aria-hidden="true"] svg, svg[aria-hidden="true"]')).toHaveCount(1)
})

test('形状按条目轮换：相邻消息卡片不同形', async ({ page }) => {
  await openSession(page)
  const cards = page.getByRole('log').locator('article.sketch-card')
  // 消息卡片才是轮换对象：这里包含用户卡与模型卡（同级卡片）
  const count = await cards.count()
  expect(count, '至少要有两张消息卡片才能测轮换').toBeGreaterThan(1)

  const radii: string[] = []
  for (let index = 0; index < Math.min(count, 6); index += 1) {
    radii.push((await styleOf(cards.nth(index))).borderRadius)
  }
  for (let index = 1; index < radii.length; index += 1) {
    expect(radii[index], `第 ${index} 张与上一张形状相同（禁止连续同形）`).not.toBe(
      radii[index - 1],
    )
  }
  expect(new Set(radii).size, '形状确实在三种之间轮换').toBeGreaterThan(1)
})

test('只声明工具调用的空回合退化成 chip，不撑一张空卡', async ({ page }) => {
  await openSession(page)
  await expandAll(page)
  expect(await assistantChips(page).count(), '空回合用 chip 标记').toBeGreaterThan(0)
  expect(await assistantCards(page).count(), '有正文的回复用整张卡').toBeGreaterThan(0)
})

test('durable 的模型回复才挂 aria-live', async ({ page }) => {
  await openSession(page)
  const model = assistantCards(page).first()
  await expect(model).toHaveAttribute('aria-live', 'polite')
  // 空回合的 chip 不播报（它没有正文）
  await expandAll(page)
  expect(await assistantChips(page).first().getAttribute('aria-live')).toBeNull()
})
