import { expect, test } from '@playwright/test'
import type { Locator, Page } from '@playwright/test'

/**
 * 交互反馈回归：**所有按键本身就有方框**（与「改名」同族），悬停再抬升一档。
 * 包括会话列表里的会话标题——它和下面的「改名 / 删除」是同一排控件，没框时不像一族。
 *
 * 需要 AVID_E2E=1 且内核已起（脚本模型即可）。
 * 断言一律等样式稳定后再读：阴影与位移是 90–120ms 的过渡，读中间帧会读到插值
 * （例如 rgba(26,26,26,0.165) 或 0.638591px），那是动画而不是缺陷。
 */
test.skip(!process.env.AVID_E2E, '需要 AVID_E2E=1 且内核已启动')

const BASE = process.env.AVID_BASE_URL ?? 'http://127.0.0.1:8765'
// `--sketch-r-chip` = `4px 6px 3px 5px / 5px 3px 6px 4px`，左上角解析后是 `4px 5px`。
const CHIP_CORNER = '4px 5px'
const INK = 'rgb(26, 26, 26)'
const CARD = 'rgb(255, 255, 255)'
const STICKER_2 = '2px 2px 0px 0px'
const STICKER_3 = '3px 3px 0px 0px'
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

test('行动型按键本身就有方框（不依赖悬停）：删除与改名同族', async ({ page }) => {
  await openSession(page)
  const remove = page.getByRole('button', { name: '删除' }).first()
  const rename = page.getByRole('button', { name: '改名' }).first()

  const rest = await styleOf(remove)
  expect(rest.borderColor, '静止时就有墨线方框').toBe(INK)
  expect(rest.borderWidth, '边框宽度 = --stroke-hair').toBe('2px')
  expect(rest.borderRadius, '圆角 = --sketch-r-chip').toBe(CHIP_CORNER)
  expect(rest.backgroundColor, '底色 = 纸卡 token').toBe(CARD)
  expect(rest.boxShadow, '静止时就有 --sticker-2 档硬阴影').toContain(STICKER_2)

  const sibling = await styleOf(rename)
  expect(rest.borderWidth, '与「改名」同边框').toBe(sibling.borderWidth)
  expect(rest.borderRadius, '与「改名」同圆角').toBe(sibling.borderRadius)
  expect(rest.boxShadow, '与「改名」同高度档').toBe(sibling.boxShadow)
})

test('悬停抬升一档，按住时阴影归零、位移等于新档偏移', async ({ page }) => {
  await openSession(page)
  // 用「复制文本」而不是「删除」：删除点下去会弹确认框，按钮随即失去悬停态。
  const action = page.getByRole('log').getByRole('button', { name: '复制文本' }).first()

  await action.hover()
  await expectStyle(
    action,
    (style) => style.boxShadow.includes(STICKER_3),
    '悬停抬升一档（--sticker-2 → --sticker-3）',
  )

  await page.mouse.down()
  await expectStyle(action, (style) => !shadowVisible(style.boxShadow), '按住时阴影归零')
  await expectStyle(
    action,
    (style) => style.transform === 'matrix(1, 0, 0, 1, 3, 3)',
    '位移 = 悬停后本档偏移（3px）',
  )
  await page.mouse.up()
  await expectStyle(action, (style) => style.boxShadow.includes(STICKER_3), '松开回到悬停档')
})

test('键盘聚焦：方框在、焦点环在', async ({ page }) => {
  await openSession(page)
  const remove = page.getByRole('button', { name: '删除' }).first()

  await remove.focus()
  await page.keyboard.press('Tab')
  await page.keyboard.press('Shift+Tab') // 用键盘回到该按钮 → 命中 :focus-visible

  await expectStyle(remove, (style) => style.outlineWidth === '2px', '保留 focus-visible 焦点环')
  const focused = await styleOf(remove)
  expect(focused.outlineColor, '焦点环用强调色 token').toBe('rgb(212, 122, 90)')
  expect(focused.borderColor, '聚焦时方框仍在').toBe(INK)
  expect(focused.boxShadow).toContain(STICKER_2)
})

test('禁用：方框还在，但不抬升、不位移', async ({ page }) => {
  await openSession(page)
  const remove = page.getByRole('button', { name: '删除' }).first()
  await remove.evaluate((element) => {
    ;(element as HTMLButtonElement).disabled = true
  })
  await remove.hover()

  await expectStyle(
    remove,
    (style) => style.boxShadow.includes(STICKER_2),
    '禁用时停在 --sticker-2，不抬升',
  )
  const disabled = await styleOf(remove)
  expect(disabled.borderColor, '禁用时仍有方框').toBe(INK)
  expect(disabled.transform, '禁用时不位移').toBe('none')
  expect(disabled.opacity, '禁用时半透明').toBe('0.5')
})

