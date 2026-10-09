/**
 * Three-column shell: titlebar 44px | sidebar 240px | conversation column <=720px | rail 280px.
 * Column widths are user preferences persisted and clamped in `state/columns.ts`; the
 * conversation column keeps at least `MAIN_MIN_WIDTH`, and a narrow viewport collapses the rail
 * before the sidebar.
 */

import { useRef } from 'react'
import type { KeyboardEvent, PointerEvent as ReactPointerEvent, ReactNode } from 'react'

import type { ColumnId, ColumnWidths } from '../state/columns'
import { clampWidths, useColumns, useViewportWidth } from '../state/columns'
import { AvidMark } from '../ui/Mark'
import { cx } from '../ui/cx'

function RegionNote({ children }: { children: ReactNode }) {
  return <span className="font-mono text-micro text-ink-muted">{children}</span>
}

export type AppShellProps = {
  sidebar?: ReactNode
  main?: ReactNode
  rail?: ReactNode
  /** Optional header actions on the right; settings live in the sidebar footer. */
  actions?: ReactNode
}

export function AppShell({ sidebar, main, rail, actions }: AppShellProps) {
  const { widths, resize, reset } = useColumns()
  const viewport = useViewportWidth()
  const gridRef = useRef<HTMLDivElement>(null)
  const hasRail = rail !== undefined
  const shown = clampWidths(widths, viewport, hasRail)

  const template = (value: ColumnWidths) =>
    hasRail ? `${value.sidebar}px minmax(0,1fr) ${value.rail}px` : `${value.sidebar}px minmax(0,1fr)`

  /** Column widths track pointer delta: sidebar grows rightward, rail grows leftward. */
  const beginDrag = (id: ColumnId) => (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.button !== 0) return
    event.preventDefault()
    const startX = event.clientX
    const start = shown[id]
    let wanted = start
    const move = (moveEvent: PointerEvent) => {
      const delta = id === 'sidebar' ? moveEvent.clientX - startX : startX - moveEvent.clientX
      wanted = start + delta
      // Live drag mutates the grid template only (clamped), never React state.
      if (gridRef.current !== null) {
        gridRef.current.style.gridTemplateColumns = template(
          clampWidths({ ...shown, [id]: wanted }, viewport, hasRail),
        )
      }
    }
    const finish = () => {
      window.removeEventListener('pointermove', move)
      window.removeEventListener('pointerup', finish)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      // Persist the wanted width, not the clamped one: the preference survives viewport growth.
      resize(id, wanted)
    }
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', finish)
  }

  /** Keyboard: arrow key 16px, Shift+arrow 48px. */
  const nudge = (id: ColumnId) => (event: KeyboardEvent<HTMLDivElement>) => {
    const grow = id === 'sidebar' ? 'ArrowRight' : 'ArrowLeft'
    const shrink = id === 'sidebar' ? 'ArrowLeft' : 'ArrowRight'
    const step = (event.shiftKey ? 48 : 16) * (event.key === grow ? 1 : event.key === shrink ? -1 : 0)
    if (step === 0) return
    event.preventDefault()
    resize(id, shown[id] + step)
  }

  return (
    <div className="grid h-dvh grid-rows-[var(--titlebar-h)_minmax(0,1fr)] bg-paper font-ui text-ink">
      <header className="flex items-center justify-between border-b border-hair px-a16">
        {/* 22px aligns the mark with the serif wordmark's cap height. */}
        <span className="flex items-center gap-a8">
          <AvidMark size={22} />
          <span className="font-serif text-title tracking-[0.01em]">Avid</span>
        </span>
        {actions}
      </header>

      <div
        ref={gridRef}
        className="grid min-h-0"
        style={{ gridTemplateColumns: template(shown) }}
      >
        <aside className="relative flex min-h-0 flex-col border-r border-hair bg-sidebar p-a12">
          {sidebar ?? <RegionNote>侧栏 240px · 会话列表占位</RegionNote>}
          <ColumnHandle
            side="right"
            label="调整侧栏宽度"
            onPointerDown={beginDrag('sidebar')}
            onKeyDown={nudge('sidebar')}
            onDoubleClick={() => reset('sidebar')}
          />
        </aside>

        <main className="min-h-0 overflow-hidden">{main ?? <RegionNote>对话列 ≤720px · 阶段 4 组装</RegionNote>}</main>

        {/* Rail is grid + clipping only; its content owns borders, padding and scrolling. */}
        {rail && (
          <aside className="relative min-h-0 overflow-hidden">
            {rail}
            <ColumnHandle
              side="left"
              label="调整侧边栏宽度"
              onPointerDown={beginDrag('rail')}
              onKeyDown={nudge('rail')}
              onDoubleClick={() => reset('rail')}
            />
          </aside>
        )}
      </div>
    </div>
  )
}

/**
 * Column-edge drag handle over the hairline, shown on hover/focus: arrows and Shift+arrows
 * move 16/48px, double-click resets to the layout token default.
 */
function ColumnHandle({
  side,
  label,
  onPointerDown,
  onKeyDown,
  onDoubleClick,
}: {
  side: 'left' | 'right'
  label: string
  onPointerDown: (event: ReactPointerEvent<HTMLDivElement>) => void
  onKeyDown: (event: KeyboardEvent<HTMLDivElement>) => void
  onDoubleClick: () => void
}) {
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      tabIndex={0}
      onPointerDown={onPointerDown}
      onKeyDown={onKeyDown}
      onDoubleClick={onDoubleClick}
      className={cx(
        'absolute inset-y-0 z-10 w-[7px] cursor-col-resize transition-colors duration-fast ease-out hover:bg-accent/25 focus-visible:bg-accent/40 focus:outline-none',
        side === 'right' ? '-right-[4px]' : '-left-[4px]',
      )}
    />
  )
}
