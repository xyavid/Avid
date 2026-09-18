import type { SessionSummary, WorkspaceSummary } from '../../../api/types'

/**
 * 导航树：工作区像文件夹，会话装在它里面。
 *
 * 这里全是纯函数——分组、可见条数、默认展开谁都是可以单独验的规则，组件只负责画。
 * 服务端已经排好序（工作区：绑定值在最前，其余按最近使用；会话：从新到旧），
 * 所以这里**只按服务端顺序归拢，不重新排序**：排序口径只有一个来源。
 */

/** 一个工作区分组。`workspace` 为 null = 归属查不到（防御性兜底，见 `groupByWorkspace`）。 */
export interface WorkspaceGroup {
  workspace: WorkspaceSummary | null
  sessions: SessionSummary[]
}

/** 折叠状态下每个工作区先露几个会话——再多就用「展开其余 N 个会话」。 */
export const PREVIEW_SESSIONS = 5

/**
 * 按工作区把会话收拢成组，顺序与服务端一致。
 *
 * 正常情况下每条会话的归属都能在候选列表里找到（`svc` 的列举与候选来自同一份
 * `workspaces()`）。真出现找不到的情况（例如注册表被外部改过），**不丢掉它**：
 * 归到最后一个 `workspace: null` 的组里显示，比让它凭空消失好。
 */
export function groupByWorkspace(
  workspaces: WorkspaceSummary[],
  sessions: SessionSummary[],
): WorkspaceGroup[] {
  const groups: WorkspaceGroup[] = workspaces.map((workspace) => ({
    workspace,
    sessions: [],
  }))
  const byId = new Map<string, WorkspaceGroup>()
  groups.forEach((group) => {
    if (group.workspace) byId.set(group.workspace.id, group)
  })

  const orphans: SessionSummary[] = []
  for (const session of sessions) {
    const ownerId = session.workspace?.id
    const group = ownerId ? byId.get(ownerId) : undefined
    if (group) group.sessions.push(session)
    else orphans.push(session)
  }

  if (orphans.length > 0) groups.push({ workspace: null, sessions: orphans })
  return groups
}

/** 展开状态下最多显示几个；`hidden` 是还剩几个没显示。 */
export function visibleSessions(
  sessions: SessionSummary[],
  expanded: boolean,
): { shown: SessionSummary[]; hidden: number } {
  if (expanded || sessions.length <= PREVIEW_SESSIONS) {
    return { shown: sessions, hidden: 0 }
  }
  return { shown: sessions.slice(0, PREVIEW_SESSIONS), hidden: sessions.length - PREVIEW_SESSIONS }
}

/**
 * 默认展开哪个工作区：装着当前会话的那个；没有当前会话就展开第一个**有会话**的；
 * 都没有就展开第一个（空工作区也要露出"还没有会话 + 新建"，否则新机器上界面像空的）。
 */
export function defaultExpandedWorkspace(
  groups: WorkspaceGroup[],
  activeSessionId: string | null,
): string | null {
  if (activeSessionId !== null) {
    const owner = groups.find((group) =>
      group.sessions.some((session) => session.id === activeSessionId),
    )
    if (owner?.workspace) return owner.workspace.id
  }
  const firstWithSessions = groups.find((group) => group.sessions.length > 0)
  return (firstWithSessions ?? groups[0])?.workspace?.id ?? null
}

/**
 * 实际是否展开：用户的显式操作优先（`toggled` 里记着），否则回落到默认。
 *
 * 用"覆盖表 + 默认值"而不是把默认值写进 state：数据是异步来的，`useState` 的初值
 * 看不到它们；这样列表刷新后默认展开也跟着变，而用户手动收起的那个仍然保持收起。
 */
export function isExpanded(
  workspaceId: string,
  toggled: Record<string, boolean>,
  defaultId: string | null,
): boolean {
  return toggled[workspaceId] ?? workspaceId === defaultId
}
