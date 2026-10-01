/**
 * 会话列表项（报告 §7.1 的标志性做法，不许省）。
 *
 * 三处刻意决定：
 *
 * 1) 行内操作（改名 / 删除）默认 `opacity-0`，`hover` 或 `focus-within` 才淡入——
 *    "隐藏直到需要"，列表在没被碰的时候保持安静。用 `focus-within`（不是 `focus`）
 *    是因为键盘用户 Tab 进操作区时，父容器必须一起点亮。
 * 2) 隐藏期间同时给 `pointer-events-none`：否则行右侧那块看不见的"删除"是活的，
 *    点上去直接弹销毁确认——不可见但可点是最糟的组合。键盘不受 pointer-events
 *    影响，所以 Tab 仍能聚焦并触发 focus-within。
 * 3) 只淡入**不改宽度**：hana 是 0→40px 展开，那会让标题在 hover 的瞬间重新截断，
 *    整行文字跳一下。这里宁可牺牲一点"展开感"，换列表静止时的稳定。
 *
 * 重命名就地编辑而不是弹窗：改名是高频小动作，弹窗的代价（焦点迁移 + 遮罩层）
 * 比收益大；双击标题或点「改名」都进编辑态。
 */

import { useEffect, useRef, useState } from 'react'
import type { ReactElement } from 'react'

import type { SessionSummary } from '../../../api/types'
import { TrashIcon } from '../../../ui/icons'
import { Button, cx } from '../../../ui/primitives'
import { sessionLabel } from '../lib/navTree'
import { relativeTime } from '../lib/relativeTime'

export interface SessionItemProps {
  session: SessionSummary
  active: boolean
  onSelect: (id: string) => void
  onRename: (id: string, name: string) => void
  onDelete: (id: string) => void
  /** 该会话是否有活动 run（流式中：显示 5px accent 圆点） */
  running: boolean
  /** 相对时间的"现在"；由上层给，便于测试。缺省不显示时间。 */
  now?: number
  /**
   * 所属工作区的显示标签（`sessionWorkspaceLabel` 的结果）。
   *
   * 本轮分组从"按工作区"改成"按时间"，工作区信息下沉成行内的小标记：
   * 不传就不画标记（组件不知道也不猜归属），传了就在元信息行右侧出现一个安静的凹槽。
   */
  workspaceLabel?: string
}

export function SessionItem({
  session,
  active,
  onSelect,
  onRename,
  onDelete,
  running,
  now,
  workspaceLabel,
}: SessionItemProps): ReactElement {
  const label = sessionLabel(session)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(label)
  const inputRef = useRef<HTMLInputElement>(null)
  /**
   * 一次编辑只提交一次。
   *
   * Enter 提交会把 input 卸载，随后可能再走一次 blur（浏览器在元素被移除时的
   * 行为不完全一致），没有这道闸就会重复调用 onRename；Esc 取消也要把它置真，
   * 否则取消后紧接着的 blur 又把输入提交了。
   */
  const settledRef = useRef(false)

  useEffect(() => {
    if (!editing) return
    // 进入编辑态即全选：改名通常是从头覆盖，不该逼用户先手动选中旧名字
    inputRef.current?.focus()
    inputRef.current?.select()
  }, [editing])

  function startEdit(): void {
    settledRef.current = false
    setDraft(label)
    setEditing(true)
  }

  function commit(): void {
    if (settledRef.current) return
    settledRef.current = true
    const next = draft.trim()
    // 空名字不提交：清空输入框不该把会话变成未命名，那是个"静默毁数据"的操作
    if (next !== '' && next !== label) onRename(session.id, next)
    setEditing(false)
  }

  function cancel(): void {
    settledRef.current = true
    setEditing(false)
  }

  const actions = (
    <div className="pointer-events-none flex shrink-0 items-center gap-a2 opacity-0 transition-opacity duration-fast group-hover:pointer-events-auto group-hover:opacity-100 group-focus-within:pointer-events-auto group-focus-within:opacity-100">
      {/* 改名用文字按钮而不是图标：图标集里没有"编辑"语义的线性图标，
          用扳手（工具）或加号都会说错话；文字按钮在 240px 栏里也只占两字宽。 */}
      <Button
        variant="ghost"
        size="sm"
        type="button"
        aria-label={`重命名会话 ${label}`}
        onClick={startEdit}
      >
        改名
      </Button>
      <Button
        variant="ghost"
        size="icon"
        type="button"
        aria-label={`删除会话 ${label}`}
        onClick={() => onDelete(session.id)}
        icon={<TrashIcon size={14} />}
      />
    </div>
  )

  // aria-current 是全局 ARIA 属性，直接标在行容器上即可——它描述"这是当前项"，
  // 不需要额外的 role 来承载。
  return (
    <li className="group relative">
      <div
        aria-current={active ? 'true' : undefined}
        className={cx(
          'flex items-center gap-a4 rounded-sm px-a8 py-a6 transition-colors duration-fast',
          active ? 'bg-accent-soft' : 'hover:bg-accent-soft',
        )}
      >
        {running ? (
          <span
            role="img"
            aria-label="正在运行"
            className="h-[5px] w-[5px] shrink-0 rounded-full bg-accent"
          />
        ) : null}

        {editing ? (
          <input
            ref={inputRef}
            aria-label={`重命名会话 ${label}`}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault()
                commit()
              } else if (event.key === 'Escape') {
                event.preventDefault()
                cancel()
              }
            }}
            onBlur={commit}
            /* 线宽与线色同行：tailwind 里 `.border-hair` 同时输出 border-width 0.5px
               与 border-color（`borderWidth.hair` 与 `colors.hair` 同名，两条规则各管
               一个属性，互不覆盖）；分开写的方向变体只用于单边。
               实测命令：npx tailwindcss -c tailwind.config.js -i src/styles/tokens.css -o /tmp/x.css */
            className="min-w-0 flex-1 rounded-xs border-hair bg-card px-a4 py-a2 text-ui text-ink"
          />
        ) : (
          // 单击选中、双击改名；双击必然先触发两次 click（选中是幂等的），可接受
          <button
            type="button"
            onClick={() => onSelect(session.id)}
            onDoubleClick={startEdit}
            className="min-w-0 flex-1 text-left"
          >
            <span
              className={cx('block truncate text-ui', active ? 'font-medium text-accent-ink' : 'text-ink')}
            >
              {label}
            </span>
            {/*
              元信息行：条数 / 相对时间是"这条会话本身"的读数，工作区标记是"它属于哪"，
              两者靠右对齐的一小块凹槽分开。标记只截断不改行高——窄栏里换行会让每项高度不一，
              列表就会看起来在抖。
              `title` 给全名：240px 宽里工作区名几乎必然被截断，完整值得有地方能读到。
            */}
            <span className="mt-a2 flex min-w-0 items-center gap-a4">
              <span className="min-w-0 flex-1 truncate text-hint text-ink-faint">
                {/* now 缺省就不显示时间：本组件不该自己读时钟（渲染期读时钟不可测，
                    也会让同一行在两次渲染间显示不同文案），时钟归上层。 */}
                {session.message_count} 条
                {now === undefined ? null : ` · ${relativeTime(session.created_at, now)}`}
              </span>
              {workspaceLabel === undefined ? null : (
                <span
                  title={workspaceLabel}
                  className="max-w-[96px] shrink-0 truncate rounded-xs bg-inset px-a4 text-hint text-ink-muted"
                >
                  {workspaceLabel}
                </span>
              )}
            </span>
          </button>
        )}

        {actions}
      </div>
    </li>
  )
}
