/**
 * 导航列头部：栏标题 + 新建会话 + 收起/展开。
 *
 * 为什么收起/展开在这里而不是外壳层：这个按钮的图标、文案与 26px 尺寸都来自
 * 设计系统，摆在外壳层会把"导航怎么呈现"的决定漏进布局层——布局只该知道
 * `collapsed` 对应的宽度。所以导航的**全部**呈现与交互都留在本目录里。
 */

import type { ReactElement } from 'react'

import { PanelLeftIcon, PlusIcon } from '../../../ui/icons'
import { Button, cx } from '../../../ui/primitives'

export interface SessionListHeaderProps {
  onNewSession: () => void
  /** 收起 / 展开导航列；由外壳按 collapsed 给宽度，本按钮只翻转那个状态。 */
  onToggleCollapsed: () => void
  collapsed: boolean
  busy?: boolean
  /**
   * 栏标题文案。缺省「会话」（task-3 的既定契约，不改）。
   *
   * 做成可选参数而不是改字面量：参考截图里这一行是「对话」，而"导航列叫什么"是
   * **调用方**的决定（同一个头部也可以被别的列表复用），组件不该替它定死；
   * 缺省值保持原样，于是不传的调用点行为与之前完全一致。
   */
  title?: string
}

export function SessionListHeader({
  onNewSession,
  onToggleCollapsed,
  collapsed,
  busy = false,
  title = '会话',
}: SessionListHeaderProps): ReactElement {
  return (
    <div
      className={cx(
        'flex min-w-0 flex-1 items-center gap-a2',
        collapsed ? 'justify-center' : 'justify-between',
      )}
    >
      {collapsed ? null : (
        <h2 className="min-w-0 flex-1 truncate text-caption font-medium text-ink-muted">{title}</h2>
      )}

      {/* loading 走 Button 自己的 loading：它在禁用按钮的同时把图标换成转圈，
          这里不再另加"正在加载"文案——栏宽只有 240px，多一行字会把列表挤下去。 */}
      <Button
        variant="ghost"
        size="icon"
        type="button"
        aria-label="新建会话"
        loading={busy}
        onClick={onNewSession}
        icon={<PlusIcon size={14} />}
      />

      {/* aria-expanded 表达的是"它所控制的那块内容是否展开"，所以取反；
          aria-label 跟着状态换词，读屏用户不必从图标猜当前是收还是展。 */}
      <Button
        variant="ghost"
        size="icon"
        type="button"
        aria-expanded={!collapsed}
        aria-label={collapsed ? '展开会话列表' : '收起会话列表'}
        onClick={onToggleCollapsed}
        icon={<PanelLeftIcon size={14} />}
      />
    </div>
  )
}
