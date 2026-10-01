/**
 * 工作区分组：一个可折叠的组头 + 组内会话列表。
 *
 * 分组用受控的 `<button aria-expanded>` 而不是 `<details>`：details 的展开动画
 * 与样式跨浏览器不一致，而这里要的只是"三角转 90° + 列表显隐"，两条都能走 token。
 *
 * `defaultOpen` 只取初始值（非受控）。为什么不做受控：折叠是纯视图状态，
 * 提交给上层只会让上层多持有一份可以自己算出来的状态——原描述也把 open 留在组件内。
 */

import { useId, useState } from 'react'
import type { ReactElement } from 'react'

import { ChevronRightIcon } from '../../../ui/icons'
import { Badge, cx } from '../../../ui/primitives'
import type { NavGroup } from '../lib/navTree'
import { SessionItem } from './SessionItem'

export interface WorkspaceFolderProps {
  group: NavGroup
  activeSessionId: string | null
  defaultOpen?: boolean
  onSelect: (id: string) => void
  onRename: (id: string, name: string) => void
  onDelete: (id: string) => void
  runningSessionIds: ReadonlySet<string>
  /** 相对时间的"现在"，透传给组内每一项；缺省则不显示时间。 */
  now?: number
}

export function WorkspaceFolder({
  group,
  activeSessionId,
  defaultOpen = true,
  onSelect,
  onRename,
  onDelete,
  runningSessionIds,
  now,
}: WorkspaceFolderProps): ReactElement {
  const [open, setOpen] = useState(defaultOpen)
  // useId 而不是把 group.key 拼进 id：key 可能是注册表里的 id 或路径，直接当
  // DOM id 会撞上非法字符与重复
  const listId = useId()

  return (
    <li>
      <button
        type="button"
        aria-expanded={open}
        aria-controls={listId}
        onClick={() => setOpen((previous) => !previous)}
        /* title 给完整路径：组名可能被截断，而路径是"这是哪个目录"的权威答案 */
        title={group.root ?? undefined}
        className="flex w-full items-center gap-a4 rounded-sm px-a8 py-a4 text-left transition-colors duration-fast hover:bg-accent-soft"
      >
        <ChevronRightIcon
          size={14}
          className={cx(
            'shrink-0 text-ink-faint transition-transform duration-fast',
            open && 'rotate-90',
          )}
        />
        <span className="min-w-0 flex-1 truncate text-caption text-ink-light">{group.label}</span>
        <Badge tone="neutral">{group.sessions.length}</Badge>
      </button>

      {open ? (
        <ul id={listId} className="mt-a2 flex flex-col gap-a2 pl-a8">
          {group.sessions.map((session) => (
            <SessionItem
              key={session.id}
              session={session}
              active={session.id === activeSessionId}
              running={runningSessionIds.has(session.id)}
              onSelect={onSelect}
              onRename={onRename}
              onDelete={onDelete}
              now={now}
            />
          ))}
        </ul>
      ) : null}
    </li>
  )
}
