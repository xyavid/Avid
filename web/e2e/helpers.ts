import { expect } from '@playwright/test'
import type { APIRequestContext, APIResponse, Page } from '@playwright/test'

/**
 * e2e 共用的造数据入口。
 *
 * 建会话**必须显式指定工作区**（服务端缺 `workspace` 一律 400 `workspace_required`）。
 * 进程自己绑定的那个工作地点只做预选，不替代这一次选择——所以测试也要先问服务端
 * "绑定的是哪一个"，再把它显式传回去，而不是指望服务端兜住。
 */
export const BASE = process.env.AVID_BASE_URL ?? 'http://127.0.0.1:8765'

interface WorkspaceItem {
  id: string
  is_default?: boolean
}

export async function boundWorkspace(request: APIRequestContext): Promise<string> {
  const response = await request.get(`${BASE}/api/workspaces`)
  const { workspaces } = (await response.json()) as { workspaces: WorkspaceItem[] }
  const chosen = workspaces.find((item) => item.is_default) ?? workspaces[0]
  if (!chosen) throw new Error('服务端没有绑定任何工作地点')
  return chosen.id
}

export async function createSession(
  request: APIRequestContext,
  data: Record<string, unknown> = {},
): Promise<APIResponse> {
  return request.post(`${BASE}/api/sessions`, {
    data: { workspace: await boundWorkspace(request), ...data },
  })
}

function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

interface WorkspaceItemWithName extends WorkspaceItem {
  name: string | null
  root: string
  is_default?: boolean
}

/** 导航树里一个工作区文件夹的折叠按钮（可访问名 = 名字 + 条数）。 */
export function workspaceFolder(page: Page, name: string) {
  return page.getByRole('button', { name: new RegExp(`^${escapeRegExp(name)}`) })
}

/**
 * 在某个工作区文件夹里新建会话（导航树的 ＋）。
 *
 * 工作区现在像文件夹：在哪个文件夹上点 ＋ 就在哪个工作区建会话，所以"选工作区"
 * 不再是先选下拉再点新建。默认只展开一个文件夹，这里按 aria-expanded 决定要不要点开。
 */
export async function newSessionIn(
  page: Page,
  request: APIRequestContext,
  workspaceName?: string,
): Promise<string> {
  const response = await request.get(`${BASE}/api/workspaces`)
  const { workspaces } = (await response.json()) as { workspaces: WorkspaceItemWithName[] }
  const bound = workspaces.find((item) => item.is_default) ?? workspaces[0]
  if (!bound) throw new Error('服务端没有绑定任何工作地点')
  const name = workspaceName ?? bound.name ?? bound.root

  const folder = workspaceFolder(page, name)
  await expect(folder).toBeVisible()
  if ((await folder.getAttribute('aria-expanded')) === 'false') await folder.click()

  const created = page.waitForResponse(
    (res) => res.request().method() === 'POST' && res.url().endsWith('/api/sessions'),
  )
  await page.getByRole('button', { name: `在「${name}」新建会话` }).click()
  const settled = await created
  expect(settled.status(), `建会话失败：${await settled.text()}`).toBe(201)
  return ((await settled.json()) as { id: string }).id
}

// ---------------------------------------------------------------- 视觉断言辅助

/**
 * 读一个 token 的解析值。视觉断言一律走它拼期望值，**不写字面量**——
 * 阶段 23b 的教训：33 条钉死取值的 expect 会让「换视觉语言」必须改测试，
 * 于是测试反而不守任何东西了（它守的是"值没变"，而不是"机制还在"）。
 */
