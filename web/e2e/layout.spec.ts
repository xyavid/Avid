/**
 * 布局与视觉的 E2E。
 *
 * 为什么这些断言要用**计算样式**而不是截图比对：
 * 截图基线对字体渲染、子像素、平台差异都敏感，本机与 CI 之间几乎必然漂移，
 * 维护成本很快超过收益（旧前端的 4 张视觉基线就是这么被删掉的）。而
 * "token 到底有没有落到元素上"是一个**确定**的事实——读计算值就能钉住，
 * 且失败信息直接给出期望与实际的色值，比"像素差了多少"可诊断得多。
 *
 * 截图另有用途：失败时 Playwright 自动留档，供人看"长什么样"。两者不互相替代。
 */

import { expect, test } from './lib/fixtures'
import { defaultSession, defaultWorkspace } from './lib/api'

/** 从 `tokens.css` 里读一个 token 的实际值，作为断言的期望来源。 */
async function readToken(page: import('@playwright/test').Page, name: string): Promise<string> {
  return page.evaluate((token) => getComputedStyle(document.documentElement).getPropertyValue(token).trim(), name)
}

test.describe('布局与视觉', () => {
  test('桌面端：整页不滚动，消息区自己滚，输入区始终在视口内', async ({ page, stubApi }) => {
    await stubApi({
      sessions: [defaultSession('s-1', '布局会话')],
      workspaces: [defaultWorkspace('ws-1', '/home/fishy/Avid', 'Avid')],
    })
    await page.goto('/', { waitUntil: 'domcontentloaded' })
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible()

    const metrics = await page.evaluate(() => {
      const composer = document.querySelector('textarea')
      const timeline = document.querySelector('[role="log"]')
      return {
        pageScrolls: document.documentElement.scrollHeight > window.innerHeight + 1,
        horizontalOverflow: document.documentElement.scrollWidth > window.innerWidth + 1,
        composerBottom: composer ? Math.round(composer.getBoundingClientRect().bottom) : null,
        viewportHeight: window.innerHeight,
        timelineScrollable: timeline ? timeline.scrollHeight >= timeline.clientHeight : null,
      }
    })

    // 整页不滚动是这套布局的地基：高度链断了必然表现为整页出现滚动条。
    expect(metrics.pageScrolls, '整页出现了纵向滚动条：高度链（h-dvh / min-h-0）断了').toBe(false)
    expect(metrics.horizontalOverflow, '出现了横向溢出').toBe(false)
    expect(metrics.composerBottom).not.toBeNull()
    expect(metrics.composerBottom!, '输入区被推出视口').toBeLessThanOrEqual(metrics.viewportHeight)
    expect(metrics.timelineScrollable).not.toBeNull()
  })

  test('纸本 token 真的落到元素上（背景 / 文字 / 圆角 / 字重）', async ({ page, stubApi }) => {
    await stubApi({ sessions: [defaultSession('s-1', '视觉会话')] })
    await page.goto('/', { waitUntil: 'domcontentloaded' })
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible()

    const canvas = await readToken(page, '--avid-bg-rgb')
    const deep = await readToken(page, '--avid-bg-deep-rgb')

    const observed = await page.evaluate(() => {
      const body = getComputedStyle(document.body)
      const nav = document.querySelector('nav[aria-label="会话"]')
      const radii = [...document.querySelectorAll('*')]
        .map((el) => parseFloat(getComputedStyle(el).borderTopLeftRadius) || 0)
        .filter((value) => Number.isFinite(value))
      const weights = [...document.querySelectorAll('h1, h2, h3, strong, b')].map((el) =>
        Number(getComputedStyle(el).fontWeight),
      )
      return {
        bodyBg: body.backgroundColor,
        bodyColor: body.color,
        bodyFont: body.fontFamily,
        navBg: nav ? getComputedStyle(nav).backgroundColor : null,
        maxRadius: radii.length > 0 ? Math.max(...radii) : 0,
        maxWeight: weights.length > 0 ? Math.max(...weights) : 0,
      }
    })

    const asRgb = (triple: string) => {
      const [r, g, b] = triple.split(/\s+/).map(Number)
      return `rgb(${r}, ${g}, ${b})`
    }

    // 背景与文字直接对着 token 断言：色值改了、或某个类名没生效，这里立刻红。
    expect(observed.bodyBg).toBe(asRgb(canvas))
    expect(observed.navBg).toBe(asRgb(deep))
    // 纸本的两条硬规矩：背景不纯白、文字不纯黑。
    expect(observed.bodyBg).not.toBe('rgb(255, 255, 255)')
    expect(observed.bodyColor).not.toBe('rgb(0, 0, 0)')
    /*
     * 字体栈必须是**无衬线优先**。注意断言写法：不能写 `not.toContain('serif')`，
     * 因为通用兜底就是 `sans-serif`，它包含 "serif" 子串——那样写会永远失败。
     * 判据要看**首个字族**（真正决定观感的那个）：无衬线档的首项应为 Inter。
     */
    expect(observed.bodyFont.split(',')[0]!.trim()).toBe('Inter')
    // 极方角：全站圆角上限 4px（lg 档），没有胶囊滥用。
    expect(observed.maxRadius, '出现了超过 lg(4px) 的圆角，破坏了"极方"这条纪律').toBeLessThanOrEqual(4)
    // 字重上限 500。
    expect(observed.maxWeight, '出现了 600+ 的字重').toBeLessThanOrEqual(500)
  })

  test('宽度降级：820px 收起导航列，1180px 以下检查器改浮层', async ({ page, stubApi }) => {
    await stubApi({
      sessions: [defaultSession('s-1', '降级会话')],
      workspaces: [defaultWorkspace('ws-1', '/home/fishy/Avid', 'Avid')],
    })

    // 窄屏（<900）：导航列不常驻（AppShell 不渲染它），但主区仍完整可用。
    await page.setViewportSize({ width: 820, height: 900 })
    await page.goto('/', { waitUntil: 'domcontentloaded' })
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible()
    await expect(page.locator('nav[aria-label="会话"]')).toHaveCount(0)
    await expect(page.getByRole('textbox', { name: '输入' })).toBeVisible()

    /*
     * 中间档（900 ≤ 宽 < 1180）：导航列常驻，但检查器**不再占一整列**。
     * 判据用右栏容器的宽度而不是"某个类名"——1180 断点会在 380px 的检查器
     * 与主区之间二选一，而信息面板是常驻的，所以这一档必须证明：
     * 导航在、输入区在、右栏没有把主区压到不可读。
     */
    await page.setViewportSize({ width: 1050, height: 900 })
    await page.goto('/', { waitUntil: 'domcontentloaded' })
    await expect(page.locator('nav[aria-label="会话"]')).toBeVisible()
    const mid = await page.evaluate(() => ({
      main: Math.round(document.querySelector('#avid-main')!.getBoundingClientRect().width),
      composerBottom: Math.round(document.querySelector('textarea')!.getBoundingClientRect().bottom),
      viewport: window.innerHeight,
    }))
    expect(mid.main, '主区在中间档被压得太窄').toBeGreaterThan(380)
    expect(mid.composerBottom).toBeLessThanOrEqual(mid.viewport)

    // 宽屏（≥1180）：导航列常驻，右栏信息面板也在。
    await page.setViewportSize({ width: 1440, height: 900 })
    await page.goto('/', { waitUntil: 'domcontentloaded' })
    await expect(page.locator('nav[aria-label="会话"]')).toBeVisible()
    await expect(page.getByRole('region', { name: '工作区' })).toBeVisible()
  })

  test('收起侧栏只改宽度，不丢内容', async ({ page, stubApi }) => {
    await stubApi({
      sessions: [defaultSession('s-1', '收起测试')],
      workspaces: [defaultWorkspace('ws-1', '/home/fishy/Avid', 'Avid')],
    })
    await page.goto('/', { waitUntil: 'domcontentloaded' })
    const nav = page.locator('nav[aria-label="会话"]')
    await expect(nav).toBeVisible()

    /*
     * 宽度由**外层容器**控制，`nav` 自身是 `w-full`——所以必须量父元素的宽度。
     * 量错对象会让这条断言永远"通过不了"，也让它失去意义。
     */
    const columnWidth = () =>
      page.evaluate(() => {
        const nav = document.querySelector('nav[aria-label="会话"]')
        return nav ? Math.round((nav.parentElement as HTMLElement).getBoundingClientRect().width) : null
      })

    const before = await columnWidth()
    expect(before, '导航列初始应为展开宽度（240px）').toBeGreaterThan(100)

    await page.getByRole('button', { name: '收起会话列表' }).click()
    await expect.poll(columnWidth).toBeLessThan(before!)
    const after = await columnWidth()
    expect(after).not.toBeNull()
    expect(after!).toBeLessThan(before!)

    // 会话名仍在（不是被卸载，只是变窄）。
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('收起测试')
  })

  /*
   * `fixme` 而不是注释掉，也不是让它红着：这条用例依赖 task-9（富文本正文）落地。
   * 把一条**已知失败**留在仓库里有两个坏处——CI 永远红（于是没人再看它），
   * 或者被顺手删掉（于是这条断言永远没人写）。`fixme` 在报告里显式列出"未运行"，
   * 依赖落地后删掉这一行即可。
   */
  test('助手正文里的 markdown 被渲染成结构，而不是原样字符', async ({ page, stubApi }) => {
    await stubApi({
      sessions: [defaultSession('s-1', 'markdown 会话')],
      entries: {
        entries: [
          {
            entry_id: 'e1',
            parent_id: null,
            seq: 1,
            timestamp: 1_700_000_000_000,
            type: 'message',
            message: {
              role: 'assistant',
              content: '先定三件事：\n\n- 暖纸三层底\n- 单点强调色 `#5BA88C`\n\n## 细节\n\n**不要**用纯白。',
            },
          },
        ],
        has_more: false,
        next_cursor: null,
        truncated_tail: false,
      },
    })
    await page.goto('/', { waitUntil: 'domcontentloaded' })

    const timeline = page.getByRole('log')
    // 列表项与二级标题都真的成为元素，而不是带 `-` / `##` 的纯文本。
    await expect(timeline.locator('li', { hasText: '暖纸三层底' })).toBeVisible()
    await expect(timeline.locator('h2', { hasText: '细节' })).toBeVisible()
    // 行内 code 有独立元素（参考截图里它有浅底）。
    await expect(timeline.locator('code', { hasText: '#5BA88C' })).toBeVisible()
    // 原文里的 `##` 不该作为字面量出现在页面上。
    await expect(timeline.getByText('##', { exact: false })).toHaveCount(0)
  })
})