test('时间线动作：静止（未悬停）时就已带方框，悬停只负责显形', async ({ page }) => {
  await openSession(page)
  const action = page.getByRole('log').getByRole('button', { name: '复制文本' }).first()

  // 关键：**没有做任何悬停**，方框已经在
  const rest = await styleOf(action)
  expect(rest.opacity, '静止时按时间线约定隐藏').toBe('0')
  expect(rest.borderColor, '静止时方框已在（墨线）').toBe(INK)
  expect(rest.boxShadow, '静止时高度档已在（--sticker-2）').toContain(STICKER_2)

  await action.hover()
  await expectStyle(action, (style) => style.opacity === '1', '悬停让动作显形')
  await expectStyle(action, (style) => style.boxShadow.includes(STICKER_3), '悬停抬升一档')
})

test('会话标题也有框，与「改名 / 删除」同族', async ({ page }) => {
  await openSession(page)
  const title = page.locator('section[aria-label="会话"] ul li').first().locator('button').first()
  const remove = page.getByRole('button', { name: '删除' }).first()

  const rest = await styleOf(title)
  expect(rest.borderColor, '标题本身就有墨线方框').toBe(INK)
  expect(rest.borderWidth, '边框宽度 = --stroke-hair').toBe('2px')
  expect(rest.borderRadius, '圆角 = --sketch-r-chip').toBe(CHIP_CORNER)
  expect(rest.backgroundColor, '底色 = 纸卡 token').toBe(CARD)
  expect(rest.boxShadow, '高度取 --sticker-2 档').toContain(STICKER_2)

  const sibling = await styleOf(remove)
  expect(rest.borderWidth, '与「删除」同边框').toBe(sibling.borderWidth)
  expect(rest.borderRadius, '与「删除」同圆角').toBe(sibling.borderRadius)
  expect(rest.boxShadow, '与「删除」同高度档').toBe(sibling.boxShadow)

  // 与卡片同宽：标题的方框铺满卡片的内容区，且不把卡片撑出横向滚动
  const widths = await title.evaluate((element) => {
    const card = element.parentElement as HTMLElement
    const style = getComputedStyle(card)
    return {
      title: element.getBoundingClientRect().width,
      inner:
        card.clientWidth -
        Number.parseFloat(style.paddingLeft) -
        Number.parseFloat(style.paddingRight),
      cardScroll: card.scrollWidth,
      cardClient: card.clientWidth,
    }
  })
  expect(Math.abs(widths.title - widths.inner), '标题方框宽度 = 卡片内容区宽度').toBeLessThanOrEqual(1)
  expect(widths.cardScroll, '卡片不应出现横向溢出').toBeLessThanOrEqual(widths.cardClient + 1)
})

test('换主题只改 tokens：注入另一组 --avid-*-rgb 后方框跟着变', async ({ page }) => {
  await openSession(page)
  const remove = page.getByRole('button', { name: '删除' }).first()

  // 模拟另一套主题：只替换 RGB 三元组，组件一个字都不改
  await page.addStyleTag({
    content: ':root { --avid-ink-rgb: 236 233 228; --avid-card-rgb: 30 32 36; }',
  })
  await expectStyle(
    remove,
    (style) => style.borderColor === 'rgb(236, 233, 228)',
    '边框跟随 --avid-ink-rgb',
  )
  await expectStyle(
    remove,
    (style) => style.backgroundColor === 'rgb(30, 32, 36)',
    '底色跟随 --avid-card-rgb',
  )
  await expectStyle(
    remove,
    (style) => style.boxShadow.includes('rgb(236, 233, 228)'),
    '硬阴影用同一个墨色 token',
  )
})

test('系统深色方案下样式不变（项目只有一套 token 主题）', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' })
  await openSession(page)
  const remove = page.getByRole('button', { name: '删除' }).first()
  const style = await styleOf(remove)
  expect(style.borderColor, '深色方案下仍是墨色 token').toBe(INK)
  expect(style.backgroundColor, '底色仍是纸卡 token').toBe(CARD)
  expect(style.boxShadow).toContain(STICKER_2)
})
