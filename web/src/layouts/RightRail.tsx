/**
 * 右栏的内容装配：**检查器（按需）+ 信息面板（常驻）**。
 *
 * 为什么不做成"二选一"（有检查器就盖掉信息面板）：参考截图里右栏是常驻的信息面，
 * 而检查器是"查看某次工具调用"的从属视图。两者回答的问题不同——一个是"我在哪个
 * 工作区、这个会话花了多少"，一个是"这次工具调用到底改了什么"。让它们互相顶替，
 * 用户看 diff 时就会丢掉会话上下文，而那个上下文常常正是判断 diff 对不对的依据。
 *
 * 空间分配：检查器用 `flex-1 min-h-0`（它自己内部滚动），信息面板 `shrink-0`
 * 按内容高度收在下方。这样窄一点时先压检查器，信息面板不会先被截断。
 */

import type { ReactElement } from 'react'

import { Inspector } from '../features/inspector'
import type { InspectorSelection, InspectorTab } from '../features/inspector'
import { InfoRail } from '../features/workspace'
import type { InfoRailProps } from '../features/workspace'

export interface RightRailProps {
  /** 当前选中的工具调用；null = 没开检查器（此时只显示信息面板）。 */
  selection: InspectorSelection | null
  tab: InspectorTab
  onTabChange: (tab: InspectorTab) => void
  onCloseInspector: () => void
  /** 信息面板的全部 props（由页面给，这一层不重新解释它们）。 */
  info: InfoRailProps
}

export function RightRail({
  selection,
  tab,
  onTabChange,
  onCloseInspector,
  info,
}: RightRailProps): ReactElement {
  return (
    <div className="flex h-full min-h-0 w-full flex-col bg-card">
      {selection === null ? null : (
        <div className="min-h-0 flex-1 overflow-hidden border-b-hair">
          <Inspector selection={selection} tab={tab} onTabChange={onTabChange} onClose={onCloseInspector} />
        </div>
      )}
      {/*
        信息面板始终在底部：它答的是"我在哪、花了多少"，与检查器要看的
        "这次调用改了什么"是两个不同的问题，互相顶替会让 diff 失去判断依据。
      */}
      <div className="shrink-0 overflow-y-auto">
        <InfoRail {...info} />
      </div>
    </div>
  )
}
