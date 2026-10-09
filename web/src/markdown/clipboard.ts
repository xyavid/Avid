/**
 * Clipboard write. `navigator.clipboard` exists only in a secure context and may be missing
 * or denied, so the result is a boolean and never a throw — a failed copy must not break the
 * message render.
 */

export async function copyText(text: string): Promise<boolean> {
  try {
    if (typeof navigator === 'undefined' || navigator.clipboard === undefined) return false
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    return false
  }
}
