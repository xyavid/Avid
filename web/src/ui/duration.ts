/** 毫秒 → 一眼能读过的时长（思考段与工具行共用）。 */

export function durationLabel(ms: number): string {
  if (ms < 1000) return `${Math.max(1, Math.round(ms))}ms`
  if (ms < 10_000) return `${(ms / 1000).toFixed(1)}s`
  if (ms < 60_000) return `${Math.round(ms / 1000)}s`
  return `${Math.round(ms / 60_000)}m`
}
