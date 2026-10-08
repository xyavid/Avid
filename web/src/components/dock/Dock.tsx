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

import { lazy, Suspense, useEffect } from 'react'

import type { LiveApproval, LiveTool, RunPhase } from '../../state/useRunStream'
import type { UsageReport } from '../../api/types'
import type { DockPanelId } from '../../state/dock'
import { IconButton } from '../../ui/IconButton'
import { Icon, type IconName } from '../../ui/Icon'
import { cx } from '../../ui/cx'
import { useAutoHideScroll } from '../../ui/useAutoHideScroll'
import { ContextRail } from '../rail/ContextRail'
import { ApprovalBar } from '../chat/ApprovalBar'

// 重面板按需加载：xterm 的体积隔离进异步 chunk（体积门禁分档计量）
const TerminalPanel = lazy(() => import('./TerminalPanel'))

const PANELS: { id: DockPanelId; label: string; icon: IconName }[] = [
  { id: 'context', label: '上下文', icon: 'file-text' },
  { id: 'processes', label: '进程', icon: 'activity' },
  { id: 'review', label: '审查', icon: 'shield-check' },
  { id: 'terminal', label: '终端', icon: 'terminal' },
]

export type DockProps = {
  active: DockPanelId
  onSelect: (id: DockPanelId) => void
  onClose: () => void
  /** 面板数据：会话的用量快照与活运行状态（无活运行时 phase 为 null）。 */
  usage: UsageReport | null
  phase: RunPhase | null
  tools: LiveTool[]
  approvals: LiveApproval[]
  onDecide: (approvalId: string, decision: 'allow' | 'deny') => void
  /** 终端面板的工作目录：选中会话的工作区根。 */
  workspaceRoot: string | null
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

export function Dock({ active, onSelect, onClose, usage, phase, tools, approvals, onDecide, workspaceRoot }: DockProps) {
  // 面板区自己滚（列只负责裁切），滚动条仍走「滚动时现形」那套。
  const scrollRef = useAutoHideScroll<HTMLDivElement>()
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
      <div className="flex items-center justify-between border-b border-hair pr-a4">
        <div className="flex" role="tablist" aria-label="侧边栏面板">
          {PANELS.map((p) => (
            <button
              key={p.id}
              type="button"
              role="tab"
              aria-selected={active === p.id}
              title={p.label}
              onClick={() => onSelect(p.id)}
              className={cx(
                'border-b-2 px-a12 py-a8 transition-colors duration-fast ease-out',
                active === p.id ? 'border-accent text-ink' : 'border-transparent text-ink-muted hover:text-ink',
              )}
            >
              <Icon name={p.icon} size={14} />
            </button>
          ))}
        </div>
        <IconButton icon="x" label="关闭侧边栏" onClick={onClose} />
      </div>

      <div ref={scrollRef} className="scroll-auto min-h-0 flex-1 overflow-y-auto">
        {active === 'context' && (
          <div className="p-a12">
            <ContextRail usage={usage} />
          </div>
        )}
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
    </aside>
  )
}
