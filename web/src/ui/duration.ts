/** 毫秒 → 一眼能读过的时长（思考段与工具行共用）。 */

export function durationLabel(ms: number): string {
  if (ms < 1000) return `${Math.max(1, Math.round(ms))}ms`
  if (ms < 10_000) return `${(ms / 1000).toFixed(1)}s`
  if (ms < 60_000) return `${Math.round(ms / 1000)}s`
  return `${Math.round(ms / 60_000)}m`
}

/**
 * 毫秒 → 收尾折叠行的「用时」（参考界面的中文读数：13分11秒 / 45秒 / 1小时2分）。
 *
 * 与 `durationLabel` 分家是因为用途不同：那个是过程里的紧凑读数（3.2s），
 * 这个是**一条回话的总账**，要能一眼说出「这轮跑了多久」，所以先四舍五入到秒
 * 再进位（59.6 秒是 1 分，不是「60秒」），整分 / 整小时不带零头。
 */
export function elapsedLabel(ms: number): string {
  const sec = Math.max(1, Math.round(ms / 1000))
  if (sec < 60) return `${sec}秒`
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min}分${sec % 60 === 0 ? '' : `${sec % 60}秒`}`
  const rest = min % 60
  return `${Math.floor(min / 60)}小时${rest === 0 ? '' : `${rest}分`}`
}
