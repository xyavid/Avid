import { expect, test } from '@playwright/test'
import type { Locator, Page } from '@playwright/test'

import { lifts, rgba, token } from './helpers'

/**
 * 消息卡片回归：模型回复与用户消息是**同一族玻璃卡**（同高光边 + 同圆角 + 同投影），
 * 差别只在方向与角色标记；两枚角色标记是 lucide 图标（`Bot` / `User`，装饰性、不进
 * 无障碍树）；新语言里卡片**不再轮换形状**（旧的手绘三形随涂鸦机制删除）；
 * 只声明工具调用的空回合不占卡片（工具卡承担）。
 *
 * 视觉断言从 `:root` 的 token 拼期望值，不钉字面量（阶段 23b 的改造）。
 *
 * 需要 AVID_E2E=1 且内核已起（脚本模型即可）。
 */
test.skip(!process.env.AVID_E2E, '需要 AVID_E2E=1 且内核已启动')

const BASE = process.env.AVID_BASE_URL ?? 'http://127.0.0.1:8765'

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
function cardWithRole(page: Page, role: string, variant = 'surface-card'): Locator {
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
  return cardWithRole(page, 'Avid', 'surface-chip')
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

test('模型回复与用户消息是同一族玻璃卡（同高光边、同圆角、同投影，方向相反）', async ({
  page,
}) => {
  await openSession(page)

  const user = userCards(page).first()
  const model = assistantCards(page).first()
  await expect(user).toBeVisible()
  await expect(model).toBeVisible()

  const userStyle = await styleOf(user)
  const modelStyle = await styleOf(model)

  const edge = rgba(await token(page, '--avid-edge-rgb'), await token(page, '--glass-edge-alpha'))
  const face = rgba(await token(page, '--avid-glass-rgb'), await token(page, '--glass-alpha'))
  for (const style of [userStyle, modelStyle]) {
    expect(style.borderWidth, '高光边宽度 = --stroke-hair').toBe(await token(page, '--stroke-hair'))
    expect(style.borderColor, '高光边用 --avid-edge-rgb').toBe(edge)
    expect(style.borderRadius, '圆角 = --r-card').toBe(await token(page, '--r-card'))
    expect(style.backgroundColor, '玻璃面用 --avid-glass-rgb').toBe(face)
    expect(lifts(style.boxShadow), '高度档已在（投影）').toBeGreaterThan(0)
  }
  expect(userStyle.borderRadius, '同族：圆角一致').toBe(modelStyle.borderRadius)
  expect(userStyle.boxShadow, '同族：高度档一致').toBe(modelStyle.boxShadow)

  // 方向：用户靠右（ml-auto），模型靠左（mr-auto）
  expect(userStyle.marginLeft, '用户消息靠右').not.toBe('0px')
  expect(modelStyle.marginRight, '模型回复靠左').not.toBe('0px')
})

test('模型卡片带 Bot 图标，且它是装饰性的', async ({ page }) => {
  await openSession(page)
  const model = assistantCards(page).first()
  await expect(model).toBeVisible()

  const mark = model.locator('svg')
  await expect(mark).toHaveCount(1)
  expect(await mark.getAttribute('aria-hidden'), '装饰不进无障碍树').toBe('true')

  // 图标用 currentColor（父级给 text-ink）→ 换主题跟着走；细笔画是新的图标语言。
  const stroke = await mark.evaluate((svg) => ({
    stroke: svg.getAttribute('stroke'),
    width: svg.getAttribute('stroke-width'),
    cls: svg.getAttribute('class') ?? '',
  }))
  expect(stroke.stroke, '颜色来自 currentColor 而不是写死').toBe('currentColor')
  expect(stroke.width, '细笔画（图标语言）').toBe('1.75')
  expect(stroke.cls, '用 lucide 图标集，不自己画一套').toContain('lucide')
})

test('用户卡片带 User 图标，与模型卡片的图标不同形', async ({ page }) => {
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
  expect(userPaths, '两枚图标必须不同，否则等于没标作者').not.toEqual(modelPaths)

  // 不同图标，但笔触语言一致（同一套图标契约：细笔画 + currentColor）
  const stroke = await userMark.evaluate((svg) => ({
    stroke: svg.getAttribute('stroke'),
    width: svg.getAttribute('stroke-width'),
    cls: svg.getAttribute('class') ?? '',
  }))
  expect(stroke.stroke, '颜色来自 currentColor').toBe('currentColor')
  expect(stroke.width, '细笔画（与模型那一枚同一档）').toBe('1.75')
  expect(stroke.cls, '同一个图标集').toContain('lucide-user')

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

test('消息卡片同形：新语言不再轮换形状', async ({ page }) => {
  await openSession(page)
  const cards = page.getByRole('log').locator('article.surface-card')
  const count = await cards.count()
  expect(count, '至少要有两张消息卡片').toBeGreaterThan(1)

  // 旧语言按条目序号在三种手绘圆角之间轮换（相邻不同形）。新语言删掉了这个机制：
  // 作者靠方向与图标区分，形状是同一档 —— 这条断言守的就是"不再轮换"，
  // 它会抓住任何"顺手加回一点随机形状"的改动。
  const radii: string[] = []
  for (let index = 0; index < Math.min(count, 6); index += 1) {
    radii.push((await styleOf(cards.nth(index))).borderRadius)
  }
  expect(radii, '所有消息卡片同形').toEqual(radii.map(() => radii[0]))
  expect(radii[0], '形状 = --r-card').toBe(await token(page, '--r-card'))
})

test('只声明工具调用的空回合不再占卡片，工具调用由工具卡承担', async ({ page }) => {
  await openSession(page)
  await expandAll(page)

  // 以前空回合会渲染成「Avid + 动作按钮」的空 chip；现在它完全不出现。
  expect(await assistantChips(page).count(), '不再为空回合撑卡片').toBe(0)
  expect(await assistantCards(page).count(), '有正文的回复用整张卡').toBeGreaterThan(0)
  // 那次调用仍然看得见——由工具卡承担，不是被静默丢掉
  expect(
    await page.getByRole('log').getByText('CALL').count(),
    '工具调用仍有自己的卡片',
  ).toBeGreaterThan(0)
})

test('durable 的模型回复才挂 aria-live', async ({ page }) => {
  // 乐观 delta 每帧都在变，播报等于噪音；durable 消息才是完整的一句话。
  // （正文为空的回合已经不渲染卡片，所以不再有「空卡不播报」这半条。）
  await openSession(page)
  const model = assistantCards(page).first()
  await expect(model).toHaveAttribute('aria-live', 'polite')
})
