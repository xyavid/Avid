import type { APIRequestContext, APIResponse } from '@playwright/test'

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
