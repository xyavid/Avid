/**
 * 相对时间：导航树里每行会话右侧显示"多久以前"（图片里的 16分钟 / 2小时）。
 *
 * 拆成"算出来的形状"与"怎么念"两步：这里只算形状（纯函数，可单测），
 * 文案交给 i18n（`sessions.time.*`）。这样换语言、改口径都不用碰时间数学。
 */

export type RelativeTime =
  | { unit: 'now' }
  | { unit: 'minute'; value: number }
  | { unit: 'hour'; value: number }
  | { unit: 'day'; value: number }
  | { unit: 'date'; text: string }

const MINUTE = 60_000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

/**
 * 口径（都与常见的一眼可读口径对齐）：
 *   <1 分钟 → 刚刚；<1 小时 → N 分钟；<24 小时 → N 小时；<7 天 → N 天；再往前给日期。
 * 未来时间（时钟回拨、服务端与本机有时差）一律按"刚刚"处理，不显示负数。
 */
export function formatRelative(
  timestampMs: number,
  nowMs: number,
  locale: string,
): RelativeTime {
  const elapsed = nowMs - timestampMs
  if (!Number.isFinite(elapsed) || elapsed < MINUTE) return { unit: 'now' }
  if (elapsed < HOUR) return { unit: 'minute', value: Math.floor(elapsed / MINUTE) }
  if (elapsed < DAY) return { unit: 'hour', value: Math.floor(elapsed / HOUR) }
  if (elapsed < 7 * DAY) return { unit: 'day', value: Math.floor(elapsed / DAY) }
  return {
    unit: 'date',
    text: new Date(timestampMs).toLocaleDateString(locale, {
      month: 'numeric',
      day: 'numeric',
    }),
  }
}
