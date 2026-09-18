import { expect, test } from '@playwright/test'
import type { Locator, Page } from '@playwright/test'

/**
 * 交互反馈回归：安静按钮（`variant="ghost"`，含时间线的「复制文本 / 查看原始 JSON」）
 * 在悬停与键盘聚焦时必须出现与其它按钮同一套方框，禁用态不出框，按下时阴影归零，
 * 且方框的取值全部来自 tokens（换主题只改 tokens.css，不需要动组件）。
 *
 * 断言一律等样式稳定后再读：方框是 120ms/90ms 的过渡，读中间帧会读到插值颜色
 * （例如 rgba(26,26,26,0.165)），那是动画而不是缺陷。用 expect.poll 等结果而不是
 * 关掉动效，顺带证明动效真的在跑。
 */
test.skip(!process.env.AVID_E2E, '需要 AVID_E2E=1 且内核已启动')

const BASE = process.env.AVID_BASE_URL ?? 'http://127.0.0.1:8765'
// `--sketch-r-chip` 是 `4px 6px 3px 5px / 5px 3px 6px 4px`，左上角解析后是 `4px 5px`。
const CHIP_CORNER = '4px 5px'
const CHIP_TOKEN = '--sketch-r-chip'
const INK = 'rgb(26, 26, 26)'
const CARD = 'rgb(255, 255, 255)'
const TRANSPARENT = /rgba?\(0, 0, 0, 0\)/

interface ChipStyle {
  borderColor: string
  borderWidth: string
  borderRadius: string
  backgroundColor: string
  boxShadow: string
  opacity: string
  transform: string
  outlineWidth: string
  outlineColor: string
}

async function styleOf(locator: Locator): Promise<ChipStyle> {
  return locator.evaluate((element) => {
    const style = getComputedStyle(element)
    return {
      borderColor: style.borderColor,
      borderWidth: style.borderTopWidth,
      borderRadius: style.borderTopLeftRadius,
      backgroundColor: style.backgroundColor,
      boxShadow: style.boxShadow,
      opacity: style.opacity,
      transform: style.transform,
      outlineWidth: style.outlineWidth,
      outlineColor: style.outlineColor,
    }
  })
}

/**
 * 硬阴影是否可见。
 *
 * Tailwind 的 ring/shadow 组合会把「无阴影」写成重复的 `rgba(0, 0, 0, 0) 0px 0px 0px 0px`，
 * 而颜色本身带逗号——**不能按逗号切分**去判断，否则会把 rgba() 的参数切开。
 */
function shadowVisible(boxShadow: string): boolean {
  if (boxShadow === 'none') return false
  const transparent = /^(\s*rgba?\(0,\s*0,\s*0,\s*0\)\s+0px\s+0px\s+0px\s+0px\s*,?)+$/
  return !transparent.test(boxShadow)
}

/** 等到某个样式条件成立（过渡结束后再断言）。 */
async function expectStyle(
  locator: Locator,
  predicate: (style: ChipStyle) => boolean,
  label: string,
): Promise<void> {
  await expect
    .poll(async () => predicate(await styleOf(locator)), { message: label, timeout: 3_000 })
    .toBe(true)
}

