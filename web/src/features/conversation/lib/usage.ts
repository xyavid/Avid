/**
 * 用量读数文案。
 *
 * 本模块只做一件事：把 `UsageReport` 变成一行字。核心纪律是
 * **null = 没有这个数**，不是 0——`api/types.ts` 对 `UsageReport` 的注释写得很明确，
 * "端点没上报"与"确实用了 0"是两条不同的结论，界面必须能区分。
 */
import type { UsageReport } from '../../../api/types'

/** 没有这个数时统一显示的长破折号。 */
const MISSING = '—'

/**
 * 紧凑 token 数：`950` / `12.3k` / `128k`。
 *
 * 十万位以下给一位小数：读数只用来判断量级，多给一位只会让相邻两帧的文字宽度跳动。
 */
function formatTokens(value: number): string {
  if (!Number.isFinite(value)) return MISSING
  if (Math.abs(value) < 1000) return String(Math.round(value))
  const thousands = value / 1000
  if (Math.abs(thousands) >= 100) return `${Math.round(thousands)}k`
  return `${Math.round(thousands * 10) / 10}k`
}

/**
 * 用量徽标文案：`12.3k / 128k · 9%`。
 *
 * `tokens` 或 `window` 为 null 时整体显示「—」：缺任一项就算不出占用率，
 * 用 0 填补会造出一个"看起来正常"的读数，比不显示更危险（报告 §3.2）。
 * `utilization` 是可选的派生字段，缺了就地从两个已有的数算，不必因此丢掉整个读数。
 *
 * 百分比用 floor 而不是四舍五入：这是"还剩多少余量"的读数，
 * 99.6% 报成 100% 会让人以为已经满了，宁可少报一个点（保守方向）。
 */
export function formatUsage(usage: UsageReport | null): string {
  if (usage === null) return MISSING
  const context = usage.context
  if (!context) return MISSING
  const { tokens, window, utilization } = context
  if (tokens === null || window === null || window <= 0) return MISSING
  const ratio = utilization ?? tokens / window
  return `${formatTokens(tokens)} / ${formatTokens(window)} · ${Math.floor(ratio * 100)}%`
}

/**
 * 缓存命中率文案：`命中 62%`；没有写入计数时返回 null（该项不出现）。
 *
 * 为什么要求 `write_tokens` 也非 null：只有 Anthropic 风格的 provider 才上报缓存写，
 * 缺它意味着这家根本没有缓存计数。此时显示"命中 0%"是把"没有数据"说成了一条结论——
 * 0% 是"确认未命中"，两者必须分开。
 */
export function formatCache(usage: UsageReport | null): string | null {
  if (usage === null) return null
  const cache = usage.cache
  if (!cache) return null
  const { hit_ratio, write_tokens } = cache
  if (hit_ratio === null || write_tokens === null) return null
  // 命中率是统计描述而非阈值，四舍五入最接近事实。
  return `命中 ${Math.round(hit_ratio * 100)}%`
}
