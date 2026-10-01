/**
 * 右栏 · 上下文卡：**只放上下文读数**——窗口 / 已用 / 占用率 / 缓存命中率，
 * 数据是分支用量快照（Branch.usage，落盘值）。工作区选择已移到侧栏的
 * 项目卡（ProjectCard）；模型、工具、技能不是上下文，不进这张卡。
 * null 一律显示「—」："未上报"与"确实为 0"不同，不当 0 渲染。
 */

import type { UsageReport } from '../../api/types'
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
  usage: UsageReport | null
}

export function ContextRail({ usage }: ContextRailProps) {
  return (
    <Card radius="md" title="上下文">
      <Row label="已用 tokens" value={formatInt(usage?.context.tokens)} />
      <Row label="上下文窗口" value={formatInt(usage?.context.window)} />
      <Row label="占用率" value={formatPct(usage?.context.utilization)} />
      <Row label="缓存命中率" value={formatPct(usage?.cache.hit_ratio)} />
    </Card>
  )
}
