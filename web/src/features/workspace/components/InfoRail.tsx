/**
 * 右栏信息面板：把"这次对话在哪个目录、用的什么模型、花了多少"摆在一处。
 *
 * 参考截图里这张栏有两块（工作区卡 / 本次对话卡），但截图里那些**我们没有的字段**
 * （文件列表、授权目录数）一律不摆——宁可少一行，也不给一个看起来像真数据的空位。
 *
 * 宽度与高度由外层布局给（`h-full w-full`），本组件不做固定宽度：同一条右栏
 * 在宽屏是常驻列、在窄屏是浮层，宽度是布局的决定，不是面板的决定。
 *
 * 数值口径（本仓一贯）：**null ≠ 0**。缺失显示「—」，工作目录缺失显示「未归属」
 * （它比「—」更具体：说的是"这条会话没记归属"，而不是"我们没查到"）。
 * 0 是值，照实显示——见 `lib/labels.ts` 的 `orDash`。
 */

import type { ReactElement } from 'react'

import type { SessionSummary, WorkspaceSummary } from '../../../api/types'
import { Card, Badge, Button } from '../../../ui/primitives'
import { orDash, permissionLabel, workspaceLabel } from '../lib/labels'

export interface InfoRailProps {
  /** 当前会话；null = 还没选中（此时只显示"本次对话"的空态）。 */
  session: SessionSummary | null
  branch: string
  model: string | null
  /** 已登记的工作区，用于显示"工作目录"与数量。 */
  workspaces: WorkspaceSummary[]
  /** 当前会话所属工作区；可能为 null（会话 header 里没有归属）。 */
  workspaceRoot: string | null
  /** 本次运行的会话累计 tokens 与条数。 */
  tokens: number
  messageCount: number
  onManageWorkspaces: () => void
}

/** 面板里最多列几条工作区。再多就给"还有 N 个"，把高度让给对话本身。 */
const MAX_ROWS = 4

/** 一行"标签 — 值"。标签与值同一个节点，便于按行断言，也便于读屏按顺序念。 */
function RailRow({ label, value }: { label: string; value: string }): ReactElement {
  return (
    <div className="flex items-baseline justify-between gap-a8">
      <span className="shrink-0 text-hint text-ink-muted">{label}</span>
      {/* `tabular-nums` 给整列读数：条数与 tokens 上下对齐，数值刷新时不会左右抖
          （tokens.css 只对 code/pre 开了等宽数字，这里是普通 span，得自己带）。 */}
      <span className="min-w-0 truncate text-caption text-ink tabular-nums" title={value}>
        {value}
      </span>
    </div>
  )
}

export function InfoRail({
  session,
  branch,
  model,
  workspaces,
  workspaceRoot,
  tokens,
  messageCount,
  onManageWorkspaces,
}: InfoRailProps): ReactElement {
  const bound = workspaces.find((item) => item.is_default) ?? null
  // 只列前 MAX_ROWS 条；绑定进程的那个已由服务端排在最前（`svc/workspaces.py` 的
  // `workspaces()`），所以不需要在渲染层再为它破例。
  const visible = workspaces.slice(0, MAX_ROWS)
  const rest = workspaces.length - visible.length

  return (
    <div className="flex h-full w-full min-h-0 flex-col gap-a8 overflow-y-auto p-a12">
      {/* ---------------- 工作区卡 ---------------- */}
      <Card as="section" aria-label="工作区" className="p-a10">
        <div className="flex items-center justify-between gap-a8">
          <h2 className="text-caption font-medium text-ink-muted">工作区</h2>
          <Button size="sm" variant="ghost" aria-label="管理工作区" onClick={onManageWorkspaces}>
            管理
          </Button>
        </div>

        {workspaces.length === 0 ? (
          <p className="mt-a8 text-hint text-ink-muted">还没有登记任何工作区</p>
        ) : (
          <>
            <ul className="mt-a8 flex flex-col gap-a4">
              {visible.map((workspace) => {
                const permission = permissionLabel(workspace.default_permission)
                return (
                  <li key={workspace.id} className="flex items-center gap-a6">
                    <span className="min-w-0 flex-1 truncate text-caption text-ink">
                      {workspaceLabel(workspace)}
                    </span>
                    {workspace.is_default ? <Badge tone="info">进程绑定</Badge> : null}
                    {permission === null ? null : <Badge tone="neutral">{permission}</Badge>}
                  </li>
                )
              })}
            </ul>
            {rest > 0 ? (
              <p className="mt-a4 text-hint text-ink-faint">{`还有 ${rest} 个`}</p>
            ) : null}
          </>
        )}

        {/* 底部一行说清"这个进程现在站在哪个目录上"。它未必在候选列表里
            （单工作区模式下进程绑定的那个不一定是注册表条目），所以单独给一行。 */}
        <div className="avid-hair-t mt-a8 flex items-baseline justify-between gap-a8 pt-a6">
          <span className="shrink-0 text-hint text-ink-muted">进程绑定</span>
          {bound === null ? (
            <span className="min-w-0 truncate text-hint text-ink-faint">—</span>
          ) : (
            <span className="min-w-0 truncate text-hint text-ink-faint" title={bound.root}>
              {bound.root}
            </span>
          )}
        </div>
      </Card>

      {/* ---------------- 本次对话卡 ---------------- */}
      <Card as="section" aria-label="本次对话" className="p-a10">
        <h2 className="text-caption font-medium text-ink-muted">本次对话</h2>
        {session === null ? (
          <p className="mt-a8 text-hint text-ink-muted">还没有选中会话</p>
        ) : (
          <div className="mt-a8 flex flex-col gap-a6">
            <RailRow
              label="工作目录"
              /* 缺失说「未归属」而不是「—」：它比"没查到"更具体——这条会话的
                 header 里本来就没记归属（`WorkspaceRef` 可空）。 */
              value={workspaceRoot === null || workspaceRoot.trim() === '' ? '未归属' : workspaceRoot}
            />
            <RailRow label="分支" value={orDash(branch)} />
            <RailRow label="模型" value={orDash(model)} />
            <RailRow label="条数" value={orDash(messageCount)} />
            <RailRow label="累计 tokens" value={orDash(tokens)} />
          </div>
        )}
      </Card>
    </div>
  )
}
