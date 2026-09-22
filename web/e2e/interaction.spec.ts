import { expect, test } from '@playwright/test'
import type { Locator, Page } from '@playwright/test'

import { brightness, lifts, rgba, token } from './helpers'

/**
 * 交互反馈回归：**所有行动型按键本身就有方框**（与「改名」同族），悬停抬升一档投影、
 * 按住收掉投影；时间线的动作行**常驻可见**。
 *
 * **断言不钉字面值**（阶段 23b 的改造）：方框的边框、圆角、投影与焦点环全部从 `:root`
 * 的 token 读出来再组合期望值（`--stroke-hair` / `--r-chip` / `--lift-*` / `--glass-*`）。
 * 于是换视觉语言不必改这个文件——它守的是「机制还在、同族一致、只有一处值来源」，
 * 而不是「阴影正好是 2px 2px 0 0」。旧版把 33 条 expect 写成字面值，正是 23b 要解的那类耦合。
 *
 * 需要 AVID_E2E=1 且内核已起（脚本模型即可）。
 */
test.skip(!process.env.AVID_E2E, '需要 AVID_E2E=1 且内核已启动')

const BASE = process.env.AVID_BASE_URL ?? 'http://127.0.0.1:8765'
const COMPOSER = '输入指令，Enter 发送，Shift+Enter 换行'

interface Look {
  borderColor: string
  borderWidth: string
  borderRadius: string
  backgroundColor: string
  boxShadow: string
  filter: string
  opacity: string
  transform: string
  outlineWidth: string
  outlineColor: string
}

async function look(locator: Locator): Promise<Look> {
  return locator.evaluate((element) => {
    const style = getComputedStyle(element)
    return {
      borderColor: style.borderColor,
      borderWidth: style.borderTopWidth,
      borderRadius: style.borderTopLeftRadius,
      backgroundColor: style.backgroundColor,
      boxShadow: style.boxShadow,
      filter: style.filter,
      opacity: style.opacity,
      transform: style.transform,
      outlineWidth: style.outlineWidth,
      outlineColor: style.outlineColor,
    }
  })
}

/** 等到某个样式条件成立（过渡结束后再断言）。 */
async function expectStyle(
  locator: Locator,
  predicate: (style: Look) => boolean,
  label: string,
): Promise<void> {
  await expect
    .poll(async () => predicate(await look(locator)), { message: label, timeout: 3_000 })
    .toBe(true)
}

async function openSession(page: Page): Promise<void> {
  const listed = await page.request.get(`${BASE}/api/sessions`)
  const sessions: { id: string; message_count: number }[] = (await listed.json()).sessions
  const target = sessions.find((item) => item.message_count > 0)
  expect(target, '需要一个含工具调用的会话').toBeTruthy()
  await page.goto(`${BASE}/sessions/${target?.id}`)
  await expect(page.getByLabel(COMPOSER)).toBeVisible()
  await expect
    .poll(async () => (await page.getByRole('log').innerText()).length, { timeout: 10_000 })
    .toBeGreaterThan(50)
}

test('行动型按键本身就有方框（不依赖悬停）：删除与改名同族', async ({ page }) => {
  await openSession(page)
  const remove = page.getByRole('button', { name: '删除' }).first()
  const rename = page.getByRole('button', { name: '改名' }).first()

  const rest = await look(remove)
  expect(rest.borderWidth, '边框宽度 = --stroke-hair').toBe(await token(page, '--stroke-hair'))
  expect(rest.borderRadius, '圆角 = --r-chip').toBe(await token(page, '--r-chip'))
  expect(rest.borderColor, '边框 = 玻璃高光边（--avid-edge-rgb + --glass-edge-alpha）').toBe(
    rgba(await token(page, '--avid-edge-rgb'), await token(page, '--glass-edge-alpha')),
  )
  expect(lifts(rest.boxShadow), '静止时就有投影（高度档已在）').toBeGreaterThan(0)

  const sibling = await look(rename)
  expect(rest.borderWidth, '与「改名」同边框').toBe(sibling.borderWidth)
  expect(rest.borderRadius, '与「改名」同圆角').toBe(sibling.borderRadius)
  expect(rest.boxShadow, '与「改名」同高度档').toBe(sibling.boxShadow)
})

test('悬停抬升一档，按住收掉投影并压暗，松开回悬停档', async ({ page }) => {
  await openSession(page)
  // 用「复制文本」而不是「删除」：删除点下去会弹确认框，按钮随即失去悬停态。
  const action = page.getByRole('log').getByRole('button', { name: '复制文本' }).first()

  const rest = await look(action)
  await action.hover()
  await expectStyle(
    action,
    (style) => style.boxShadow !== rest.boxShadow && lifts(style.boxShadow) > 0,
    '悬停抬升一档（投影变化）',
  )

  await page.mouse.down()
  await expectStyle(
    action,
    (style) => lifts(style.boxShadow) === 0,
    '按住时只剩高光带（投影收掉）',
  )
  await expectStyle(action, (style) => brightness(style.filter) < 1, '按住时压暗')
  await page.mouse.up()
  await expectStyle(
    action,
    (style) => lifts(style.boxShadow) > 0 && brightness(style.filter) >= 1,
    '松开回到悬停档',
  )
})