export async function token(page: Page, name: string): Promise<string> {
  return page.evaluate(
    (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim(),
    name,
  )
}

/** `201 123 63` + `70%` → 计算值里的写法 `rgba(201, 123, 63, 0.7)`。 */
export function rgba(triplet: string, percent: string): string {
  const [r, g, b] = triplet.split(/\s+/)
  return `rgba(${r}, ${g}, ${b}, ${Number.parseFloat(percent) / 100})`
}

/**
 * 拆开 box-shadow 的顶层逗号。颜色里也有逗号，**不能按逗号直接切**。
 * 返回顶层每一条；含 `inset` 的是玻璃高光带，其余是投影。
 */
export function shadows(boxShadow: string): string[] {
  if (boxShadow === 'none' || boxShadow === '') return []
  const parts: string[] = []
  let depth = 0
  let current = ''
  for (const char of boxShadow) {
    if (char === '(') depth += 1
    if (char === ')') depth -= 1
    if (char === ',' && depth === 0) {
      parts.push(current.trim())
      current = ''
    } else {
      current += char
    }
  }
  if (current.trim()) parts.push(current.trim())
  return parts
}

/**
 * 投影条数：高度由投影承担，高光带不算。
 *
 * 判 `inset` 用 includes 而不是 startsWith —— Chromium 把 `inset` 序列化在**每条阴影的
 * 末尾**（`rgba(...) 0px 1.5px 0px 0px inset`），按开头判会把高光带数成投影。
 */
export function lifts(boxShadow: string): number {
  return shadows(boxShadow).filter((part) => !part.includes('inset')).length
}

/** 亮度滤镜的系数；没有滤镜时给 1。 */
export function brightness(filter: string): number {
  const match = /brightness\(([\d.]+)\)/.exec(filter)
  return match ? Number(match[1]) : 1
}

/** 把 `rgb(r, g, b)` / `rgba(r, g, b, a)` 拆成通道与 alpha（不解析百分比写法）。 */
export function channels(color: string): { rgb: number[]; alpha: number } {
  const match = /rgba?\(\s*([\d.]+)[\s,]+([\d.]+)[\s,]+([\d.]+)\s*(?:[,/]\s*([\d.]+)\s*)?\)/.exec(
    color,
  )
  if (!match) throw new Error(`不是可解析的颜色：${color}`)
  return {
    rgb: [Number(match[1]), Number(match[2]), Number(match[3])],
    alpha: match[4] === undefined ? 1 : Number(match[4]),
  }
}

/**
 * WCAG 对比度（`(L1+0.05)/(L2+0.05)`）。**取计算色，不查 token 表**——
 * `check:contrast` 已经按 alpha 合成验过 token 之间的配对，这里要验的是另一件事：
 * 页面上那个元素的 `background-color` / `color` 真的是那对值（"类名写对、规则没生成"
 * 的空洞只有真的读 DOM 才看得见）。
 */
export function contrastOf(foreground: string, background: string): number {
  const luminance = (color: string) => {
    const { rgb } = channels(color)
    const [r, g, b] = rgb.map((channel) => {
      const c = channel / 255
      return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
    })
    return 0.2126 * r + 0.7152 * g + 0.0722 * b
  }
  const a = luminance(foreground)
  const b = luminance(background)
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05)
}

/** 两个矩形是否重叠（浮层"在组件外面"的判据）。 */
export function overlaps(
  a: { x: number; y: number; width: number; height: number },
  b: { x: number; y: number; width: number; height: number },
): boolean {
  return !(a.x + a.width <= b.x || b.x + b.width <= a.x || a.y + a.height <= b.y || b.y + b.height <= a.y)
}

/**
 * 等会话时间线真的渲染出内容（打开会话之后各用例的共同前提）。
 *
 * 判据是**结构**——至少一条条目 + 一枚动作按钮——而不是 `log.innerText` 的长度：
 * 长度里计入了动作按钮的可见文字（「复制文本」「从此处分支」），而动作行收成图标按钮
 * 之后那些字就没了（实测同一会话 56 → 45），于是"长度 > 50 就算加载好了"这条代理
 * 会随文案漂移：红的不是真的没渲染，而是少了几个字。等结构就不会被文案改动误伤。
 */
export async function waitForTimeline(page: Page): Promise<void> {
  const log = page.getByRole('log')
  await expect(log.locator('article').first(), '时间线至少渲染出一条条目').toBeVisible({
    timeout: 10_000,
  })
  await expect(
    log.getByRole('button', { name: '复制文本' }).first(),
    '条目动作行已渲染（复制是图标按钮，名字在 aria-label 上）',
  ).toBeVisible({ timeout: 10_000 })
}
