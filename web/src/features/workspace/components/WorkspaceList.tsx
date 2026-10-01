/**
 * 工作区候选列表：管理弹窗里的"行"。
 *
 * 组件边界（有意划在这里）：**不在这里做二次确认**。移除是不可逆动作，但确认弹窗
 * 需要"当前选中哪一项"这样的状态，那属于组装层（`WorkspaceManager`）。行组件只负责
 * 把"这一项现在能不能删、删了意味着什么"表达清楚——所以进程绑定的那一项按钮 disabled，
 * 并且 `title` 说明理由（服务端会以 409 `workspace_bound` 拒绝，界面提前拦住更好）。
 *
 * 权限徽标只说两档：服务端 `Literal["manual", "auto"]`，别的值一律不显示徽标
 * （见 `lib/labels.ts` 的 `permissionLabel`）——显示一个猜出来的档位比不显示更糟。
 */

import type { ReactElement } from 'react'

import type { WorkspaceSummary } from '../../../api/types'
import { PlusIcon, TrashIcon } from '../../../ui/icons'
import { Badge, Button } from '../../../ui/primitives'
import { permissionLabel, workspaceLabel } from '../lib/labels'

export interface WorkspaceListProps {
  workspaces: WorkspaceSummary[]
  /** 进程绑定的工作区 id（`is_default` 为 true 的那个）：它删不掉，按钮要禁用并说明。 */
  onRemove: (id: string) => void
  onAdd: () => void
  busy?: boolean
}

export function WorkspaceList({
  workspaces,
  onRemove,
  onAdd,
  busy = false,
}: WorkspaceListProps): ReactElement {
  return (
    <div className="flex flex-col gap-a8">
      {workspaces.length === 0 ? (
        <p className="text-ui text-ink-muted">还没有登记任何工作区</p>
      ) : (
        <ul className="flex flex-col gap-a6">
          {workspaces.map((workspace) => {
            const label = workspaceLabel(workspace)
            const permission = permissionLabel(workspace.default_permission)
            return (
              <li
                key={workspace.id}
                /* `border-hair` 一次 = 0.5px 线宽 + 发丝线色（同名 token 分属两个轴）。
                   叠方向变体不会有线色，叠彩色类会被字母序覆盖——都别写。 */
                className="flex items-start gap-a8 rounded-sm border-hair bg-card px-a10 py-a8"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-a6">
                    <span className="min-w-0 truncate text-ui text-ink">{label}</span>
                    {workspace.is_default ? <Badge tone="info">进程绑定</Badge> : null}
                    {permission === null ? null : <Badge tone="neutral">{permission}</Badge>}
                  </div>
                  <div className="truncate text-hint text-ink-muted" title={workspace.root}>
                    {workspace.root}
                  </div>
                </div>
                <Button
                  size="icon"
                  variant="ghost"
                  icon={<TrashIcon />}
                  aria-label={`移除工作区 ${label}`}
                  title={
                    workspace.is_default
                      ? '这是进程绑定的工作区，不能移除'
                      : '只从候选列表摘掉，不删会话数据'
                  }
                  disabled={workspace.is_default || busy}
                  onClick={() => onRemove(workspace.id)}
                />
              </li>
            )
          })}
        </ul>
      )}

      {/* 添加入口放在列表之后：列表为空时它就是页面上唯一的动作，不必再找一遍。 */}
      <div>
        <Button size="sm" variant="secondary" icon={<PlusIcon />} onClick={onAdd}>
          添加工作区
        </Button>
      </div>
    </div>
  )
}