test('键盘聚焦：方框在、焦点环用 token', async ({ page }) => {
  await openSession(page)
  const remove = page.getByRole('button', { name: '删除' }).first()

  await remove.focus()
  await page.keyboard.press('Tab')
  await page.keyboard.press('Shift+Tab') // 用键盘回到该按钮 → 命中 :focus-visible

  const ringWidth = await token(page, '--focus-ring-width')
  await expectStyle(remove, (style) => style.outlineWidth === ringWidth, '焦点环出现')
  const focused = await look(remove)
  expect(focused.outlineColor, '焦点环用强调色 token').toBe(
    `rgb(${(await token(page, '--avid-accent-rgb')).split(/\s+/).join(', ')})`,
  )
  expect(focused.borderWidth, '聚焦时方框仍在').toBe(await token(page, '--stroke-hair'))
  expect(lifts(focused.boxShadow), '聚焦时高度档仍在').toBeGreaterThan(0)
})

test('禁用：方框还在，但不抬升、不位移', async ({ page }) => {
  await openSession(page)
  const remove = page.getByRole('button', { name: '删除' }).first()
  await remove.evaluate((element) => {
    ;(element as HTMLButtonElement).disabled = true
  })
  const rest = await look(remove)
  await remove.hover()
  await page.waitForTimeout(200)

  const disabled = await look(remove)
  expect(disabled.boxShadow, '禁用时投影与静止一致（不抬升）').toBe(rest.boxShadow)
  expect(disabled.borderWidth, '禁用时仍有方框').toBe(await token(page, '--stroke-hair'))
  // 新语言不做位移：高度由投影承担，所以 transform 永远不该被用来表达层级。
  expect(disabled.transform, '新语言不做位移').toBe('none')
  expect(disabled.opacity, '禁用时半透明').toBe('0.5')
})

test('时间线动作：常驻可见，悬停只抬升一档', async ({ page }) => {
  await openSession(page)
  const action = page.getByRole('log').getByRole('button', { name: '复制文本' }).first()

  // 关键：**没有做任何悬停**，动作就该看得见——以前是 opacity-0 + 悬停才显形，
  // 代价是「有这功能」本身要先被猜到。
  const rest = await look(action)
  expect(rest.opacity, '静止时就该可见').toBe('1')
  expect(rest.borderWidth, '静止时方框已在').toBe(await token(page, '--stroke-hair'))
  expect(lifts(rest.boxShadow), '静止时高度档已在').toBeGreaterThan(0)

  await action.hover()
  await expectStyle(action, (style) => style.opacity === '1', '悬停时仍然可见')
  await expectStyle(
    action,
    (style) => style.boxShadow !== rest.boxShadow,
    '悬停抬升一档',
  )

  // 条目级「查看原始 JSON」已删：检查器改由工具卡的「查看」打开
  // （conversation.spec.ts 那条检查器用例顺带证明它没被一起拆掉）。
  await expect(page.getByRole('button', { name: '查看原始 JSON' })).toHaveCount(0)
})

test('会话标题也有框，与「改名 / 删除」同族', async ({ page }) => {
  await openSession(page)
  // 用 aria-current 定位"当前会话的标题"：导航树是嵌套的（工作区 → 会话），
  // 按结构取 `first button` 会取到工作区文件夹的折叠按钮。
  const title = page.locator('section[aria-label="工作区"] [aria-current="true"]').first()
  const remove = page.getByRole('button', { name: '删除' }).first()

  const rest = await look(title)
  expect(rest.borderWidth, '标题本身就有方框').toBe(await token(page, '--stroke-hair'))
  expect(rest.borderRadius, '圆角 = --r-chip').toBe(await token(page, '--r-chip'))
  expect(lifts(rest.boxShadow), '高度档已在').toBeGreaterThan(0)

  const sibling = await look(remove)
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

test('换主题只改 tokens：注入另一组 RGB 三元组后方框跟着变', async ({ page }) => {
  await openSession(page)
  const remove = page.getByRole('button', { name: '删除' }).first()

  // 模拟另一套主题：只替换 RGB 三元组，组件一个字都不改。
  await page.addStyleTag({
    content: ':root { --avid-edge-rgb: 40 40 40; --avid-glass-rgb: 30 32 36; }',
  })
  // 期望值从**注入之后的 token** 里读出来：连"另一套主题的取值"也不钉在用例里。
  const edge = rgba(await token(page, '--avid-edge-rgb'), await token(page, '--glass-edge-alpha'))
  const face = rgba(await token(page, '--avid-glass-rgb'), await token(page, '--glass-alpha'))
  await expectStyle(remove, (style) => style.borderColor === edge, '边框跟随 --avid-edge-rgb')
  await expectStyle(remove, (style) => style.backgroundColor === face, '玻璃面跟随 --avid-glass-rgb')
})

test('系统深色方案下样式不变（项目只有一套 token 主题）', async ({ page }) => {
  await openSession(page)
  const remove = page.getByRole('button', { name: '删除' }).first()
  const light = await look(remove)

  await page.emulateMedia({ colorScheme: 'dark' })
  const dark = await look(remove)

  expect(dark.borderColor, '深色方案下边框不变').toBe(light.borderColor)
  expect(dark.backgroundColor, '深色方案下玻璃面不变').toBe(light.backgroundColor)
  expect(dark.boxShadow, '深色方案下高度档不变').toBe(light.boxShadow)
})
