/**
 * Project card (sidebar, collapsible): each row is a workspace candidate, and the selected one is
 * the workspace a new session will use — session ownership is bound at creation, so existing
 * sessions keep theirs and only show a "current session" marker.
 * The title row carries the add button (host folder picker, disabled with an explanation when
 * unavailable) and a chevron that collapses the list.
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
  /** Registered workspace candidates; null = still loading. */
  workspaces: WorkspaceSummary[] | null
  activeWorkspaceId: string | null
  /** Workspace id the selected session belongs to (gets the "current session" marker). */
  sessionWorkspaceId?: string | null
  pickerAvailable?: boolean
  busy?: boolean
  hint?: string | null
  onSelectWorkspace?: (id: string) => void
  onAddByPicker?: () => void
  /** Provide to get the "more → delete" affordance; without it the rows stay read-only. */
  onDeleteWorkspace?: (id: string) => void
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
  onDeleteWorkspace,
}: ProjectCardProps) {
  const [open, setOpen] = useState(true)
  /** Which row's "more" menu is open; only one at a time so delete buttons cannot pile up. */
  const [menuFor, setMenuFor] = useState<string | null>(null)
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
            const label = ws.name ?? basename(ws.root)
            const expanded = menuFor === ws.id
            return (
              <div key={ws.id} className="group flex flex-col">
                <div
                  className={cx(
                    'flex items-center rounded-sm transition-colors duration-fast ease-out',
                    active ? 'bg-accent-light' : 'hover:bg-overlay-light',
                  )}
                >
                  <button
                    type="button"
                    title={ws.root}
                    onClick={() => {
                      setMenuFor(null)
                      onSelectWorkspace?.(ws.id)
                    }}
                    className="flex min-w-0 flex-1 items-center gap-a8 rounded-sm px-a8 py-a4 text-left"
                  >
                    <span className={cx('shrink-0', active ? 'text-accent' : 'text-ink-light')}>
                      <Icon name="folder" size={14} />
                    </span>
                    <span className={cx('min-w-0 flex-1 truncate font-ui text-ui', active ? 'font-medium text-accent' : 'text-ink')}>
                      {label}
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
                  {/* "More": hidden until hover or focus, visible while expanded. */}
                  {onDeleteWorkspace && (
                    <button
                      type="button"
                      aria-label={`更多：${label}`}
                      aria-expanded={expanded}
                      data-testid="project-more"
                      title="更多"
                      onClick={() => setMenuFor(expanded ? null : ws.id)}
                      className={cx(
                        'mr-a4 inline-flex h-[20px] w-[20px] shrink-0 items-center justify-center rounded-sm text-ink-muted transition-opacity duration-fast ease-out hover:bg-overlay-medium hover:text-ink',
                        expanded ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 group-focus-within:opacity-100',
                      )}
                    >
                      <Icon name="more-horizontal" size={12} />
                    </button>
                  )}
                </div>

                {expanded && onDeleteWorkspace && (
                  <div className="mb-a2 ml-a8 flex items-center justify-between gap-a8 rounded-sm border-hairline border-hair bg-card px-a8 py-a4">
                    <span className="font-ui text-micro text-ink-muted">只从项目列表移除，会话文件留在磁盘</span>
                    <button
                      type="button"
                      aria-label={`删除 ${label}`}
                      data-testid="project-delete"
                      onClick={() => {
                        onDeleteWorkspace(ws.id)
                        setMenuFor(null)
                      }}
                      className="shrink-0 rounded-xs px-a8 py-[1px] font-ui text-micro text-danger transition-colors duration-fast ease-out hover:bg-overlay-light"
                    >
                      删除
                    </button>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {open && hint && <p className="px-a8 font-ui text-micro text-ink-light">{hint}</p>}
    </div>
  )
}
