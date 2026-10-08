/**
 * 右侧 dock（阶段 48 立，阶段 49 加重面板）：三栏骨架里**占位**的右列，常驻。
 *
 * 形态语义：它是 `AppShell` 的 `rail` 插槽内容，不是浮层——展开时主列让位、
 * 面板不盖住对话（配合的规则在骨架的栅格里：侧栏 240 ｜ 主列 minmax(0,1fr) ｜
 * 右列 280）。收起由页面决定（不挂这个组件，栅格自然回到两列），所以这里没有
 * `open` 形态；键盘只留 Esc 一条退出路径。
 * 面板注册表是单点：加面板 = 在 PANELS 加一个条目。Dock 是纯展示，数据由
 * 页面下发（useRunStream 的活状态与会话快照），自己不发请求。
 */

import { lazy, Suspense, useEffect, useState } from 'react'

import type { LiveApproval, LiveTool, RunPhase } from '../../state/useRunStream'
import type { DockPanelId } from '../../state/dock'
import { IconButton } from '../../ui/IconButton'
import { Icon, type IconName } from '../../ui/Icon'
import { cx } from '../../ui/cx'
import { useAutoHideScroll } from '../../ui/useAutoHideScroll'
import { ApprovalBar } from '../chat/ApprovalBar'
import { FilesPanel } from './FilesPanel'

// 重面板按需加载：xterm 的体积隔离进异步 chunk（体积门禁分档计量）
const TerminalPanel = lazy(() => import('./TerminalPanel'))

// 面板注册表是单点：加一个面板 = 加一个条目 + 在本文件末尾挂上它的视图。
// hint 是列表里那一行说明（参考界面同形：图标 + 名称 + 一句话）。
const PANELS: { id: DockPanelId; label: string; hint: string; icon: IconName }[] = [
  { id: 'files', label: '工作区文件', hint: '浏览会话工作区的文件', icon: 'folder' },
  { id: 'processes', label: '进程', hint: '运行状态、工具与待决审批', icon: 'activity' },
  { id: 'review', label: '审查', hint: '待决审批在这里答复', icon: 'shield-check' },
  { id: 'terminal', label: '终端', hint: '在会话工作区运行命令', icon: 'terminal' },
]

export type DockProps = {
  active: DockPanelId
  onSelect: (id: DockPanelId) => void
  onClose: () => void
  /** 面板数据：活运行状态（无活运行时 phase 为 null）。 */
  phase: RunPhase | null
  tools: LiveTool[]
  approvals: LiveApproval[]
  onDecide: (approvalId: string, decision: 'allow' | 'deny') => void
  /** 终端面板的工作目录 / 文件面板要浏览的工作区：选中会话的工作区。 */
  workspaceRoot: string | null
  workspaceId: string | null
}

const PHASE_LABEL: Record<RunPhase, string> = {
  idle: '空闲',
  starting: '启动中',
  running: '运行中',
  settling: '收尾中',
  error: '出错',
}

/** ZCode 式状态行：标签居左、值居右，发丝线分隔。 */
function StatusRow({ label, value, accent = false }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className="flex items-baseline justify-between gap-a8 border-b border-hair px-a12 py-a8">
      <span className="font-ui text-ui text-ink">{label}</span>
      <span className={cx('font-ui text-hint', accent ? 'text-accent' : 'text-ink-muted')}>{value}</span>
    </div>
  )
}

function ProcessesPanel({ phase, tools, approvals }: { phase: RunPhase | null; tools: LiveTool[]; approvals: LiveApproval[] }) {
  const running = tools.filter((t) => t.status === 'running').length
  const failed = tools.filter((t) => t.status === 'failed').length
  return (
    <div>
      <StatusRow label="进程" value={phase === null ? '空闲' : PHASE_LABEL[phase]} accent={phase === 'running'} />
      <StatusRow
        label="工具"
        value={tools.length === 0 ? '0' : `${tools.length} 个 · ${running} 运行${failed ? ` · ${failed} 失败` : ''}`}
      />
      <StatusRow label="审批" value={approvals.length === 0 ? '0 待决' : `${approvals.length} 待决`} accent={approvals.length > 0} />
      {tools.length > 0 && (
        <div className="mt-a8 flex flex-col gap-a4 px-a4">
          {tools.map((t) => (
            <div key={t.callId} className="flex items-center gap-a8 rounded-sm px-a4 py-a4 font-ui text-caption text-ink">
              <span
                aria-hidden
                className={cx(
                  'h-[6px] w-[6px] shrink-0 rounded-full',
                  t.status === 'running' && 'bg-accent',
                  t.status === 'ok' && 'bg-ink-muted',
                  t.status === 'failed' && 'bg-danger',
                )}
              />
              <span className="min-w-0 truncate">{t.tool}</span>
            </div>
          ))}
        </div>
      )}
      {phase === null && tools.length === 0 && (
        <p className="px-a12 py-a8 font-ui text-hint text-ink-muted">没有正在运行的进程</p>
      )}
    </div>
  )
}

export function Dock({
  active,
  onSelect,
  onClose,
  phase,
  tools,
  approvals,
  onDecide,
  workspaceRoot,
  workspaceId,
}: DockProps) {
  // 面板区自己滚（列只负责裁切），滚动条仍走「滚动时现形」那套。
  const scrollRef = useAutoHideScroll<HTMLDivElement>()
  // 列表是"换面板"的入口，不是第四种面板：点条目进面板，面板头部再回列表。
  const [listing, setListing] = useState(false)
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
            onClick={() => setListing(true)}
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
              onClick={() => {
                onSelect(panel.id)
                setListing(false)
              }}
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
        {active === 'processes' && <ProcessesPanel phase={phase} tools={tools} approvals={approvals} />}
        {active === 'review' && (
          <div className="p-a12">
            {approvals.length === 0 ? (
              <p className="font-ui text-hint text-ink-muted">没有待决审批</p>
            ) : (
              <ApprovalBar approvals={approvals} busy={false} onDecide={onDecide} />
            )}
          </div>
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
