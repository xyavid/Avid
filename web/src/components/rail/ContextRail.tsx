/**
 * 右栏（参考图「工作区 / 上下文」区）：只读信息卡。
 * 工作区卡来自选中会话；上下文卡**只放上下文读数**——窗口 / 已用 / 占用率 /
 * 缓存命中率，数据是分支用量快照（Branch.usage，落盘值）。模型、工具、技能
 * 不是上下文，不进这张卡（用户明确要求）。null 一律显示「—」：
 * "未上报"与"确实为 0"不同，不当 0 渲染。
 */

import type { Meta, SessionSummary, UsageReport } from '../../api/types'
import { Card } from '../../ui/Card'

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

export type ContextRailProps = {
  meta: Meta | null
  session: SessionSummary | null
  usage: UsageReport | null
}

export function ContextRail({ meta, session, usage }: ContextRailProps) {
  const workspace = session?.workspace
  return (
    <div className="flex flex-col gap-a16">
      <Card radius="md" title="工作区">
        <Row label="名称" value={workspace?.name ?? meta?.capabilities.workspace ?? '—'} />
        {workspace?.root && (
          <div className="mt-a4 break-all font-mono text-micro text-ink-muted">{workspace.root}</div>
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
