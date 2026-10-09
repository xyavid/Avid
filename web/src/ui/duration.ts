/** Milliseconds → compact duration label (thinking block and tool row share it). */

export function durationLabel(ms: number): string {
  if (ms < 1000) return `${Math.max(1, Math.round(ms))}ms`
  if (ms < 10_000) return `${(ms / 1000).toFixed(1)}s`
  if (ms < 60_000) return `${Math.round(ms / 1000)}s`
  return `${Math.round(ms / 60_000)}m`
}

/**
 * Milliseconds → total time for the collapsed turn footer, in whole Chinese units (seconds /
 * minutes+seconds / hours+minutes); rounding happens before carrying, so 59.6s yields one minute —
 * never "60 seconds" — and whole minutes or hours carry no zero remainder.
 */
export function elapsedLabel(ms: number): string {
  const sec = Math.max(1, Math.round(ms / 1000))
  if (sec < 60) return `${sec}秒`
  const min = Math.floor(sec / 60)
  if (min < 60) return `${min}分${sec % 60 === 0 ? '' : `${sec % 60}秒`}`
  const rest = min % 60
  return `${Math.floor(min / 60)}小时${rest === 0 ? '' : `${rest}分`}`
}
