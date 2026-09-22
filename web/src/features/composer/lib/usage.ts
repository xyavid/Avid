/**
 * usage 展示口径的纯函数：两个域的合并规则与数字格式。
 *
 * 文案一律走 `t()`（字典），这里只出数字与符号，于是这段逻辑能脱离 React 单测。
 */

import type { UsageReport } from '../../../api/types'

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

/** 比值 → 整数百分比：0.778 → 78%、0.36 → 36%。null 一律「—」。 */
export function formatPercent(ratio: number | null): string {
  if (ratio === null || !Number.isFinite(ratio)) return '—'
  return `${Math.round(ratio * 100)}%`
}
