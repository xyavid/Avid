/**
 * 输入区角落的紧凑用量读数。
 *
 * 口径单点在服务端 `RunState.usage_report()`：**null 表示"没有这个数"**（端点不上报用量 /
 * 没问到模型窗口 / 这一轮没记分块字符数），与"确实是 0"是两件事。所以这里所有格式化都先判
 * null 再判值——缺数返回「—」，绝不回落成 0（用量读数上的一个假 0 会直接误导用户对自己
 * 上下文预算的判断）。
 */

import type { UsageReport } from '../../../api/types'

/** 百分比：utilization 是 0–1 的比例（`prompt / window`），不是已经乘过 100 的数。 */
function formatRatio(ratio: number): string {
  return `${Math.round(ratio * 100)}%`
}

/** 12_300 → `12.3k`；128_000 → `128k`；不足一千按整数原样。 */
function formatTokens(tokens: number): string {
  if (!Number.isFinite(tokens)) return '—'
  if (tokens < 1000) return String(Math.round(tokens))
  const scaled = tokens < 1_000_000 ? tokens / 1000 : tokens / 1_000_000
  const unit = tokens < 1_000_000 ? 'k' : 'M'
  const rounded = Math.round(scaled * 10) / 10
  return `${Number.isInteger(rounded) ? rounded : rounded.toFixed(1)}${unit}`
}

/** 输入区角落的紧凑读数：`12.3k tok · 9%`；缺数显示「—」，**不得当 0**。 */
export function compactUsage(usage: UsageReport | null): string {
  const tokens = usage?.context?.tokens ?? null
  const utilization = usage?.context?.utilization ?? null
  if (tokens === null && utilization === null) return '—'

  // 两个数各自独立缺失：有的模型只报 tokens 不给窗口（就是没有百分比可算），
  // 这时只显示 tokens，而不是补一个假的分母。
  const parts: string[] = []
  if (tokens !== null) parts.push(`${formatTokens(tokens)} tok`)
  if (utilization !== null) parts.push(formatRatio(utilization))
  return parts.join(' · ')
}

/**
 * 分块堆叠条的三段占比；parts 为 null 时返回 null（不画条）。
 *
 * 三块之和由内核保证等于真实总数，但那是"三块之和"，不是"三块之和 > 0"：
 * 全 0 的分块（旧快照 / 这一轮没记字符数）画出来是一条空条，与"没有数据"看起来一样，
 * 所以一并归到"不画条"。
 */
export function contextSegments(
  usage: UsageReport | null,
): Array<{ key: 'system' | 'tools' | 'messages'; ratio: number }> | null {
  const parts = usage?.context?.parts ?? null
  if (parts === null) return null

  const total = parts.system + parts.tools + parts.messages
  if (!(total > 0)) return null

  return [
    { key: 'system', ratio: parts.system / total },
    { key: 'tools', ratio: parts.tools / total },
    { key: 'messages', ratio: parts.messages / total },
  ]
}
