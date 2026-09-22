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
