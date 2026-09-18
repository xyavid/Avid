import type { WorkspaceSummary } from '../../../api/types'

/**
 * 新建会话的缺省工作区。
 *
 * 优先用户当前选中的那个（它可能因为列表刷新而不存在了）；否则用服务端标了
 * `is_default` 的（只有单工作区模式会给）；再否则取列表第一个——服务端已按
 * 「默认最前、其余最近使用」排序，所以第一个就是「最近用过的那一个」。
 * 列表为空返回 null，调用方据此禁用新建按钮（多工作区模式下没有工作区可选时，
 * 建会话必然 400 `workspace_required`，不该让按钮点出一个已知会失败的请求）。
 */
export function pickWorkspace(
  workspaces: WorkspaceSummary[],
  preferredId: string | null,
): WorkspaceSummary | null {
  if (preferredId !== null) {
    const preferred = workspaces.find((item) => item.id === preferredId)
    if (preferred) return preferred
  }
  return workspaces.find((item) => item.is_default) ?? workspaces[0] ?? null
}
