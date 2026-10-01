/**
 * 项目卡（侧栏 · 可收回）：一个「项目」就是一个选定的工作区——项目行即
 * 工作区候选（参考图形态：文件夹图标 + 名称的紧凑单行）。选中项是新会话
 * 将使用的工作区；会话归属在创建时绑定，现有会话只读（归属行打「当前会话」
 * 标记）。标题行：⊕ 新增（走宿主机文件夹选择器，不可用时按钮禁用并说明）
 * + chevron 收起/展开（可收回）。放在会话搜索框上方；右栏不再有工作区卡。
 */

import { useState } from 'react'

import type { WorkspaceSummary } from '../../api/types'
import { cx } from '../../ui/cx'
import { Icon } from '../../ui/Icon'
import { useAutoHideScroll } from '../../ui/useAutoHideScroll'

function basename(root: string): string {
  const parts = root.split(/[\\/]/).filter(Boolean)
  return parts[parts.length - 1] ?? root
}

export type ProjectCardProps = {
  /** 注册过的工作区候选；null = 还在加载。 */
  workspaces: WorkspaceSummary[] | null
  activeWorkspaceId: string | null
  /** 选中会话归属的工作区 id（打「当前会话」标记，只读）。 */
  sessionWorkspaceId?: string | null
  pickerAvailable?: boolean
  busy?: boolean
  hint?: string | null
  onSelectWorkspace?: (id: string) => void
  onAddByPicker?: () => void
}

export function ProjectCard({
  workspaces,
  activeWorkspaceId,
  sessionWorkspaceId = null,
  pickerAvailable = false,
  busy = false,
  hint = null,
  onSelectWorkspace,
  onAddByPicker,
}: ProjectCardProps) {
  const [open, setOpen] = useState(true)
  const listScrollRef = useAutoHideScroll<HTMLDivElement>()

  return (
    <div className="flex flex-col gap-a4">
      <div className="flex items-center justify-between">
        <span className="font-ui text-hint font-medium text-ink-muted">项目</span>
        <div className="flex items-center gap-a2">
          <button
            type="button"
            onClick={onAddByPicker}
            disabled={busy || !pickerAvailable}
            aria-label="新增项目"
            title={pickerAvailable ? '打开文件夹选择器' : '宿主机文件夹选择器不可用；可在 CLI 用 avid workspace add 登记'}
            className="inline-flex h-[22px] w-[22px] items-center justify-center rounded-sm text-ink-muted transition-colors duration-fast ease-out hover:bg-overlay-light hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
          >
            <Icon name="plus" size={12} />
          </button>
          <button
            type="button"
            onClick={() => setOpen((v) => !v)}
            aria-label={open ? '收起项目' : '展开项目'}
            title={open ? '收起项目' : '展开项目'}
            className="inline-flex h-[22px] w-[22px] items-center justify-center rounded-sm text-ink-muted transition-colors duration-fast ease-out hover:bg-overlay-light hover:text-ink"
          >
            <span className={cx('transition-transform duration-fast ease-out', open ? '' : '-rotate-90')}>
              <Icon name="chevron-down" size={12} />
            </span>
          </button>
        </div>
      </div>

      {open && (
        <div ref={listScrollRef} className="scroll-auto flex max-h-[200px] flex-col gap-a2 overflow-y-auto">
          {workspaces === null && <p className="px-a8 font-ui text-hint text-ink-muted">正在加载项目…</p>}
          {workspaces?.length === 0 && <p className="px-a8 font-ui text-hint text-ink-muted">还没有项目</p>}
          {workspaces?.map((ws) => {
            const active = ws.id === activeWorkspaceId
            return (
              <button
                key={ws.id}
                type="button"
                title={ws.root}
                onClick={() => onSelectWorkspace?.(ws.id)}
                className={cx(
                  'flex w-full items-center gap-a8 rounded-sm px-a8 py-a4 text-left transition-colors duration-fast ease-out',
                  active ? 'bg-accent-light' : 'hover:bg-overlay-light',
                )}
              >
                <span className={cx('shrink-0', active ? 'text-accent' : 'text-ink-light')}>
                  <Icon name="folder" size={14} />
                </span>
                <span className={cx('min-w-0 flex-1 truncate font-ui text-ui', active ? 'font-medium text-accent' : 'text-ink')}>
                  {ws.name ?? basename(ws.root)}
                </span>
                {ws.id === sessionWorkspaceId && (
                  <span className="shrink-0 font-ui text-micro text-ink-muted">当前会话</span>
                )}
                {active && (
                  <span className="shrink-0 text-accent">
                    <Icon name="check" size={12} />
                  </span>
                )}
              </button>
            )
          })}
        </div>
      )}

      {open && hint && <p className="px-a8 font-ui text-micro text-ink-light">{hint}</p>}
    </div>
  )
}
