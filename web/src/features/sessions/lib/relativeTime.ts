/**
 * 会话列表元信息用的相对时间。
 *
 * **必须传 `now`**，函数内不读时钟：分档边界（"刚刚"与"昨天"）对运行时偏差非常
 * 敏感，读时钟就没法把边界钉在测试里。组件那边用挂载时刻传进来。
 *
 * 为什么超过一个月改给绝对日期：天数长到几十上百以后，"238 天前"并不比
 * "2024-03-05" 更有信息量，反而更难定位。
 */

const MINUTE = 60_000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

/** 「刚刚 / N 分钟前 / N 小时前 / 昨天 / N 天前 / YYYY-MM-DD」；未来时间返回「刚刚」。 */
export function relativeTime(ts: number, now: number): string {
  const delta = now - ts
  // 负值来自时钟回拨或服务端与浏览器时间不一致；显示"负 3 分钟前"是纯噪声，按刚刚处理
  if (delta < MINUTE) return '刚刚'
  if (delta < HOUR) return `${Math.floor(delta / MINUTE)} 分钟前`
  if (delta < DAY) return `${Math.floor(delta / HOUR)} 小时前`
  if (delta < 2 * DAY) return '昨天'
  if (delta < 30 * DAY) return `${Math.floor(delta / DAY)} 天前`

  const d = new Date(ts)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}
