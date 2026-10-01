/**
 * 工作区域三个端点：登记候选、移除候选、调系统文件夹选择器。
 *
 * 为什么不并进 `api/sessions.ts`：那份文件的对齐物是 `docs/guide/web-ui.md` §2 的
 * **会话**端点表，工作区是另一张表；混在一起之后，"会话端点有没有漏"这件事
 * 就没法靠读一个文件回答。`fetchWorkspaces`（GET）留在 `sessions.ts` 里不动——
 * 它先于本文件存在，且已经是 Lead 状态层的依赖，搬动只会制造无意义的冲突。
 *
 * 纪律（与 `client.ts` 一致）：这里是**端点封装层**，只拼路径、拆壳、透传 signal；
 * 不缓存、不重试、不吞错误。所有函数都接 `AbortSignal`——弹窗关掉时用户不想
 * 再等一个选到一半的目录（真实场景：系统对话框开着，用户先关了页面）。
 */

import { request } from './client'
import type { PickFolderResult, WorkspaceSummary } from './types'

/**
 * 登记载荷。
 *
 * 字段与 `src/avid/web/schemas.py:107` 的 `CreateWorkspaceIn` 逐项对齐
 * （`model_config = extra="forbid"`，所以**不要**在这里多塞字段：多一个键服务端直接 422）。
 * `permission` 只收 `manual` / `auto`：`full` 是每次运行的显式授权，不能成为一个
 * 工作区的默认值，服务端用 `Literal["manual", "auto"]` 把这条钉死（`schemas.py:115`）。
 */
export interface CreateWorkspaceInput {
  path: string
  name?: string
  permission?: 'manual' | 'auto'
}

/**
 * 打开**本机**的系统文件夹选择器（`POST /api/workspaces/pick`，无请求体）。
 *
 * 为什么这件事必须由后端做：浏览器拿不到目录的绝对路径——`<input webkitdirectory>`
 * 只给相对路径，File System Access 只给一个 handle。而登记工作区需要绝对路径，
 * 所以"选择文件夹"唯一可行的实现是让跑在本机的后端弹对话框
 * （`src/avid/web/routes/workspaces.py:39`）。
 *
 * 返回 `{ path: null }` = **用户点了取消**，不是错误（`schemas.py:98` 的
 * `PickFolderOut` 注释把这条写死了）。调用方必须静默处理：把取消当失败弹红字，
 * 会让每一次"点开看一眼又关掉"都留下一条假错误。
 */
export function fetchPickFolder(signal?: AbortSignal): Promise<PickFolderResult> {
  return request<PickFolderResult>('/workspaces/pick', { method: 'POST', signal })
}

/**
 * 登记一个工作区（`POST /api/workspaces`，201 → `WorkspaceOut`）。
 *
 * 只把有值的键放进请求体：`body: {name: undefined}` 会被 `JSON.stringify` 丢掉，
 * 但显式 `null` 会真的发出去，而 `name` / `permission` 的语义是"没给"而不是"设成空"。
 *
 * 服务端的三种失败各有稳定码，界面按码分支（不按文案）：
 *   · 400 `workspace_invalid` —— 路径不存在 / 不是目录 / 权限档非法；
 *   · 409 `workspace_exists` —— 已登记（`detail` 带已有 id / name / root）；
 *   · 503 `picker_unavailable` —— 只可能出现在 pick 上，这里偶发也会透传上来。
 */
export function createWorkspace(
  input: CreateWorkspaceInput,
  signal?: AbortSignal,
): Promise<WorkspaceSummary> {
  const body: Record<string, unknown> = { path: input.path }
  if (input.name !== undefined) body.name = input.name
  if (input.permission !== undefined) body.permission = input.permission
  return request<WorkspaceSummary>('/workspaces', { method: 'POST', body, signal })
}

/**
 * 从候选列表里摘掉一个工作区（`DELETE /api/workspaces/{id}`，204 无正文）。
 *
 * 服务端语义值得写在这里，因为界面的确认文案要照着说：**只摘登记，不删会话数据**
 * （`routes/workspaces.py:32`）。想删进程绑定的那一个会拿到 409 `workspace_bound`——
 * 界面靠 `is_default` 提前禁用按钮，但竞态下仍可能撞上，所以错误也要处理。
 */
export function deleteWorkspace(id: string, signal?: AbortSignal): Promise<void> {
  return request<void>(`/workspaces/${encodeURIComponent(id)}`, { method: 'DELETE', signal })
}
