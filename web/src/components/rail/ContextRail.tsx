/**
 * 右栏（参考图「工作区 / 上下文」区）：只读信息卡。
 * 工作区卡（阶段 4 补交互）：列出注册过的工作区候选供**选择**——选中的是
 * 「新会话将使用的工作区」（会话的归属在创建时绑定，现有会话只读，其归属项
 * 打「当前会话」标记）；标题行「+」新增：picker 可用时走宿主机文件夹选择器，
 * 不可用时出手动路径输入行。上下文卡**只放上下文读数**——窗口 / 已用 / 占用率 /
 * 缓存命中率（分支用量快照）。null 一律显示「—」："未上报"与"确实为 0"不同。
 */

import { useState } from 'react'

import type { Meta, SessionSummary, UsageReport, WorkspaceSummary } from '../../api/types'
import { Card } from '../../ui/Card'
import { cx } from '../../ui/cx'
import { Icon } from '../../ui/Icon'
import { Input } from '../../ui/Input'

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-a8">
      <span className="font-ui text-hint text-ink-muted">{label}</span>
      <span className="truncate font-ui text-ui text-ink">{value}</span>
    </div>
  )
}

/** token 数千分位；null → 「—」。 */
function formatInt(value: number | null | undefined): string {
  return value === null || value === undefined ? '—' : value.toLocaleString('en-US')
}

/** 比率（0–1）→ 百分号一位小数；null → 「—」。 */
function formatPct(ratio: number | null | undefined): string {
  return ratio === null || ratio === undefined ? '—' : `${(ratio * 100).toFixed(1)}%`
}

function basename(root: string): string {
  const parts = root.split(/[\\/]/).filter(Boolean)
  return parts[parts.length - 1] ?? root
}

export type ContextRailProps = {
  meta: Meta | null
  session: SessionSummary | null
  usage: UsageReport | null
  /** 注册过的工作区候选；缺省/null = 还在加载（回落到会话归属展示）。 */
  workspaces?: WorkspaceSummary[] | null
  activeWorkspaceId?: string | null
  /** 选中会话归属的工作区 id（打「当前会话」标记，只读）。 */
  sessionWorkspaceId?: string | null
  /** 宿主机文件夹选择器是否可用（meta.capabilities.workspace_picker）。 */
  pickerAvailable?: boolean
  busy?: boolean
  hint?: string | null
  onSelectWorkspace?: (id: string) => void
  onAddByPicker?: () => void
  onAddByPath?: (path: string) => void
}

export function ContextRail({
  meta,
  session,
  usage,
  workspaces = null,
  activeWorkspaceId = null,
  sessionWorkspaceId = null,
  pickerAvailable = false,
  busy = false,
  hint = null,
  onSelectWorkspace,
  onAddByPicker,
  onAddByPath,
}: ContextRailProps) {
  const workspace = session?.workspace
  const [manualOpen, setManualOpen] = useState(false)
  const [manualPath, setManualPath] = useState('')

  const submitManual = () => {
    const path = manualPath.trim()
    if (!path || busy) return
    onAddByPath?.(path)
    setManualPath('')
  }

  return (
    <div className="flex flex-col gap-a16">
      <Card
        radius="md"
        title="工作区"
        actions={
          pickerAvailable ? (
            <button
              type="button"
              onClick={onAddByPicker}
              disabled={busy}
              title="打开文件夹选择器新增工作区"
              className="inline-flex h-[26px] items-center gap-[5px] rounded-sm border-hairline border-hair px-a8 font-ui text-hint font-medium text-ink-light transition-colors duration-fast ease-out hover:bg-overlay-light hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
            >
              <Icon name="plus" size={12} />
              新增
            </button>
          ) : null
        }
      >
        {workspaces === null ? (
          <>
            <Row label="名称" value={workspace?.name ?? meta?.capabilities.workspace ?? '—'} />
            {workspace?.root && (
              <div className="mt-a4 break-all font-mono text-micro text-ink-muted">{workspace.root}</div>
            )}
          </>
        ) : (
          <>
            <div className="flex flex-col gap-a4">
              {workspaces.length === 0 && (
                <p className="font-ui text-hint text-ink-muted">还没有注册工作区</p>
              )}
              {workspaces.map((ws) => {
                const active = ws.id === activeWorkspaceId
                return (
                  <button
                    key={ws.id}
                    type="button"
                    onClick={() => onSelectWorkspace?.(ws.id)}
                    className={cx(
                      'w-full rounded-sm px-a8 py-a6 text-left transition-colors duration-fast ease-out',
                      active ? 'bg-accent-light' : 'hover:bg-overlay-light',
                    )}
                  >
                    <div className="flex items-center justify-between gap-a8">
                      <span
                        className={cx('truncate font-ui text-ui', active ? 'font-medium text-accent' : 'text-ink')}
                      >
                        {ws.name ?? basename(ws.root)}
                      </span>
                      <span className="flex shrink-0 items-center gap-a4">
                        {ws.id === sessionWorkspaceId && (
                          <span className="font-ui text-micro text-ink-muted">当前会话</span>
                        )}
                        {active && (
                          <span className="text-accent">
                            <Icon name="check" size={14} />
                          </span>
                        )}
                      </span>
                    </div>
                    <div className="mt-[1px] truncate font-mono text-micro text-ink-muted">{ws.root}</div>
                  </button>
                )
              })}
            </div>
            <p className="mt-a8 font-ui text-micro text-ink-muted">新会话将使用所选工作区；现有会话的归属不变。</p>

            {manualOpen || !pickerAvailable ? (
              <div className="mt-a8 flex items-center gap-a8">
                <Input
                  bare
                  value={manualPath}
                  onChange={(e) => setManualPath(e.target.value)}
                  placeholder="/绝对/路径"
                  aria-label="工作区路径"
                  disabled={busy}
                  className="h-[30px] rounded-sm bg-overlay-light px-a8"
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') submitManual()
                  }}
                />
                <button
                  type="button"
                  onClick={submitManual}
                  disabled={busy || manualPath.trim() === ''}
                  className="shrink-0 rounded-sm border-hairline border-hair px-a8 py-a4 font-ui text-hint font-medium text-ink-light transition-colors duration-fast ease-out hover:bg-overlay-light hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
                >
                  确认
                </button>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => setManualOpen(true)}
                className="mt-a8 font-ui text-micro text-ink-muted transition-colors duration-fast ease-out hover:text-ink-light"
              >
                手动输入路径
              </button>
            )}

            {busy && <p className="mt-a8 font-ui text-hint text-ink-muted">正在与后端确认…</p>}
            {hint && <p className="mt-a8 font-ui text-hint text-ink-light">{hint}</p>}
          </>
        )}
      </Card>
      <Card radius="md" title="上下文">
        <Row label="已用 tokens" value={formatInt(usage?.context.tokens)} />
        <Row label="上下文窗口" value={formatInt(usage?.context.window)} />
        <Row label="占用率" value={formatPct(usage?.context.utilization)} />
        <Row label="缓存命中率" value={formatPct(usage?.cache.hit_ratio)} />
      </Card>
    </div>
  )
}