async function openSessionWithTool(page: Page): Promise<void> {
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

function actionButton(page: Page, name: string): Locator {
  return page.getByRole('log').getByRole('button', { name }).first()
}

test('时间线消息动作：静止无框，悬停出框，样式取自 token', async ({ page }) => {
  await openSessionWithTool(page)
  const action = actionButton(page, '复制文本')

  // 静止态：透明度 0（时间线的「悬停/聚焦才出现」）且没有墨线方框
  const rest = await styleOf(action)
  expect(rest.opacity, '静止时应隐藏').toBe('0')
  expect(rest.borderColor, '静止时边框透明').toMatch(TRANSPARENT)
  expect(shadowVisible(rest.boxShadow), '静止时没有硬阴影').toBe(false)
  // 动效存在，且只过渡显式属性（不是 transition-all）
  const motion = await action.evaluate((element) => {
    const style = getComputedStyle(element)
    return { property: style.transitionProperty, duration: style.transitionDuration }
  })
  expect(motion.property).toContain('border-color')
  expect(motion.property).toContain('box-shadow')
  expect(motion.property).not.toContain('all')
  expect(motion.duration).not.toBe('0s')

  await action.hover()
  await expectStyle(action, (style) => style.opacity === '1', '悬停后可见')
  await expectStyle(action, (style) => style.borderColor === INK, '悬停出现墨色方框')
  const hovered = await styleOf(action)
  expect(hovered.borderWidth, '边框宽度与其它按钮一致（--stroke-hair）').toBe('2px')
  expect(hovered.borderRadius, '圆角取 --sketch-r-chip').toBe(CHIP_CORNER)
  const token = await action.evaluate(
    (element, name) => getComputedStyle(element).getPropertyValue(name),
    CHIP_TOKEN,
  )
  expect(token.trim(), '圆角来自 token 而不是组件里的字面量').not.toBe('')
  expect(hovered.backgroundColor, '底色取纸卡 token').toBe(CARD)
  expect(hovered.boxShadow, '高度取 --sticker-1 档').toContain('1px 1px 0px 0px')
})

test('键盘聚焦：按钮显形、出框，并保留焦点环', async ({ page }) => {
  await openSessionWithTool(page)
  const action = actionButton(page, '复制文本')

  await action.focus()
  await page.keyboard.press('Tab')
  await page.keyboard.press('Shift+Tab') // 用键盘回到该按钮 → 命中 :focus-visible

  await expectStyle(action, (style) => style.opacity === '1', '键盘聚焦必须让动作可见')
  await expectStyle(action, (style) => style.borderColor === INK, '聚焦出现墨色方框')
  await expectStyle(action, (style) => shadowVisible(style.boxShadow), '聚焦有硬阴影')
  await expectStyle(action, (style) => style.outlineWidth === '2px', '保留 focus-visible 焦点环')
  const focused = await styleOf(action)
  expect(focused.outlineColor, '焦点环用强调色 token').toBe('rgb(212, 122, 90)')
})

test('按下：阴影归零、位移等于 --sticker-1 档偏移', async ({ page }) => {
  await openSessionWithTool(page)
  const action = actionButton(page, '查看原始 JSON')
  await action.hover()
  await expectStyle(action, (style) => shadowVisible(style.boxShadow), '悬停先出现阴影')

  await page.mouse.down()
  await expectStyle(action, (style) => !shadowVisible(style.boxShadow), '按住时阴影归零')
  await expectStyle(
    action,
    (style) => style.transform === 'matrix(1, 0, 0, 1, 1, 1)',
    '位移量 = 本档偏移（1px，缩放为 1 时）',
  )
  await page.mouse.up()
})

test('禁用：不出框、不位移、不显示阴影', async ({ page }) => {
  await openSessionWithTool(page)
  const action = actionButton(page, '复制文本')
  await action.evaluate((element) => {
    ;(element as HTMLButtonElement).disabled = true
  })
  await action.hover()

  await expectStyle(action, (style) => !shadowVisible(style.boxShadow), '禁用时没有阴影')
  const disabled = await styleOf(action)
  expect(disabled.borderColor, '禁用时边框透明').toMatch(TRANSPARENT)
  expect(disabled.transform, '禁用时不位移').toBe('none')
})

test('换主题只改 tokens：注入另一组 --avid-*-rgb 后方框跟着变', async ({ page }) => {
  await openSessionWithTool(page)
  const action = actionButton(page, '复制文本')

  // 模拟「深色」一组 token：只替换 RGB 三元组，组件一个字都不改
  await page.addStyleTag({
    content: ':root { --avid-ink-rgb: 236 233 228; --avid-card-rgb: 30 32 36; }',
  })
  await action.hover()
  await expectStyle(
    action,
    (style) => style.borderColor === 'rgb(236, 233, 228)',
    '边框跟随 --avid-ink-rgb',
  )
  const dark = await styleOf(action)
  expect(dark.backgroundColor, '底色跟随 --avid-card-rgb').toBe('rgb(30, 32, 36)')
  expect(dark.boxShadow, '硬阴影用同一个墨色 token').toContain('rgb(236, 233, 228)')
})

test('系统深色方案下样式不变（项目只有一套 token 主题）', async ({ page }) => {
  // 本项目按设计只有一套纸面主题，`dark:` 修饰符被 lint 禁止。这里验证系统处于
  // prefers-color-scheme: dark 时方框仍是同一套 token 值（不会因 UA 方案而变样）。
  await page.emulateMedia({ colorScheme: 'dark' })
  await openSessionWithTool(page)
  const action = actionButton(page, '复制文本')
  await action.hover()
  await expectStyle(action, (style) => style.borderColor === INK, '深色方案下边框仍是墨色 token')
  const style = await styleOf(action)
  expect(style.backgroundColor, '底色仍是纸卡 token').toBe(CARD)
  expect(style.boxShadow, '高度档位不变').toContain('1px 1px 0px 0px')
})

test('其它安静按钮与时间线动作同族（同一圆角与高度档）', async ({ page }) => {
  await openSessionWithTool(page)

  const cases: { name: string; locator: Locator }[] = [
    {
      name: '会话标题',
      locator: page.locator('section[aria-label="会话"] ul li').first().locator('button').first(),
    },
    { name: '面板收起', locator: page.getByRole('button', { name: '收起' }).first() },
  ]

  for (const item of cases) {
    if ((await item.locator.count()) === 0) continue
    await item.locator.hover()
    await expectStyle(item.locator, (style) => style.borderColor === INK, `${item.name}：出墨线方框`)
    const style = await styleOf(item.locator)
    expect(style.borderRadius, `${item.name}：圆角与时间线动作一致`).toBe(CHIP_CORNER)
    expect(style.boxShadow, `${item.name}：高度档位一致（--sticker-1）`).toContain(
      '1px 1px 0px 0px',
    )
  }
})
