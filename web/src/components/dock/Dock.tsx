/**
 * Right dock: the on-demand right column of the AppShell `rail` slot, not an overlay — opening it
 * lets the main column yield (sidebar 240 | main minmax(0,1fr) | rail 280); panel ids live in both
 * `state/dock.ts` and `PANELS` below and must stay in sync.
 * Presentational only: the page owns open/close (collapsed = not mounted, so the grid falls back
 * to two columns) and hands down all data, so there is no `open` prop and no request here.
 */

import { lazy, Suspense, useEffect } from 'react'

import type { SubagentRunView } from '../../state/timeline'
import type { DockPanelId } from '../../state/dock'
import { IconButton } from '../../ui/IconButton'
import { Icon, type IconName } from '../../ui/Icon'
import { cx } from '../../ui/cx'
import { useAutoHideScroll } from '../../ui/useAutoHideScroll'
import { FilesPanel } from './FilesPanel'
import { ScratchPanel } from './ScratchPanel'
import { SubagentPanel } from './SubagentPanel'

// Heavy panels load on demand: xterm stays isolated in its own async chunk.
const TerminalPanel = lazy(() => import('./TerminalPanel'))

// Adding a panel = one entry here + its view at the bottom of this file; hint is the list line.
const PANELS: { id: DockPanelId; label: string; hint: string; icon: IconName }[] = [
  { id: 'files', label: '工作区文件', hint: '浏览会话工作区的文件', icon: 'folder' },
  { id: 'subagents', label: '子智能体', hint: '看每个子任务自己干了什么', icon: 'bot' },
  { id: 'scratch', label: '临时对话', hint: '带主对话上下文的一次性只读支线', icon: 'message-square' },
  { id: 'terminal', label: '终端', hint: '在会话工作区运行命令', icon: 'terminal' },
]

export type DockProps = {
  active: DockPanelId
  /** True while on the chooser page (panel list) — the first screen after a manual open. */
  choosing: boolean
  onChoose: (on: boolean) => void
  onSelect: (id: DockPanelId) => void
  onClose: () => void
  /** Working directory (terminal) / browsed workspace (files panel): the selected session's. */
  workspaceRoot: string | null
  workspaceId: string | null
  /** Subagent runs flattened from the whole timeline (including history with only a task list). */
  subagentRuns: SubagentRunView[]
  /** The current turn is still running: panels use it for the running mark. */
  live: boolean
  /** Context source for the scratch chat: the selected main session; null = none selected. */
  sourceSessionId: string | null
  /** Model and effort for this run; the scratch panel follows the main composer's choice. */
  model: string | null
  effort: string | null
}

export function Dock({
  active,
  choosing,
  onChoose,
  onSelect,
  onClose,
  workspaceRoot,
  workspaceId,
  subagentRuns,
  live,
  sourceSessionId,
  model,
  effort,
}: DockProps) {
  // The panel body scrolls itself (the column only clips) via the auto-hiding scrollbar.
  const scrollRef = useAutoHideScroll<HTMLDivElement>()
  // The chooser switches panels (not a fourth panel); open/close state lives with the page.
  const listing = choosing
  const current = PANELS.find((p) => p.id === active) ?? PANELS[0]!
  // Esc closes: the dock is unmounted while collapsed, so the listener can stay always-on.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <aside aria-label="侧边栏" className="flex h-full min-h-0 flex-col border-l border-hair bg-card">
      <div className="flex items-center gap-a4 border-b border-hair px-a8 py-a6">
        {listing ? (
          <span className="flex-1 px-a4 font-ui text-ui text-ink">面板</span>
        ) : (
          <button
            type="button"
            aria-label="回到面板列表"
            title="面板列表"
            onClick={() => onChoose(true)}
            className="flex min-w-0 flex-1 items-center gap-a6 rounded-sm px-a4 py-a6 text-left transition-colors duration-fast ease-out hover:bg-overlay-light"
          >
            <span aria-hidden className="shrink-0 rotate-90 text-ink-muted">
              <Icon name="chevron-down" size={12} />
            </span>
            <span className="shrink-0 text-accent">
              <Icon name={current.icon} size={14} />
            </span>
            <span className="min-w-0 truncate font-ui text-ui text-ink">{current.label}</span>
          </button>
        )}
        <IconButton icon="x" label="关闭侧边栏" onClick={onClose} />
      </div>

      {listing ? (
        <nav className="flex flex-col gap-a8 p-a12" aria-label="面板列表">
          {PANELS.map((panel) => (
            <button
              key={panel.id}
              type="button"
              onClick={() => onSelect(panel.id)}
              className={cx(
                'flex items-start gap-a12 rounded-card border-hairline border-hair px-a12 py-a12 text-left transition-colors duration-fast ease-out hover:bg-overlay-light',
                active === panel.id && 'bg-overlay-light',
              )}
            >
              <span className="mt-[1px] shrink-0 text-accent">
                <Icon name={panel.icon} size={16} />
              </span>
              <span className="flex min-w-0 flex-col">
                <span className="font-ui text-ui text-ink">{panel.label}</span>
                <span className="font-ui text-hint text-ink-muted">{panel.hint}</span>
              </span>
            </button>
          ))}
        </nav>
      ) : (
      <div ref={scrollRef} className="scroll-auto min-h-0 flex-1 overflow-y-auto">
        {active === 'files' && <FilesPanel workspaceId={workspaceId} root={workspaceRoot} />}
        {active === 'subagents' && (
          <SubagentPanel runs={subagentRuns} live={live} workspaceRoot={workspaceRoot} />
        )}
        {active === 'scratch' && (
          <ScratchPanel
            sourceSessionId={sourceSessionId}
            workspaceRoot={workspaceRoot}
            model={model}
            effort={effort}
          />
        )}
        {active === 'terminal' && (
          <div className="h-full p-a12">
            <Suspense fallback={<p className="font-ui text-hint text-ink-muted">加载终端组件…</p>}>
              <TerminalPanel root={workspaceRoot} />
            </Suspense>
          </div>
        )}
      </div>
      )}
    </aside>
  )
}
