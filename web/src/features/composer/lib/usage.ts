/**
 * usage 展示口径的纯函数：两个域的合并规则、数字格式、占用分档与分块。
 *
 * 文案一律走 `t()`（字典），这里只出数字与符号，于是这段逻辑能脱离 React 单测。
 */

import type { ContextParts, UsageReport } from '../../../api/types'

/** 占用分档的阈值（展示约定，写进 `docs/guide/web-ui.md`）。 */
export const CONTEXT_WARN_RATIO = 0.6
export const CONTEXT_DANGER_RATIO = 0.85

/** 分档：`null`（没有窗口，算不出占用率）一律中性。 */
export type UsageTone = 'neutral' | 'warn' | 'danger'

export function usageTone(utilization: number | null): UsageTone {
  if (utilization === null || !Number.isFinite(utilization)) return 'neutral'
  if (utilization > CONTEXT_DANGER_RATIO) return 'danger'
  if (utilization > CONTEXT_WARN_RATIO) return 'warn'
  return 'neutral'
}

/**: 有占用但不足一格时露出的最小宽度（%）。 */
export const MIN_BAR_PERCENT = 2

/**
 * 进度条宽度（0–100 的整数）。没有占用率时给 0：调用方按 `null` 决定画不画。
 *
 * 有占用但四舍五入到 0 时给一个最小可见宽度：1M 窗口下真实占用长期不足 1%，
 * 条永远是空的会让人以为这个数没取到。**只放大条，不放大数字**——旁边的百分比
 * 仍然是 `0.3%`，读数不受影响。
 */
export function barPercent(utilization: number | null): number {
  if (utilization === null || !Number.isFinite(utilization)) return 0
  if (utilization <= 0) return 0
  return Math.min(100, Math.max(MIN_BAR_PERCENT, Math.round(utilization * 100)))
}

export interface UsagePart {
  key: keyof ContextParts
  /** 估算 token（内核按字符占比分配，三块之和 = 真实总数）。 */
  tokens: number
  /** 占比，供堆叠条与清单用。 */
  ratio: number
}

/**
 * 三块分块：固定顺序（系统提示词 → 工具定义 → 对话消息），无数据回空数组。
 *
 * 分母取**三块之和**而不是 `context.tokens`：两者在正常情况下相等（内核保证余数
 * 归到对话消息），而用前者能让条宽永远加起来是 100%，不会因浮点误差露缝。
 */
export function usageParts(parts: ContextParts | null | undefined): UsagePart[] {
  if (!parts) return []
  const keys: (keyof ContextParts)[] = ['system', 'tools', 'messages']
  const total = keys.reduce((sum, key) => sum + Math.max(0, parts[key]), 0)
  if (total <= 0) return []
  return keys.map((key) => ({
    key,
    tokens: Math.max(0, parts[key]),
    ratio: Math.max(0, parts[key]) / total,
  }))
}

/**
 * 实时（活动域）优先，落盘（查询域）兜底。
 *
 * 为什么这个顺序：运行中的读数是**现在**的（每轮 `run_status` 刷新），落盘那份是
 * 上一次运行结束时写的。切换会话、换分支、刷新页面时活动域被清空，于是回落到落盘值——
 * 这正是"进会话就能看到上次用了多少"要的行为。
 */
export function pickUsage(
  live: UsageReport | null,
  saved: UsageReport | null | undefined,
): UsageReport | null {
  return live ?? saved ?? null
}

/** tokens 的紧凑写法：1234 → 1.2k、120000 → 120k、1200000 → 1.2M。null 一律「—」。 */
export function formatTokens(tokens: number | null): string {
  if (tokens === null || !Number.isFinite(tokens)) return '—'
  const abs = Math.abs(tokens)
  const scaled =
    abs >= 1_000_000
      ? `${trim(abs / 1_000_000)}M`
      : abs >= 1_000
        ? `${trim(abs / 1_000)}k`
        : String(abs)
  return tokens < 0 ? `-${scaled}` : scaled
}

/** 一位小数，且整数不带 `.0`（72.0k → 72k）。 */
function trim(value: number): string {
  const fixed = value.toFixed(1)
  return fixed.endsWith('.0') ? fixed.slice(0, -2) : fixed
}

/**
 * 比值 → 百分比：0.778 → 78%、0.36 → 36%。null 一律「—」。
 *
 * 不足 1% 时保留一位小数（0.003 → 0.3%）：大窗口（1M）下真实占用长期不到 1%，
 * 四舍五入成「0%」会让人以为这个数没取到——那不是精度问题，是读错。
 */
export function formatPercent(ratio: number | null): string {
  if (ratio === null || !Number.isFinite(ratio)) return '—'
  const percent = ratio * 100
  if (percent > 0 && percent < 1) return `${percent.toFixed(1)}%`
  return `${Math.round(percent)}%`
}
