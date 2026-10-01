/**
 * 相对时间文案。
 *
 * 「现在」必须由调用方传入：组件里读 `Date.now()` 会让这段逻辑不可测，
 * 也会让同一份数据在两次渲染间给出不同结果（React 严格模式下尤其明显）。
 */

const MINUTE_MS = 60_000
const HOUR_MS = 3_600_000
const DAY_MS = 86_400_000

/**
 * 传入毫秒时间戳与"现在"，返回「刚刚 / N 分钟前 / N 小时前 / 昨天 / N 天前」。
 *
 * 分档按**时长**而不是日历天：日历天要引入时区与本地午夜边界，跨时区测试会飘，
 * 而这里的读者只关心"多久以前"。代价是 25 小时前在日历上可能是"前天"，
 * 但对一个 agent 会话列表来说，这个精度差异不影响判断。
 * 未来时间（客户端时钟比服务端快）一律当"刚刚"，不渲染"还有 N 分钟"这种需要假设的话。
 */
export function relativeTime(ts: number, now: number): string {
  const delta = now - ts
  if (!Number.isFinite(delta) || delta < MINUTE_MS) return '刚刚'
  if (delta < HOUR_MS) return `${Math.floor(delta / MINUTE_MS)} 分钟前`
  if (delta < DAY_MS) return `${Math.floor(delta / HOUR_MS)} 小时前`
  if (delta < 2 * DAY_MS) return '昨天'
  return `${Math.floor(delta / DAY_MS)} 天前`
}
