/**
 * 右侧 dock（阶段 48 立，阶段 53 收成四面板）：三栏骨架里**占位**的右列，按需打开
 * （阶段 54 起默认收起，打开先给选择页——`choosing` 由页面持有）。
 *
 * 形态语义：它是 `AppShell` 的 `rail` 插槽内容，不是浮层——打开时主列让位、
 * 面板不盖住对话（配合的规则在骨架的栅格里：侧栏 240 ｜ 主列 minmax(0,1fr) ｜
 * 右列 280）。开合由页面决定（不挂这个组件，栅格自然回到两列），所以这里没有
 * `open` 形态；键盘只留 Esc 一条退出路径。
 * 面板注册表是单点：加面板 = 在 PANELS 加一个条目。Dock 是纯展示，数据由
 * 页面下发（会话快照与活运行的子运行），自己不发请求。
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

// 重面板按需加载：xterm 的体积隔离进异步 chunk（体积门禁分档计量）
const TerminalPanel = lazy(() => import('./TerminalPanel'))

// 面板注册表是单点：加一个面板 = 加一个条目 + 在本文件末尾挂上它的视图。
// hint 是列表里那一行说明（参考界面同形：图标 + 名称 + 一句话）。
const PANELS: { id: DockPanelId; label: string; hint: string; icon: IconName }[] = [
  { id: 'files', label: '工作区文件', hint: '浏览会话工作区的文件', icon: 'folder' },
  { id: 'subagents', label: '子智能体', hint: '看每个子任务自己干了什么', icon: 'bot' },
  { id: 'scratch', label: '临时对话', hint: '带主对话上下文的一次性只读支线', icon: 'message-square' },
  { id: 'terminal', label: '终端', hint: '在会话工作区运行命令', icon: 'terminal' },
]

export type DockProps = {
  active: DockPanelId
  /** 是否停在选择页（面板列表）：手动打开时的第一屏。 */
  choosing: boolean
  onChoose: (on: boolean) => void
  onSelect: (id: DockPanelId) => void
  onClose: () => void
  /** 终端面板的工作目录 / 文件面板要浏览的工作区：选中会话的工作区。 */
  workspaceRoot: string | null
  workspaceId: string | null
  /** 子智能体面板的数据：整条时间线里摊平出来的子运行（含只剩任务清单的历史项）。 */
  subagentRuns: SubagentRunView[]
  /** 这一轮还在跑：面板据此给运行中的标记。 */
  live: boolean
  /** 临时对话的上下文来源：当前选中的主会话。null = 还没有选中会话。 */
  sourceSessionId: string | null
  /** 这次运行用哪个模型（临时对话面板跟随主输入区的选择）。 */
  model: string | null
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
}: DockProps) {
  // 面板区自己滚（列只负责裁切），滚动条仍走「滚动时现形」那套。
  const scrollRef = useAutoHideScroll<HTMLDivElement>()
  // 选择页是"换面板"的入口，不是第四种面板：点条目进面板，面板头部再回列表。
  // 开还是关由页面持有（手动打开先给选择页），这里只管画。
  const listing = choosing
  const current = PANELS.find((p) => p.id === active) ?? PANELS[0]!
  // Esc 关闭：键盘要有一条不找鼠标的退出路径。收起时这个组件根本不挂（页面决定），
  // 所以监听常开。
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
          <ScratchPanel sourceSessionId={sourceSessionId} workspaceRoot={workspaceRoot} model={model} />
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
